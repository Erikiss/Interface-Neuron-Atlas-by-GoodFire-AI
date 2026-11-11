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

import contextlib
import hashlib
import math
import re
import sqlite3
import tempfile
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import regex
from pydantic import ValidationError

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

SCHEMA_VERSION = 4

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

#: Most search terms one query may carry (repeats are counted once).
MAX_TERMS = 32
#: Most values an integer range such as ``expert:0..255`` may expand to.
MAX_RANGE_VALUES = 4096
_MAX_REGEX_LENGTH = 300
#: Total time one regular-expression search may take.
_REGEX_BUDGET_SECONDS = 5.0
#: Word beginnings (``riv`` -> river) are tried for words of at least this length ...
_PREFIX_MIN = 3
#: ... and count less than a whole-word hit, so that ``cat`` ranks "cat" above "category".
_PREFIX_WEIGHT = 0.6
#: Semantic search keeps poles scoring at least this share of the best score.
_SEMANTIC_KEEP = 0.3


@dataclass(frozen=True)
class TextField:
    """One searchable text of a pole."""

    kind: str
    text: str
    mode_index: int | None = None
    ref: str | None = None
    scope: int = SCOPE_DEEP


#: One matching text, best per (pole, mode, kind of text): (score, kind, mode_index, table, rowid).
Row = tuple[float, str, "int | None", str, int]


# --------------------------------------------------------------------------- query parsing

_FILTER_KEYS = (
    "layer", "l", "expert", "e", "neuron", "n", "pole", "cat", "category", "verdict", "modes", "sim",
    "simulatability", "damage", "has", "lacks", "sample", "in", "sort", "group",
)  # fmt: skip
#: ``key:value`` - no space after the colon, so ordinary sentences ("results in: …") stay text.
_INLINE = re.compile(rf"^(?P<key>{'|'.join(_FILTER_KEYS)}):(?P<val>\S+)$", re.IGNORECASE)
_NUM = r"-?\d+(?:\.\d+)?"
_RANGE = re.compile(rf"^({_NUM})\.\.({_NUM})$")
_CMP = re.compile(rf"^(>=|<=|>|<|=)?({_NUM})$")
_ID_PART = re.compile(r"^(?:L(?P<L>\d{1,3}))?[\s_/]*(?:E(?P<E>\d{1,4}))?[\s_/]*(?:N(?P<N>\d{1,4}))?$", re.IGNORECASE)
_TOKEN = re.compile(r'(-?)"([^"]*)"|(\S+)')
_WORDCHAR = re.compile(r"\w", re.UNICODE)
#: Text the word index can take as it is: letters, digits and spaces only.
_PLAIN = re.compile(r"[\w\s]+", re.UNICODE)

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

    @property
    def key(self) -> tuple[str, bool, bool]:
        return (self.text.casefold(), self.phrase, self.prefix)


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
        lo, hi = float(m.group(1)), float(m.group(2))
        bounds: tuple[float | None, float | None] = (min(lo, hi), max(lo, hi))
    else:
        m = _CMP.match(value)
        if not m:
            raise ValueError(f"{value!r} is not a number, a comparison like >=3 or a range like 9..36")
        op, num = m.group(1) or "=", float(m.group(2))
        eps = 1e-9
        bounds = {
            "=": (num, num),
            ">=": (num, None),
            ">": (num + eps, None),
            "<=": (None, num),
            "<": (None, num - eps),
        }[op]
    if any(b is not None and not math.isfinite(b) for b in bounds):
        raise ValueError(f"{value!r} is not a usable number")
    return bounds


def int_list(value: str, name: str = "value") -> list[int]:
    """``4,9..14`` -> [4, 9, 10, 11, 12, 13, 14]; ranges may be written either way round.

    Raises ``ValueError`` for anything else, and for ranges that would expand to
    more than :data:`MAX_RANGE_VALUES` values.
    """
    out: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        m = _RANGE.match(part)
        try:
            if m:
                lo, hi = sorted((int(float(m.group(1))), int(float(m.group(2)))))
                if hi - lo >= MAX_RANGE_VALUES:
                    raise ValueError(f"{name}: the range {part} is too large (at most {MAX_RANGE_VALUES} values)")
                out.extend(range(lo, hi + 1))
            else:
                out.append(int(part))
        except OverflowError:
            raise ValueError(f"{name}: {part!r} is not a usable number") from None
        except ValueError as exc:
            if "too large" in str(exc):
                raise
            raise ValueError(f"{name}: {part!r} is not a number or a range like 9..36") from None
        if len(out) > MAX_RANGE_VALUES:
            raise ValueError(f"{name}: at most {MAX_RANGE_VALUES} values")
    return out


def _apply_filter(q: SearchQuery, key: str, val: str) -> None:
    """Fold one inline ``key:value`` into ``q``; raises ``ValueError`` with a readable message."""
    from .ids import normalise_pole

    if key in ("layer", "l"):
        if "," in val:
            q.layers = sorted(set((q.layers or []) + int_list(val, "layer")))
            return
        lo, hi = _bounds(val)
        if lo is not None and lo == hi:
            q.layers = sorted(set((q.layers or []) + [int(lo)]))
        else:
            q.layer_min = int(math.ceil(lo)) if lo is not None else q.layer_min
            q.layer_max = int(math.floor(hi)) if hi is not None else q.layer_max
    elif key in ("expert", "e"):
        q.experts = sorted(set((q.experts or []) + int_list(val, "expert")))
    elif key in ("neuron", "n"):
        q.neurons = sorted(set((q.neurons or []) + int_list(val, "neuron")))
    elif key == "pole":
        q.pole = normalise_pole(val)  # type: ignore[assignment]
    elif key in ("cat", "category"):
        cats = []
        for c in val.split(","):
            if c.strip():
                if c.strip().lower() not in _CAT_ALIASES:
                    raise ValueError(f"cat: unknown category {c!r} (mono, fn, poly, dark, none)")
                cats.append(_CAT_ALIASES[c.strip().lower()])
        q.categories = sorted(set((q.categories or []) + cats))  # type: ignore[arg-type]
    elif key == "verdict":
        v = val.lower()
        if v.startswith("mono"):
            q.verdict = "monosemantic"
        elif v.startswith("poly"):
            q.verdict = "polysemantic"
        else:
            raise ValueError(f"verdict: {val!r} is neither monosemantic nor polysemantic")
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


def parse_query(query: SearchQuery, known: Callable[[str], bool] | None = None) -> ParsedQuery:
    """Resolve inline ``key:value`` filters and split the remaining text into clauses.

    Operators are written in capitals (``OR``, ``AND``, ``NOT``); in lower case
    they are ordinary words.  ``known(did)`` tells whether a neuron exists: bare
    number triples such as ``24/7/365`` only count as an id when it does.
    """
    q = query.model_copy(deep=True)
    parsed = ParsedQuery(q)
    words: list[str] = []
    for m in _TOKEN.finditer(q.q or ""):
        token = m.group(0)
        inline = _INLINE.match(token) if m.group(3) is not None else None
        if inline is None:
            words.append(token)
            continue
        key, val = inline.group("key").lower(), inline.group("val")
        try:
            _apply_filter(q, key, val)
        except (ValueError, KeyError, OverflowError) as exc:
            if key in ("l", "e", "n"):  # short aliases: "e:mail" is more likely text than a filter
                words.append(token)
                parsed.notes.append(f"{token!r} was searched as text (not a valid {key}: filter)")
                continue
            raise ValueError(f"invalid inline filter {token!r}: {exc}") from None
    try:
        parsed.query = q = SearchQuery.model_validate(q.model_dump())
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ValueError(f"invalid inline filter ({problems})") from None
    text = " ".join(" ".join(words).split())
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
        bare = not re.search(r"[A-Za-z]", text.replace("pos", "").replace("neg", ""))
        if not bare or known is None or known(nid.did):
            parsed.direct = [nid.pid(pole)] if pole else [nid.pid("pos"), nid.pid("neg")]
            if bare:
                parsed.notes.append(f"{text!r} was read as the neuron id {nid.display}; put it in quotes to search the text")
            return parsed

    group: list[Term] = []
    pending_or = False
    pending_not = False
    for m in _TOKEN.finditer(text):
        if m.group(3) is not None:
            raw = m.group(3)
            if raw == "OR":
                pending_or = bool(group) or bool(parsed.groups)
                continue
            if raw == "AND":
                continue
            if raw == "NOT":
                pending_not = True
                continue
            # "-word" excludes; "->" or "--flag" are strings to look for, not exclusions.
            negate = raw.startswith("-") and len(raw) > 1 and (raw[1].isalnum() or raw[1] == "_")
            word = raw[1:] if negate else raw
            prefix = word.endswith("*") and len(word.rstrip("*")) > 0
            term = Term(word.rstrip("*") if prefix else word, prefix=prefix, negate=negate)
        else:
            term = Term(m.group(2), phrase=True, negate=bool(m.group(1)))
        if not term.text.strip():
            continue
        if pending_not:
            term.negate, pending_not = True, False
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
    if group:
        parsed.groups.append(group)
    if pending_not:
        parsed.notes.append("a NOT at the end of the query was ignored")
    if not parsed.groups and not parsed.negatives:
        # Only operators were typed ("NOT", "OR"): search them as words rather than return everything.
        parsed.groups = [[Term(t)] for t in text.split()]

    # Repeated terms count once; too many terms make a query slow without making it better.
    seen: set[tuple[str, bool, bool]] = set()
    groups: list[list[Term]] = []
    for g in parsed.groups:
        unique = []
        for t in g:
            if t.key not in seen:
                seen.add(t.key)
                unique.append(t)
        if unique:
            groups.append(unique)
    parsed.groups = groups
    parsed.negatives = list({t.key: t for t in parsed.negatives}.values())
    count = sum(len(g) for g in parsed.groups) + len(parsed.negatives)
    if count > MAX_TERMS:
        raise ValueError(f"too many search terms ({count}); at most {MAX_TERMS}")
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
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None
        if path is None:
            # A private file rather than a shared in-memory database: that one has table-level
            # locks, so concurrent readers and writers would see "database table is locked".
            self._tempdir = tempfile.TemporaryDirectory(prefix="atlas-search-")
            path = Path(self._tempdir.name) / "search.sqlite"
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._uri = Path(path).resolve().as_uri()
        self._writer = self._connect(autocommit=True)
        self._ensure_schema()
        self.build_labels()

    # ------------------------------------------------------------------ storage

    def _connect(self, autocommit: bool = False) -> sqlite3.Connection:
        con = sqlite3.connect(
            self._uri, uri=True, check_same_thread=False, timeout=30, isolation_level=None if autocommit else ""
        )
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.create_function("casefold", 1, _casefold, deterministic=True)
        return con

    @contextlib.contextmanager
    def _write(self) -> Iterator[sqlite3.Connection | None]:
        """One write transaction, serialised across threads *and* processes (BEGIN IMMEDIATE).

        Yields ``None`` once the index has been closed.
        """
        with self._write_lock:
            if self._closed:
                yield None
                return
            con = self._writer
            con.execute("BEGIN IMMEDIATE")
            try:
                yield con
            except BaseException:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")

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
            if self._closed:
                return
            self._closed = True
            self._writer.close()
        if self._tempdir is not None:
            self._tempdir.cleanup()

    def _ensure_schema(self) -> None:
        with self._write() as con:
            assert con is not None
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
        with self._write() as con:
            if con is None:
                return False
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
        with self._write() as con:
            if con is None:
                return 0
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
        return len(rows)

    def drop_deep(self, pid: str, source: str | None = None) -> None:
        with self._write() as con:
            if con is not None:
                self._drop_deep_locked(con, pid, source)

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
        except sqlite3.OperationalError as exc:
            # A text FTS5 cannot read simply matches nothing; anything else (a locked or broken
            # database) must not pass as "no hits".
            message = str(exc).lower()
            if message.startswith("fts5") or "syntax error" in message or "unterminated" in message:
                return []
            raise

    @staticmethod
    def _tables(scopes: set[int]) -> list[tuple[str, str, list[int]]]:
        tables = []
        if SCOPE_LABELS in scopes:
            tables.append(("labels", "labels_fts", [SCOPE_LABELS]))
        deep_scopes = sorted(s for s in scopes if s != SCOPE_LABELS)
        if deep_scopes:
            tables.append(("deep", "deep_fts", deep_scopes))
        return tables

    def _term_rows(self, term: Term, scopes: set[int], mode: str) -> dict[str, list[Row]]:
        """Best matching text per (pole, mode, kind of text) for one term.

        Matching and ranking happen in SQLite and only one row per group comes
        back - without its text, which is fetched later for the hits on screen.
        """
        best: dict[str, dict[tuple[int | None, str], Row]] = defaultdict(dict)
        tables = self._tables(scopes)

        def add(table: str, rows: Iterable[tuple[Any, ...]], weight: float) -> None:
            for pid, mode_index, kind, rowid, score in rows:
                value = float(score) * weight * KIND_WEIGHT.get(kind, 0.5)
                key = (mode_index, kind)
                old = best[pid].get(key)
                if old is None or value > old[0]:
                    best[pid][key] = (value, kind, mode_index, table, int(rowid))

        def fts(expr: str, weight: float) -> None:
            for table, index, scs in tables:
                marks = ",".join("?" * len(scs))
                add(
                    table,
                    self._rows(
                        f"WITH m AS MATERIALIZED (SELECT rowid AS id, -bm25({index}) AS s FROM {index} WHERE {index} MATCH ?) "
                        f"SELECT d.pid, d.mode_index, d.kind, d.id, MAX(m.s) FROM m JOIN {table} d ON d.id = m.id "
                        f"WHERE d.scope IN ({marks}) GROUP BY d.pid, d.mode_index, d.kind",
                        [expr, *scs],
                    ),
                    weight,
                )

        def words() -> None:
            literal = _fts_literal(term.text)
            if term.prefix:
                fts(literal + " *", 1.0)
                return
            fts(literal, 1.0)
            if mode == "auto" and not term.phrase and len(term.text) >= _PREFIX_MIN:
                fts(literal + " *", _PREFIX_WEIGHT)

        def substring() -> None:
            needle = term.text
            for table, _index, scs in tables:
                marks = ",".join("?" * len(scs))
                if table == "labels" and len(needle) >= 3:
                    # The trigram index folds case for every script.
                    sql = (
                        "SELECT d.pid, d.mode_index, d.kind, MIN(d.id), 1.0 FROM labels_tri JOIN labels d "
                        f"ON d.id = labels_tri.rowid WHERE labels_tri MATCH ? AND d.scope IN ({marks}) "
                        "GROUP BY d.pid, d.mode_index, d.kind"
                    )
                    args: list[Any] = [_fts_literal(needle), *scs]
                elif needle.isascii():
                    sql = (
                        f"SELECT pid, mode_index, kind, MIN(id), 1.0 FROM {table} WHERE text LIKE ? ESCAPE '\\' "
                        f"AND scope IN ({marks}) GROUP BY pid, mode_index, kind"
                    )
                    args = ["%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", *scs]
                else:  # LIKE folds ASCII only
                    sql = (
                        f"SELECT pid, mode_index, kind, MIN(id), 1.0 FROM {table} WHERE instr(casefold(text), ?) > 0 "
                        f"AND scope IN ({marks}) GROUP BY pid, mode_index, kind"
                    )
                    args = [_casefold(needle), *scs]
                add(table, self._rows(sql, args), 1.0)

        plain = bool(_PLAIN.fullmatch(term.text))
        if mode == "substring":
            substring()
        elif mode == "words":
            if _WORDCHAR.search(term.text):
                words()
        else:  # auto: words where the word index can take the text, characters otherwise
            if plain:
                words()
            if not best:
                # C++, #ff8800, U+28xx - and parts of words (anub -> Danube)
                substring()
            # A bare L30 / E47 / N449 also means "that layer / expert / neuron".
            m = _ID_PART.match(term.text) if not term.phrase else None
            if m and any(m.groupdict().values()):
                want = {k: int(v) for k, v in m.groupdict().items() if v is not None}
                for p in self.catalog.iter_poles():
                    if (
                        ("L" not in want or p.layer == want["L"])
                        and ("E" not in want or p.expert == want["E"])
                        and ("N" not in want or p.neuron == want["N"])
                    ):
                        best[p.pid].setdefault((None, "id"), (0.2, "id", None, "", 0))
        return {pid: list(groups.values()) for pid, groups in best.items()}

    def _regex_rows(self, pattern: str, scopes: set[int]) -> tuple[dict[str, list[Row]], Any]:
        if not self.allow_regex:
            raise ValueError("regular-expression search is switched off on this server")
        if len(pattern) > _MAX_REGEX_LENGTH:
            raise ValueError(f"regular expression longer than {_MAX_REGEX_LENGTH} characters")
        try:
            rx = regex.compile(pattern, regex.IGNORECASE)
        except (regex.error, OverflowError, RecursionError) as exc:
            raise ValueError(f"invalid regular expression: {exc}") from None
        best: dict[str, dict[tuple[int | None, str], Row]] = defaultdict(dict)
        deadline = time.monotonic() + _REGEX_BUDGET_SECONDS
        too_long = ValueError("the regular expression took too long; narrow the scope or simplify it")
        for table, _index, scs in self._tables(scopes):
            marks = ",".join("?" * len(scs))
            cursor = self._con.execute(f"SELECT id, pid, kind, mode_index, text FROM {table} WHERE scope IN ({marks})", scs)
            for rowid, pid, kind, mode_index, body in cursor:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise too_long
                try:
                    found = rx.search(body, timeout=remaining)
                except TimeoutError:
                    raise too_long from None
                if found:
                    value = KIND_WEIGHT.get(kind, 0.5)
                    key = (mode_index, kind)
                    if key not in best[pid] or value > best[pid][key][0]:
                        best[pid][key] = (value, kind, mode_index, table, rowid)
        return {pid: list(groups.values()) for pid, groups in best.items()}, rx

    def _texts(self, rows: Iterable[Row]) -> dict[tuple[str, int], tuple[str | None, str]]:
        """``(table, rowid) -> (ref, text)`` for the given rows."""
        wanted: dict[str, set[int]] = defaultdict(set)
        for row in rows:
            if row[3]:
                wanted[row[3]].add(row[4])
        out: dict[tuple[str, int], tuple[str | None, str]] = {}
        for table, ids in wanted.items():
            ordered = sorted(ids)
            for i in range(0, len(ordered), 500):
                chunk = ordered[i : i + 500]
                marks = ",".join("?" * len(chunk))
                for rowid, ref, text in self._con.execute(f"SELECT id, ref, text FROM {table} WHERE id IN ({marks})", chunk):
                    out[(table, rowid)] = (ref, text)
        return out

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

    def search(self, query: SearchQuery | dict[str, Any] | str, *, group_by: str | None = None) -> SearchResponse:
        """Run a search.  ``group_by`` overrides the query's grouping, including an inline ``group:``."""
        started = time.perf_counter()
        if isinstance(query, str):
            query = SearchQuery(q=query)
        elif isinstance(query, dict):
            query = SearchQuery.model_validate(query)
        parsed = parse_query(query, known=lambda did: did in self.catalog.neurons)
        q = parsed.query
        if group_by is not None and q.group_by != group_by:
            q = parsed.query = q.model_copy(update={"group_by": group_by})
        notes = list(parsed.notes)
        scopes = {_SCOPE_BY_NAME[s] for s in q.scope} or {SCOPE_LABELS}

        scored: dict[str, float] = {}
        matched: dict[str, list[Row]] = defaultdict(list)
        semantic_labels: dict[str, list[tuple[float, int | None, str]]] = {}
        locate: Any = None
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
            ranked = self.semantic.query(parsed.text, top=len(self.catalog.poles))
            floor = max(0.05, _SEMANTIC_KEEP * ranked[0][2]) if ranked else 0.0
            for pid, mode_index, score, label in ranked:
                if score < floor:
                    break
                scored[pid] = float(score)
                semantic_labels[pid] = [(float(score), mode_index, label)]
            if not scored:
                notes.append("no word of the query occurs in the atlas vocabulary; try match=auto")
            elif len(ranked) > len(scored):
                notes.append(f"semantic search keeps poles scoring at least {_SEMANTIC_KEEP:.0%} of the best match")
        elif q.match == "regex" and has_text:
            hits, locate = self._regex_rows(parsed.text, scopes)
            for pid, rows in hits.items():
                scored[pid] = max(r[0] for r in rows)
                matched[pid] = rows
        elif has_text:
            mode = q.match
            candidate: set[str] | None = None
            for group in parsed.groups:
                group_hits: dict[str, list[Row]] = defaultdict(list)
                for term in group:
                    for pid, rows in self._term_rows(term, scopes, mode).items():
                        group_hits[pid].extend(rows)
                candidate = set(group_hits) if candidate is None else candidate & set(group_hits)
                for pid, rows in group_hits.items():
                    if candidate is not None and pid not in candidate:
                        continue
                    matched[pid].extend(rows)
                    scored[pid] = scored.get(pid, 0.0) + max(r[0] for r in rows)
            if candidate is None:  # only negative terms
                candidate = set(self.catalog.poles)
                for pid in candidate:
                    scored.setdefault(pid, 0.0)
            for term in parsed.negatives:
                candidate -= set(self._term_rows(term, scopes, mode))
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

        def ordered(items: list[Any], key_value: Callable[[Any], float | None], key_id: Callable[[Any], Any]) -> list[Any]:
            """Sort by value in the requested direction; items without a value go last either way."""
            if sort == "id":
                return sorted(items, key=key_id, reverse=descending)
            with_value = sorted((i for i in items if key_value(i) is not None), key=key_id)
            with_value.sort(key=key_value, reverse=descending)  # type: ignore[arg-type]
            without = sorted((i for i in items if key_value(i) is None), key=key_id)
            return with_value + without

        poles = ordered(poles, value, id_key)

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

        texts: dict[tuple[str, int], tuple[str | None, str]] = {}

        def load_texts(rows: Iterable[Row]) -> None:
            if q.snippets:
                texts.update(self._texts([r for r in rows if (r[3], r[4]) not in texts]))

        def to_matches(p: PoleEntry, rows: list[Row], limit: int, skip_kind: str | None = None) -> list[Match]:
            share = {m.index: m.share for m in p.modes}
            out: list[Match] = []
            for score, kind, mode_index, table, rowid in sorted(rows, key=lambda r: -r[0]):
                if kind == "id" or kind == skip_kind or (table, rowid) not in texts:
                    continue
                ref, body = texts[(table, rowid)]
                out.append(
                    Match(
                        field=kind,
                        text=_snippet(body, parsed, locate),
                        mode_index=mode_index,
                        share=share.get(mode_index) if mode_index is not None else None,
                        ref=ref,
                        score=round(score, 4),
                    )
                )
                if len(out) >= limit:
                    break
            return out

        def matches_of(p: PoleEntry) -> list[Match]:
            if not q.snippets:
                return []
            if p.pid in semantic_labels:
                share = {m.index: m.share for m in p.modes}
                return [
                    Match(field="mode" if mi is not None else "description", text=label, mode_index=mi,
                          share=share.get(mi) if mi is not None else None, score=round(sc, 4))
                    for sc, mi, label in semantic_labels[p.pid]
                ]  # fmt: skip
            return to_matches(p, matched.get(p.pid, []), 6)

        if q.group_by == "pole":
            chunk = page(poles)
            load_texts(r for p in chunk for r in sorted(matched.get(p.pid, ()), key=lambda r: -r[0])[:6])
            mirrored = self._mirrored([p.pid for p in chunk]) if self._mirrored else {}
            response.hits = [self.hit(p, scored.get(p.pid, 0.0), matches_of(p), mirrored.get(p.pid)) for p in chunk]
        elif q.group_by == "neuron":
            order: dict[str, list[PoleEntry]] = {}
            for p in poles:
                order.setdefault(p.did, []).append(p)
            chunk = page(list(order.items()))
            load_texts(r for _, ps in chunk for p in ps for r in sorted(matched.get(p.pid, ()), key=lambda r: -r[0])[:6])
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
        else:  # mode: one hit per matching mode of the pole's mode list
            mode_rows: list[tuple[float, PoleEntry, int, list[Row]]] = []
            for p in poles:
                listed = {m.index for m in p.modes}
                if has_text and not parsed.direct:
                    by_mode: dict[int, list[Row]] = defaultdict(list)
                    for row in matched.get(p.pid, ()):
                        if row[2] is not None and row[2] in listed:
                            by_mode[row[2]].append(row)
                    for sc, mi, _label in semantic_labels.get(p.pid, ()):
                        if mi is not None and mi in listed:
                            by_mode[mi].append((sc, "mode", mi, "", 0))
                    for mode_index, rows in by_mode.items():
                        mode_rows.append((max(r[0] for r in rows), p, mode_index, rows))
                else:
                    for m in p.modes:
                        mode_rows.append((scored.get(p.pid, 0.0), p, m.index, []))

            def mode_share(r: tuple[float, PoleEntry, int, list[Row]]) -> float | None:
                return next((m.share for m in r[1].modes if m.index == r[2]), None)

            def mode_value(r: tuple[float, PoleEntry, int, list[Row]]) -> float | None:
                if sort == "relevance":
                    return r[0]
                if sort == "share":
                    return mode_share(r)
                return value(r[1])

            mode_rows = ordered(mode_rows, mode_value, lambda r: (*id_key(r[1]), r[2]))
            chunk = page(mode_rows)
            load_texts(row for _s, _p, _m, rows in chunk for row in sorted(rows, key=lambda r: -r[0])[:4])
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
                        matches=to_matches(p, rows, 4, skip_kind="mode") if q.snippets else [],
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


def _casefold(value: Any) -> Any:
    return value.casefold() if isinstance(value, str) else value


def _snippet(text: str, parsed: ParsedQuery, locate: Any = None, width: int = 280) -> str:
    """The part of ``text`` around the first query term (whole text when it is short).

    ``locate`` is a compiled regular expression (regex searches); otherwise the
    query's terms are looked for.
    """
    if len(text) <= width:
        return text
    pos = -1
    if locate is not None:
        try:
            found = locate.search(text, timeout=0.2)
        except TimeoutError:
            found = None
        pos = found.start() if found else -1
    else:
        lower = text.casefold()
        for group in parsed.groups:
            for term in group:
                pos = lower.find(term.text.casefold())
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
