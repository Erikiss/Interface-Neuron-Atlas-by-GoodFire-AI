"""Search over the atlas.

Two layers of text are searchable:

* **labels** - what the global index files say about every pole: mode labels,
  island summaries, Overview headlines and the circuit explorer's automatic
  label.  Always available; the index is rebuilt from the catalog in well under
  a second.
* **deep** / **examples** - the long texts inside per-pole files (mode and
  subcircuit readings, island descriptions, explanations, reports, example
  passages).  They become searchable for every pole that has been mirrored.

The texts live in SQLite FTS5 tables (a word index, plus a trigram index for
substring search over the labels).  Structured filters, sorting, grouping and
paging are done in Python over the in-memory catalog, which is tiny.
"""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .catalog import CATEGORIES, Catalog, PoleEntry
from .ids import parse_reference
from .models import (
    Match,
    ModeHit,
    ModeOut,
    NeuronHit,
    PoleHit,
    SearchQuery,
    SearchResponse,
)

SCHEMA_VERSION = 3

SCOPE_LABELS, SCOPE_DEEP, SCOPE_EXAMPLES = 0, 1, 2
_SCOPE_BY_NAME = {"labels": SCOPE_LABELS, "deep": SCOPE_DEEP, "examples": SCOPE_EXAMPLES}

#: How much a hit in each kind of text counts.
KIND_WEIGHT = {
    "mode": 1.0,
    "description": 1.0,
    "headline": 1.1,
    "island_summary": 0.8,
    "circuit_label": 0.5,
    # deep
    "mode_description": 0.8,
    "summary": 0.7,
    "subcircuit": 0.6,
    "island": 0.6,
    "overview": 0.7,
    "report": 0.4,
    "circuit": 0.3,
    # examples
    "example": 0.3,
}

_MAX_ROWS_PER_TERM = 50_000
_MAX_REGEX_LENGTH = 300


@dataclass(frozen=True)
class TextField:
    """One searchable text of a pole."""

    kind: str
    text: str
    mode_index: int | None = None
    ref: str | None = None
    scope: int = SCOPE_DEEP


# --------------------------------------------------------------------------- query parsing

_INLINE = re.compile(
    r"(?<!\S)(?P<key>layer|l|expert|e|neuron|n|pole|cat|category|verdict|modes|sim|simulatability|"
    r"damage|has|lacks|sample|in|sort|group)\s*:\s*(?P<val>\"[^\"]*\"|\S+)",
    re.IGNORECASE,
)
_NUM = r"-?\d+(?:\.\d+)?"
_RANGE = re.compile(rf"^({_NUM})\.\.({_NUM})$")
_CMP = re.compile(rf"^(>=|<=|>|<|=)?({_NUM})$")
_ID_PART = re.compile(r"^(?:L(?P<L>\d{1,3}))?[\s_/]*(?:E(?P<E>\d{1,4}))?[\s_/]*(?:N(?P<N>\d{1,4}))?$", re.IGNORECASE)
_TOKEN = re.compile(r'(-?)"([^"]*)"|(\S+)')
_WORDCHAR = re.compile(r"\w", re.UNICODE)

_CAT_ALIASES = {
    "mono": "mono", "single": "mono", "monosemantic": "mono",
    "fn": "fn", "dominant": "fn",
    "poly": "poly", "multiple": "poly", "polysemantic": "poly", "multi": "poly",
    "dark": "dark", "uninterpretable": "dark",
    "none": "none", "unread": "none",
}  # fmt: skip


@dataclass
class Term:
    text: str
    phrase: bool = False
    prefix: bool = False
    negate: bool = False


@dataclass
class ParsedQuery:
    """A :class:`SearchQuery` with inline filters folded in and the text split into clauses."""

    query: SearchQuery
    #: AND of groups; each group is an OR of terms.
    groups: list[list[Term]] = field(default_factory=list)
    negatives: list[Term] = field(default_factory=list)
    direct: list[str] = field(default_factory=list)  # pole ids named by the query text
    text: str = ""
    notes: list[str] = field(default_factory=list)


def _bounds(value: str) -> tuple[float | None, float | None]:
    """``5`` -> (5, 5); ``>=5`` -> (5, None); ``<0.3`` -> (None, 0.3-eps); ``9..36`` -> (9, 36)."""
    m = _RANGE.match(value)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = _CMP.match(value)
    if not m:
        raise ValueError(value)
    op, num = m.group(1) or "=", float(m.group(2))
    eps = 1e-9
    return {
        "=": (num, num),
        ">=": (num, None),
        ">": (num + eps, None),
        "<=": (None, num),
        "<": (None, num - eps),
    }[op]


def _int_list(value: str) -> list[int]:
    out: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        m = _RANGE.match(part)
        if m:
            out.extend(range(int(float(m.group(1))), int(float(m.group(2))) + 1))
        else:
            out.append(int(part))
    return out


def parse_query(query: SearchQuery) -> ParsedQuery:
    """Resolve inline ``key:value`` filters and split the remaining text into clauses."""
    q = query.model_copy(deep=True)
    parsed = ParsedQuery(q)
    text = q.q or ""

    def take(m: re.Match[str]) -> str:
        key, val = m.group("key").lower(), m.group("val").strip('"')
        try:
            if key in ("layer", "l"):
                lo, hi = _bounds(val) if not re.search(r",", val) else (None, None)
                if "," in val or _RANGE.match(val) or _CMP.match(val) is None:
                    q.layers = sorted(set((q.layers or []) + _int_list(val)))
                elif lo == hi and lo is not None:
                    q.layers = sorted(set((q.layers or []) + [int(lo)]))
                else:
                    q.layer_min = int(math.ceil(lo)) if lo is not None else q.layer_min
                    q.layer_max = int(math.floor(hi)) if hi is not None else q.layer_max
            elif key in ("expert", "e"):
                q.experts = sorted(set((q.experts or []) + _int_list(val)))
            elif key in ("neuron", "n"):
                q.neurons = sorted(set((q.neurons or []) + _int_list(val)))
            elif key == "pole":
                from .ids import normalise_pole

                q.pole = normalise_pole(val)  # type: ignore[assignment]
            elif key in ("cat", "category"):
                cats = [_CAT_ALIASES[c.strip().lower()] for c in val.split(",") if c.strip()]
                q.categories = sorted(set((q.categories or []) + cats))  # type: ignore[arg-type]
            elif key == "verdict":
                v = val.lower()
                q.verdict = "monosemantic" if v.startswith("mono") else "polysemantic"
            elif key == "modes":
                lo, hi = _bounds(val)
                q.modes_min = int(math.ceil(lo)) if lo is not None else q.modes_min
                q.modes_max = int(math.floor(hi)) if hi is not None else q.modes_max
            elif key in ("sim", "simulatability"):
                lo, hi = _bounds(val)
                q.simulatability_min = lo if lo is not None else q.simulatability_min
                q.simulatability_max = hi if hi is not None else q.simulatability_max
            elif key == "damage":
                lo, hi = _bounds(val)
                q.damage_min = lo if lo is not None else q.damage_min
                q.damage_max = hi if hi is not None else q.damage_max
            elif key == "has":
                q.has = sorted(set((q.has or []) + [v.strip().lower() for v in val.split(",") if v.strip()]))  # type: ignore[arg-type]
            elif key == "lacks":
                q.lacks = sorted(set((q.lacks or []) + [v.strip().lower() for v in val.split(",") if v.strip()]))  # type: ignore[arg-type]
            elif key == "sample":
                q.sample = val.lower()  # type: ignore[assignment]
            elif key == "in":
                q.scope = [v.strip().lower() for v in val.split(",") if v.strip()]  # type: ignore[assignment]
            elif key == "sort":
                q.sort = {"sim": "simulatability"}.get(val.lower(), val.lower())  # type: ignore[assignment]
            elif key == "group":
                q.group_by = val.lower()  # type: ignore[assignment]
        except (ValueError, KeyError):
            parsed.notes.append(f"ignored filter {m.group(0)!r}: could not read the value")
            return " "
        return " "

    text = _INLINE.sub(take, text)
    # Re-validate what the inline filters wrote (rejects e.g. has:nonsense with a clear error).
    try:
        parsed.query = q = SearchQuery.model_validate(q.model_dump())
    except Exception as exc:  # pydantic.ValidationError
        raise ValueError(f"invalid inline filter: {exc}") from exc
    text = " ".join(text.split())
    parsed.text = text
    if not text:
        return parsed

    if q.match == "regex":
        parsed.groups = [[Term(text)]]
        return parsed
    if q.match == "semantic":
        return parsed

    # The whole text is an id -> open it directly.
    ref = parse_reference(text)
    if ref:
        nid, pole = ref
        parsed.direct = [nid.pid(pole)] if pole else [nid.pid("pos"), nid.pid("neg")]
        return parsed

    group: list[Term] = []
    pending_or = False
    for m in _TOKEN.finditer(text):
        if m.group(3) is not None:
            raw = m.group(3)
            if raw.upper() == "OR":
                pending_or = bool(group) or bool(parsed.groups)
                continue
            if raw.upper() == "AND":
                continue
            negate = raw.startswith("-") and len(raw) > 1
            if raw.upper() == "NOT":
                # "NOT x" negates the next token; emulate by flagging through a marker term.
                group.append(Term("\0NOT"))
                continue
            word = raw[1:] if negate else raw
            prefix = word.endswith("*") and len(word) > 1
            term = Term(word.rstrip("*") if prefix else word, prefix=prefix, negate=negate)
        else:
            term = Term(m.group(2), phrase=True, negate=bool(m.group(1)))
        if not term.text.strip():
            continue
        if group and group[-1].text == "\0NOT":
            group.pop()
            term.negate = True
        if term.negate:
            parsed.negatives.append(term)
            continue
        if pending_or and parsed.groups and not group:
            parsed.groups[-1].append(term)
        elif pending_or and group:
            group.append(term)
        else:
            if group:
                parsed.groups.append(group)
            group = [term]
        pending_or = False
    if group and group[-1].text != "\0NOT":
        parsed.groups.append(group)
    return parsed


# --------------------------------------------------------------------------- the index


def _fts_literal(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


class SearchIndex:
    """FTS index over pole texts plus the query engine on top of the catalog."""

    def __init__(
        self,
        catalog: Catalog,
        path: Path | str | None = None,
        *,
        mirrored: Callable[[Iterable[str]], dict[str, list[str]]] | None = None,
        semantic: Any = None,
        allow_regex: bool = True,
    ):
        self.catalog = catalog
        self._mirrored = mirrored
        self.semantic = semantic
        self.allow_regex = allow_regex
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._closed = False
        if path is None:
            self._uri = f"file:atlas-search-{id(self)}?mode=memory&cache=shared"
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._uri = Path(path).resolve().as_uri()
        self._keepalive = self._connect()  # keeps a shared in-memory database alive
        self._ensure_schema()
        self.build_labels()

    # ------------------------------------------------------------------ storage

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._uri, uri=True, check_same_thread=False, timeout=30)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    @property
    def _con(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = self._local.con = self._connect()
        return con

    def close(self) -> None:
        # Writers run on background threads (mirror jobs); wait for the one in flight,
        # then refuse new ones - closing a connection under a running statement crashes SQLite.
        with self._write_lock:
            self._closed = True
            self._keepalive.close()

    def _ensure_schema(self) -> None:
        with self._write_lock:
            con = self._keepalive
            con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
            row = con.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
            if row is None or int(row[0]) != SCHEMA_VERSION:
                for name in ("labels", "labels_fts", "labels_tri", "deep", "deep_fts", "deep_sources"):
                    con.execute(f"DROP TABLE IF EXISTS {name}")
                con.execute("DELETE FROM meta")
                con.execute("INSERT INTO meta VALUES('schema', ?)", (str(SCHEMA_VERSION),))
            for table in ("labels", "deep"):
                con.execute(
                    f"""CREATE TABLE IF NOT EXISTS {table}(
                            id INTEGER PRIMARY KEY,
                            pid TEXT NOT NULL,
                            kind TEXT NOT NULL,
                            mode_index INTEGER,
                            ref TEXT,
                            scope INTEGER NOT NULL,
                            source TEXT NOT NULL DEFAULT '',
                            text TEXT NOT NULL)"""
                )
                con.execute(f"CREATE INDEX IF NOT EXISTS {table}_pid ON {table}(pid)")
                con.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {table}_fts USING fts5("
                    f"text, content='{table}', content_rowid='id', tokenize='unicode61 remove_diacritics 2')"
                )
            con.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS labels_tri USING fts5("
                "text, content='labels', content_rowid='id', tokenize='trigram')"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS deep_sources("
                "pid TEXT NOT NULL, source TEXT NOT NULL, sha TEXT NOT NULL, docs INTEGER NOT NULL, "
                "PRIMARY KEY(pid, source))"
            )
            con.commit()

    def _label_fields(self, p: PoleEntry) -> list[TextField]:
        out: list[TextField] = []
        if p.modes:
            out.extend(
                TextField("mode", m.label, mode_index=m.index, scope=SCOPE_LABELS) for m in p.modes if m.label
            )
            if p.island_description:
                out.append(TextField("island_summary", p.island_description, scope=SCOPE_LABELS))
        elif p.description:
            out.append(TextField("description", p.description, scope=SCOPE_LABELS))
        if p.headline:
            out.append(TextField("headline", p.headline, scope=SCOPE_LABELS))
        neuron = self.catalog.neuron(p.did)
        if neuron and neuron.circuit_label:
            out.append(TextField("circuit_label", neuron.circuit_label, scope=SCOPE_LABELS))
        return out

    def build_labels(self, force: bool = False) -> bool:
        """(Re)build the label index when the catalog changed.  Returns True when it was rebuilt."""
        rows: list[tuple[str, str, int | None, str | None, int, str]] = []
        digest = hashlib.sha1()
        for p in self.catalog.iter_poles():
            for f in self._label_fields(p):
                rows.append((p.pid, f.kind, f.mode_index, f.ref, f.scope, f.text))
                digest.update(f"{p.pid}\x1f{f.kind}\x1f{f.mode_index}\x1f{f.text}\x1e".encode())
        fingerprint = digest.hexdigest()
        with self._write_lock:
            if self._closed:
                return False
            con = self._keepalive
            row = con.execute("SELECT value FROM meta WHERE key='labels'").fetchone()
            if row and row[0] == fingerprint and not force:
                return False
            con.execute("DELETE FROM labels")
            con.execute("INSERT INTO labels_fts(labels_fts) VALUES('delete-all')")
            con.execute("INSERT INTO labels_tri(labels_tri) VALUES('delete-all')")
            con.executemany("INSERT INTO labels(pid,kind,mode_index,ref,scope,text) VALUES(?,?,?,?,?,?)", rows)
            con.execute("INSERT INTO labels_fts(rowid,text) SELECT id,text FROM labels")
            con.execute("INSERT INTO labels_tri(rowid,text) SELECT id,text FROM labels")
            con.execute("INSERT OR REPLACE INTO meta VALUES('labels', ?)", (fingerprint,))
            con.commit()
        return True

    # ------------------------------------------------------------------ deep texts

    def deep_state(self, pid: str) -> dict[str, str]:
        """``source -> sha`` of what is indexed for ``pid``."""
        return dict(self._con.execute("SELECT source,sha FROM deep_sources WHERE pid=?", (pid,)).fetchall())

    def index_deep(self, pid: str, source: str, sha: str, fields: Iterable[TextField]) -> int:
        """Replace the deep texts of one (pole, source file).  Idempotent on ``sha``."""
        rows = [
            (pid, f.kind, f.mode_index, f.ref, f.scope, source, f.text)
            for f in fields
            if f.text and f.text.strip()
        ]
        with self._write_lock:
            if self._closed:
                return 0
            con = self._keepalive
            old = con.execute(
                "SELECT sha FROM deep_sources WHERE pid=? AND source=?", (pid, source)
            ).fetchone()
            if old and old[0] == sha:
                return 0
            self._drop_deep_locked(con, pid, source)
            cur = con.cursor()
            for r in rows:
                cur.execute(
                    "INSERT INTO deep(pid,kind,mode_index,ref,scope,source,text) VALUES(?,?,?,?,?,?,?)", r
                )
                cur.execute("INSERT INTO deep_fts(rowid,text) VALUES(?,?)", (cur.lastrowid, r[6]))
            con.execute(
                "INSERT OR REPLACE INTO deep_sources(pid,source,sha,docs) VALUES(?,?,?,?)",
                (pid, source, sha, len(rows)),
            )
            con.commit()
        return len(rows)

    def drop_deep(self, pid: str, source: str | None = None) -> None:
        with self._write_lock:
            if self._closed:
                return
            self._drop_deep_locked(self._keepalive, pid, source)
            self._keepalive.commit()

    @staticmethod
    def _drop_deep_locked(con: sqlite3.Connection, pid: str, source: str | None) -> None:
        where, args = "pid=?", [pid]
        if source is not None:
            where += " AND source=?"
            args.append(source)
        for rowid, text in con.execute(f"SELECT id,text FROM deep WHERE {where}", args).fetchall():
            con.execute("INSERT INTO deep_fts(deep_fts,rowid,text) VALUES('delete',?,?)", (rowid, text))
        con.execute(f"DELETE FROM deep WHERE {where}", args)
        con.execute(f"DELETE FROM deep_sources WHERE {where}", args)

    def stats(self) -> dict[str, Any]:
        con = self._con
        labels = con.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
        deep = dict(con.execute("SELECT scope,COUNT(*) FROM deep GROUP BY scope").fetchall())
        poles = con.execute("SELECT COUNT(DISTINCT pid) FROM deep_sources").fetchone()[0]
        return {
            "label_texts": labels,
            "deep_texts": deep.get(SCOPE_DEEP, 0),
            "example_texts": deep.get(SCOPE_EXAMPLES, 0),
            "poles_with_deep_texts": poles,
        }

    # ------------------------------------------------------------------ term evaluation

    def _rows(self, sql: str, args: list[Any]) -> list[tuple[Any, ...]]:
        try:
            return self._con.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            return []  # an expression FTS5 cannot parse simply matches nothing

    def _term_hits(self, term: Term, scopes: set[int], mode: str) -> dict[str, list[tuple[float, str, int | None, str | None, str]]]:
        """``pid -> [(score, kind, mode_index, ref, text)]`` for one term."""
        hits: dict[str, list[tuple[float, str, int | None, str | None, str]]] = defaultdict(list)
        text = term.text
        tables = []
        if SCOPE_LABELS in scopes:
            tables.append(("labels", "labels_fts", [SCOPE_LABELS]))
        deep_scopes = sorted(s for s in scopes if s != SCOPE_LABELS)
        if deep_scopes:
            tables.append(("deep", "deep_fts", deep_scopes))

        def add(rows: Iterable[tuple[Any, ...]]) -> None:
            for pid, kind, mode_index, ref, body, score in rows:
                hits[pid].append((float(score) * KIND_WEIGHT.get(kind, 0.5), kind, mode_index, ref, body))

        def words() -> None:
            expr = _fts_literal(text) + (" *" if (term.prefix or (mode == "auto" and not term.phrase)) and len(text) >= 2 else "")
            for table, fts, scs in tables:
                marks = ",".join("?" * len(scs))
                add(
                    self._rows(
                        f"SELECT d.pid,d.kind,d.mode_index,d.ref,d.text,-bm25({fts}) "
                        f"FROM {fts} JOIN {table} d ON d.id={fts}.rowid "
                        f"WHERE {fts} MATCH ? AND d.scope IN ({marks}) LIMIT {_MAX_ROWS_PER_TERM}",
                        [expr, *scs],
                    )
                )

        def substring() -> None:
            needle = text
            like = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            for table, _fts, scs in tables:
                marks = ",".join("?" * len(scs))
                if table == "labels" and len(needle) >= 3:
                    add(
                        self._rows(
                            "SELECT d.pid,d.kind,d.mode_index,d.ref,d.text,1.0 "
                            "FROM labels_tri JOIN labels d ON d.id=labels_tri.rowid "
                            f"WHERE labels_tri MATCH ? AND d.scope IN ({marks}) LIMIT {_MAX_ROWS_PER_TERM}",
                            [_fts_literal(needle), *scs],
                        )
                    )
                else:
                    add(
                        self._rows(
                            f"SELECT pid,kind,mode_index,ref,text,1.0 FROM {table} "
                            f"WHERE text LIKE ? ESCAPE '\\' AND scope IN ({marks}) LIMIT {_MAX_ROWS_PER_TERM}",
                            [like, *scs],
                        )
                    )

        if mode == "substring":
            substring()
        elif mode == "words":
            if _WORDCHAR.search(text):
                words()
        else:  # auto
            if _WORDCHAR.search(text):
                words()
            if not hits:
                substring()
            # A bare L30 / E47 / N449 also means "that layer / expert / neuron".
            m = _ID_PART.match(text) if not term.phrase else None
            if m and any(m.groupdict().values()):
                want = {k: int(v) for k, v in m.groupdict().items() if v is not None}
                for p in self.catalog.iter_poles():
                    if (
                        ("L" not in want or p.layer == want["L"])
                        and ("E" not in want or p.expert == want["E"])
                        and ("N" not in want or p.neuron == want["N"])
                    ):
                        hits[p.pid].append((0.2, "id", None, None, p.display))
        return hits

    def _regex_hits(self, pattern: str, scopes: set[int]) -> dict[str, list[tuple[float, str, int | None, str | None, str]]]:
        if not self.allow_regex:
            raise ValueError("regular-expression search is switched off on this server")
        if len(pattern) > _MAX_REGEX_LENGTH:
            raise ValueError(f"regular expression longer than {_MAX_REGEX_LENGTH} characters")
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        hits: dict[str, list[tuple[float, str, int | None, str | None, str]]] = defaultdict(list)
        deadline = time.monotonic() + 10.0
        for table, scs in (("labels", [SCOPE_LABELS]), ("deep", sorted(s for s in scopes if s != SCOPE_LABELS))):
            scs = [s for s in scs if s in scopes]
            if not scs:
                continue
            marks = ",".join("?" * len(scs))
            cur = self._con.execute(
                f"SELECT pid,kind,mode_index,ref,text FROM {table} WHERE scope IN ({marks})", scs
            )
            for pid, kind, mode_index, ref, body in cur:
                if rx.search(body):
                    hits[pid].append((KIND_WEIGHT.get(kind, 0.5), kind, mode_index, ref, body))
                if time.monotonic() > deadline:
                    raise ValueError("regular-expression search took too long; narrow the scope or simplify it")
        return hits

    # ------------------------------------------------------------------ filters

    def _feature(self, p: PoleEntry, name: str, mirrored: dict[str, list[str]] | None) -> bool:
        if name == "subcircuit":
            return p.has_subcircuit
        if name == "overview":
            return p.has_overview
        if name == "landscape":
            return p.has_landscape
        if name == "islands":
            return p.has_islands
        if name == "circuit":
            return p.has_circuit
        if name == "simulatability":
            return p.simulatability is not None
        if name == "headline":
            return bool(p.headline)
        if name == "mirrored":
            return bool((mirrored or {}).get(p.pid))
        return False

    def _filter(self, q: SearchQuery) -> Callable[[PoleEntry], bool]:
        layers = set(q.layers) if q.layers else None
        experts = set(q.experts) if q.experts else None
        neurons = set(q.neurons) if q.neurons else None
        cats = set(q.categories) if q.categories else None
        ids: set[str] | None = None
        if q.ids:
            ids = set()
            for ref in q.ids:
                parsed = parse_reference(ref)
                if parsed:
                    nid, pole = parsed
                    ids.update([nid.pid(pole)] if pole else [nid.pid("pos"), nid.pid("neg")])
        need_mirror = "mirrored" in (q.has or []) or "mirrored" in (q.lacks or [])
        mirrored = self._mirrored(self.catalog.poles.keys()) if (need_mirror and self._mirrored) else None

        def ok(p: PoleEntry) -> bool:
            if ids is not None and p.pid not in ids:
                return False
            if layers is not None and p.layer not in layers:
                return False
            if q.layer_min is not None and p.layer < q.layer_min:
                return False
            if q.layer_max is not None and p.layer > q.layer_max:
                return False
            if experts is not None and p.expert not in experts:
                return False
            if neurons is not None and p.neuron not in neurons:
                return False
            if q.pole and p.pole != q.pole:
                return False
            if cats is not None and p.category not in cats:
                return False
            if q.verdict and p.verdict != q.verdict:
                return False
            if q.sample == "public" and not p.in_public_sample:
                return False
            if q.sample == "hidden" and p.in_public_sample:
                return False
            n_modes = len(p.modes) if p.modes else (p.n_modes or 0)
            if q.modes_min is not None and n_modes < q.modes_min:
                return False
            if q.modes_max is not None and n_modes > q.modes_max:
                return False
            if q.simulatability_min is not None and (p.simulatability is None or p.simulatability < q.simulatability_min):
                return False
            if q.simulatability_max is not None and (p.simulatability is None or p.simulatability > q.simulatability_max):
                return False
            if q.damage_min is not None and (p.main_damage_share is None or p.main_damage_share < q.damage_min):
                return False
            if q.damage_max is not None and (p.main_damage_share is None or p.main_damage_share > q.damage_max):
                return False
            if q.has and not all(self._feature(p, f, mirrored) for f in q.has):
                return False
            return not (q.lacks and any(self._feature(p, f, mirrored) for f in q.lacks))

        return ok

    # ------------------------------------------------------------------ search

    def search(self, query: SearchQuery | dict[str, Any] | str) -> SearchResponse:
        started = time.perf_counter()
        if isinstance(query, str):
            query = SearchQuery(q=query)
        elif isinstance(query, dict):
            query = SearchQuery.model_validate(query)
        parsed = parse_query(query)
        q = parsed.query
        notes = list(parsed.notes)
        scopes = {_SCOPE_BY_NAME[s] for s in q.scope} or {SCOPE_LABELS}

        # pid -> (score, matches)
        scored: dict[str, float] = {}
        matched: dict[str, list[tuple[float, str, int | None, str | None, str]]] = defaultdict(list)
        has_text = bool(parsed.text)

        if parsed.direct:
            for pid in parsed.direct:
                if pid in self.catalog.poles:
                    scored[pid] = 100.0
            if not scored:
                notes.append(f"{parsed.text!r} looks like an id, but this release has no such neuron")
        elif q.match == "semantic" and has_text:
            if self.semantic is None:
                raise ValueError("semantic search is not available on this server")
            for pid, mode_index, score, label in self.semantic.query(parsed.text, top=2000):
                if score <= 0:
                    continue
                scored[pid] = max(scored.get(pid, 0.0), float(score))
                matched[pid].append((float(score), "mode" if mode_index is not None else "description", mode_index, None, label))
            if not scored:
                notes.append("no word of the query occurs in the atlas vocabulary; try match=auto")
        elif q.match == "regex" and has_text:
            hits = self._regex_hits(parsed.text, scopes)
            for pid, rows in hits.items():
                scored[pid] = max(r[0] for r in rows)
                matched[pid] = rows
        elif has_text:
            mode = q.match
            candidate: set[str] | None = None
            for group in parsed.groups:
                group_hits: dict[str, list[tuple[float, str, int | None, str | None, str]]] = defaultdict(list)
                for term in group:
                    for pid, rows in self._term_hits(term, scopes, mode).items():
                        group_hits[pid].extend(rows)
                pids = set(group_hits)
                candidate = pids if candidate is None else candidate & pids
                for pid, rows in group_hits.items():
                    matched[pid].extend(rows)
                    scored[pid] = scored.get(pid, 0.0) + max(r[0] for r in rows)
            if candidate is None:  # only negative terms
                candidate = set(self.catalog.poles)
                for pid in candidate:
                    scored.setdefault(pid, 0.0)
            for term in parsed.negatives:
                candidate -= set(self._term_hits(term, scopes, "auto" if mode == "auto" else mode))
            scored = {pid: s for pid, s in scored.items() if pid in candidate}
        else:
            scored = dict.fromkeys(self.catalog.poles, 0.0)

        keep = self._filter(q)
        poles = [self.catalog.poles[pid] for pid in scored if pid in self.catalog.poles]
        poles = [p for p in poles if keep(p)]

        sort = q.sort
        if sort == "relevance" and not (has_text or parsed.direct):
            sort = "id"
        descending = (q.order == "desc") if q.order else sort in {"relevance", "simulatability", "modes", "damage", "share"}

        def id_key(p: PoleEntry) -> tuple[int, int, int, int]:
            return (p.layer, -1 if p.expert is None else p.expert, p.neuron, 0 if p.pole == "pos" else 1)

        def value(p: PoleEntry) -> float | None:
            if sort == "relevance":
                return scored.get(p.pid, 0.0)
            if sort == "simulatability":
                return p.simulatability
            if sort == "modes":
                return float(len(p.modes)) if p.has_subcircuit else None
            if sort == "damage":
                return p.main_damage_share
            if sort == "share":
                return p.top_mode_share
            if sort == "layer":
                return float(p.layer)
            if sort == "random":
                return float(p.sample_rank)
            return None

        if sort == "id":
            poles.sort(key=id_key, reverse=descending)
        else:
            # Like the explorer: poles without a value go last in either direction.
            with_value = [p for p in poles if value(p) is not None]
            without = [p for p in poles if value(p) is None]
            with_value.sort(key=id_key)
            with_value.sort(key=lambda p: value(p), reverse=descending)  # type: ignore[arg-type,return-value]
            without.sort(key=id_key)
            poles = with_value + without

        facets = None
        if q.facets:
            facets = {
                "layer": {str(k): v for k, v in sorted(Counter(p.layer for p in poles).items())},
                "category": {c: n for c in CATEGORIES if (n := sum(1 for p in poles if p.category == c))},
                "pole": dict(Counter(p.pole for p in poles)),
                "sample": {
                    "public": sum(1 for p in poles if p.in_public_sample),
                    "hidden": sum(1 for p in poles if not p.in_public_sample),
                },
            }

        response = SearchResponse(
            query=q.model_dump(exclude_defaults=True),
            total=0,
            offset=q.offset,
            limit=q.limit,
            group_by=q.group_by,
            facets=facets,
            notes=notes,
        )

        def page(items: list[Any]) -> list[Any]:
            response.total = len(items)
            chunk = items[q.offset : q.offset + q.limit]
            end = q.offset + len(chunk)
            response.next_offset = end if end < len(items) and chunk else None
            return chunk

        def matches_of(p: PoleEntry, limit: int = 6) -> list[Match]:
            if not q.snippets:
                return []
            share = {m.index: m.share for m in p.modes}
            seen: set[tuple[str, int | None, str | None, str]] = set()
            out: list[Match] = []
            for score, kind, mode_index, ref, body in sorted(matched.get(p.pid, ()), key=lambda r: -r[0]):
                key = (kind, mode_index, ref, body[:80])
                if key in seen or kind == "id":
                    continue
                seen.add(key)
                out.append(
                    Match(
                        field=kind,
                        text=_snippet(body, parsed),
                        mode_index=mode_index,
                        share=share.get(mode_index) if mode_index is not None else None,
                        ref=ref,
                        score=round(score, 4),
                    )
                )
                if len(out) >= limit:
                    break
            return out

        if q.group_by == "pole":
            chunk = page(poles)
            mirrored = self._mirrored([p.pid for p in chunk]) if self._mirrored else {}
            response.hits = [self.hit(p, scored.get(p.pid, 0.0), matches_of(p), mirrored.get(p.pid)) for p in chunk]
        elif q.group_by == "neuron":
            order: dict[str, list[PoleEntry]] = {}
            for p in poles:
                order.setdefault(p.did, []).append(p)
            chunk = page(list(order.items()))
            mirrored = self._mirrored([p.pid for _, ps in chunk for p in ps]) if self._mirrored else {}
            for did, ps in chunk:
                neuron = self.catalog.neurons[did]
                ps = sorted(ps, key=lambda p: 0 if p.pole == "pos" else 1)
                response.neurons.append(
                    NeuronHit(
                        did=did,
                        display=neuron.display,
                        layer=neuron.layer,
                        expert=neuron.expert,
                        neuron=neuron.neuron,
                        circuit_label=neuron.circuit_label,
                        score=round(max(scored.get(p.pid, 0.0) for p in ps), 4),
                        poles=[self.hit(p, scored.get(p.pid, 0.0), matches_of(p), mirrored.get(p.pid)) for p in ps],
                    )
                )
        else:  # mode
            mode_rows: list[tuple[float, PoleEntry, int, list[tuple[float, str, int | None, str | None, str]]]] = []
            for p in poles:
                by_mode: dict[int, list[tuple[float, str, int | None, str | None, str]]] = defaultdict(list)
                for row in matched.get(p.pid, ()):
                    if row[2] is not None:
                        by_mode[row[2]].append(row)
                if has_text and not parsed.direct:
                    for mode_index, rows in by_mode.items():
                        mode_rows.append((max(r[0] for r in rows), p, mode_index, rows))
                else:
                    for m in p.modes:
                        mode_rows.append((m.share or 0.0, p, m.index, []))
            if sort in ("relevance", "share") or (has_text and sort == "id"):
                mode_rows.sort(key=lambda r: -r[0])
            chunk = page(mode_rows)
            for score, p, mode_index, rows in chunk:
                brief = next((m for m in p.modes if m.index == mode_index), None)
                response.modes.append(
                    ModeHit(
                        pid=p.pid,
                        did=p.did,
                        pole=p.pole,  # type: ignore[arg-type]
                        display=p.display,
                        layer=p.layer,
                        category=p.category,  # type: ignore[arg-type]
                        mode_index=mode_index,
                        label=brief.label if brief else "",
                        share=brief.share if brief else None,
                        score=round(score, 4),
                        matches=[
                            Match(field=k, text=_snippet(b, parsed), mode_index=mi, ref=ref, score=round(s, 4))
                            for s, k, mi, ref, b in sorted(rows, key=lambda r: -r[0])[:4]
                            if k != "mode"
                        ]
                        if q.snippets
                        else [],
                        # Modes without firings are not in the explorer's own mode list, which shifts its indices.
                        atlas_url=p.atlas_url("islands", mode_index if brief and brief.share else None),
                    )
                )

        response.took_ms = round((time.perf_counter() - started) * 1000, 2)
        return response

    def hit(
        self,
        p: PoleEntry,
        score: float = 0.0,
        matches: list[Match] | None = None,
        mirrored: list[str] | None = None,
        *,
        with_modes: bool = True,
    ) -> PoleHit:
        return pole_hit(p, score, matches, mirrored, with_modes=with_modes)


def pole_hit(
    p: PoleEntry,
    score: float = 0.0,
    matches: list[Match] | None = None,
    mirrored: list[str] | None = None,
    *,
    with_modes: bool = True,
) -> PoleHit:
    """The public summary of one pole (used for search hits and as the header of detail documents)."""
    available = [
        name
        for name, flag in (
            ("subcircuit", p.has_subcircuit or p.subcircuit_empty),
            ("overview", p.has_overview),
            ("landscape", p.has_landscape),
            ("islands", p.has_islands),
            ("circuit", p.has_circuit),
        )
        if flag
    ]
    return PoleHit(
        pid=p.pid,
        did=p.did,
        pole=p.pole,  # type: ignore[arg-type]
        display=p.display,
        layer=p.layer,
        expert=p.expert,
        neuron=p.neuron,
        category=p.category,  # type: ignore[arg-type]
        category_words=p.category_words,
        description=p.description,
        headline=p.headline,
        n_modes=len(p.modes) if p.has_subcircuit else p.n_modes,
        n_subcircuits=p.n_subcircuits,
        verdict=p.verdict,
        main_damage_share=p.main_damage_share,
        simulatability=p.simulatability,
        in_public_sample=p.in_public_sample,
        available=available,
        mirrored=mirrored or [],
        score=round(score, 4),
        matches=matches or [],
        modes=[ModeOut(index=m.index, label=m.label, share=m.share) for m in p.modes] if with_modes else None,
        atlas_url=p.atlas_url(),
    )


def _snippet(text: str, parsed: ParsedQuery, width: int = 280) -> str:
    """The part of ``text`` around the first query term (whole text when it is short)."""
    if len(text) <= width:
        return text
    lower = text.lower()
    pos = -1
    for group in parsed.groups:
        for term in group:
            pos = lower.find(term.text.lower())
            if pos >= 0:
                break
        if pos >= 0:
            break
    if pos < 0:
        return text[:width].rstrip() + " …"
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    start = max(0, end - width)
    return ("… " if start else "") + text[start:end].strip() + (" …" if end < len(text) else "")
