"""Meaning-based lookup over the mode labels: latent semantic analysis (LSA).

Every mode label (and the summary of poles without modes) becomes a TF-IDF
vector that is projected onto a few hundred latent dimensions.  Texts that
share vocabulary *patterns* end up close together even when they share no
literal word, which is what "find poles about X" and "what is similar to this
pole" need.  It is also the coordinate system the geometry tools start from.

The model is fitted locally from the catalog (a few seconds, no download, no
external model) and cached next to the mirror as plain arrays.  A stronger
sentence-embedding model can be plugged in through :class:`Embedder`.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from scipy import sparse

from .catalog import Catalog

CACHE_VERSION = 3


class Embedder(Protocol):
    """Anything that turns texts into unit vectors (rows)."""

    name: str

    def fit(self, texts: Sequence[str]) -> np.ndarray: ...

    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


@dataclass(frozen=True)
class Doc:
    pid: str
    #: Index of the mode inside the pole, or ``None`` for a pole-level text.
    mode_index: int | None
    text: str
    #: Share of the pole's firings in this mode (1.0 for pole-level texts).
    weight: float


def _unit(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class LsaEmbedder:
    """TF-IDF (words and word pairs) followed by truncated SVD."""

    def __init__(self, dims: int = 256, max_features: int = 40_000):
        self.dims = dims
        self.max_features = max_features
        self.name = f"lsa-{dims}"
        self.terms: list[str] = []
        self.idf = np.zeros(0, dtype=np.float32)
        self.components = np.zeros((0, 0), dtype=np.float32)  # dims x terms
        #: Row-normalised TF-IDF matrix of the fitted texts (documents x terms), for exact-word overlap.
        self.tfidf: sparse.csr_matrix | None = None
        self._lookup: dict[str, int] = {}
        self._analyzer: Callable[[str], list[str]] | None = None

    def _vectorizer(self, n_texts: int = 1_000_000):  # noqa: ANN202 - sklearn type
        from sklearn.feature_extraction.text import TfidfVectorizer

        # Rare-word and common-word cut-offs only make sense with enough texts.
        large = n_texts >= 200
        return TfidfVectorizer(
            lowercase=True,
            strip_accents="unicode",
            stop_words="english",
            ngram_range=(1, 2),
            min_df=2 if large else 1,
            max_df=0.5 if large else 1.0,
            max_features=self.max_features,
            sublinear_tf=True,
            dtype=np.float32,
        )

    @property
    def analyzer(self) -> Callable[[str], list[str]]:
        if self._analyzer is None:
            self._analyzer = self._vectorizer().build_analyzer()
        return self._analyzer

    def fit(self, texts: Sequence[str]) -> np.ndarray:
        from sklearn.decomposition import TruncatedSVD

        vec = self._vectorizer(len(texts))
        try:
            tfidf = vec.fit_transform(texts)
        except ValueError:  # no usable word at all (empty or stop-word-only texts)
            self.terms, self.idf = [], np.zeros(0, dtype=np.float32)
            self.components = np.zeros((2, 0), dtype=np.float32)
            self.tfidf = sparse.csr_matrix((len(texts), 0), dtype=np.float32)
            self._lookup = {}
            return np.zeros((len(texts), 2), dtype=np.float32)
        self.terms = list(vec.get_feature_names_out())
        self.idf = vec.idf_.astype(np.float32)
        self.tfidf = sparse.csr_matrix(tfidf, dtype=np.float32)
        self._lookup = {t: i for i, t in enumerate(self.terms)}
        dims = min(self.dims, tfidf.shape[1] - 1, tfidf.shape[0] - 1)
        if dims < 2:
            # Too little text for a latent space: the word space itself is the embedding.
            self.components = np.eye(tfidf.shape[1], dtype=np.float32)
            return _unit(np.asarray(tfidf.todense(), dtype=np.float32))
        svd = TruncatedSVD(n_components=dims, algorithm="randomized", n_iter=5, random_state=0)
        reduced = svd.fit_transform(tfidf)
        self.components = svd.components_.astype(np.float32)
        return _unit(reduced)

    def _weights(self, text: str) -> tuple[np.ndarray, np.ndarray] | None:
        """Unit TF-IDF vector of ``text`` as (term indices, weights); same maths as scikit-learn."""
        counts = Counter(i for tok in self.analyzer(text) if (i := self._lookup.get(tok)) is not None)
        if not counts:
            return None
        idx = np.fromiter(counts.keys(), dtype=np.int64)
        tf = np.fromiter((1.0 + math.log(c) for c in counts.values()), dtype=np.float32)
        weights = tf * self.idf[idx]
        norm = float(np.linalg.norm(weights))
        if norm == 0:
            return None
        return idx, (weights / norm).astype(np.float32)

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Project new texts into the latent space of the fitted model."""
        out = np.zeros((len(texts), self.components.shape[0]), dtype=np.float32)
        for row, text in enumerate(texts):
            w = self._weights(text)
            if w is not None:
                out[row] = self.components[:, w[0]] @ w[1]
        return _unit(out)

    def lexical(self, text: str) -> np.ndarray | None:
        """Cosine of ``text`` with every fitted document in plain TF-IDF space (exact words only)."""
        w = self._weights(text)
        if w is None or self.tfidf is None:
            return None
        q = sparse.csr_matrix((w[1], (np.zeros(len(w[0]), dtype=np.int64), w[0])), shape=(1, self.tfidf.shape[1]))
        return np.asarray((self.tfidf @ q.T).todense()).ravel().astype(np.float32)

    def known_terms(self, text: str) -> list[str]:
        return [t for t in dict.fromkeys(self.analyzer(text)) if t in self._lookup]

    # -- cache as plain arrays (no pickle)

    STATE_KEYS = ("terms", "idf", "components", "tfidf_data", "tfidf_indices", "tfidf_indptr", "tfidf_shape")

    def state(self) -> dict[str, np.ndarray]:
        tfidf = self.tfidf if self.tfidf is not None else sparse.csr_matrix((0, len(self.terms)), dtype=np.float32)
        return {
            "terms": np.array(self.terms, dtype=object).astype(str),
            "idf": self.idf,
            "components": self.components,
            "tfidf_data": tfidf.data,
            "tfidf_indices": tfidf.indices,
            "tfidf_indptr": tfidf.indptr,
            "tfidf_shape": np.array(tfidf.shape, dtype=np.int64),
        }

    def load_state(self, state: dict[str, np.ndarray]) -> None:
        self.terms = [str(t) for t in state["terms"]]
        self.idf = state["idf"].astype(np.float32)
        self.components = state["components"].astype(np.float32)
        self.tfidf = sparse.csr_matrix(
            (state["tfidf_data"], state["tfidf_indices"], state["tfidf_indptr"]),
            shape=tuple(int(x) for x in state["tfidf_shape"]),
        )
        self._lookup = {t: i for i, t in enumerate(self.terms)}


class FastEmbedEmbedder:
    """Sentence embeddings from a small local model (optional package ``fastembed``, ONNX on CPU).

    Unlike LSA this understands paraphrases ("grief" finds "mourning"), and with
    a multilingual model the query may be in another language than the labels.
    The model is downloaded once (67-220 MB) and runs offline afterwards.
    """

    DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
    MULTILINGUAL_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    def __init__(
        self,
        model: str | None = None,
        *,
        cache_dir: Path | str | None = None,
        batch_size: int = 256,
        offline: bool = False,
    ):
        self.model_name = model or self.DEFAULT_MODEL
        self.cache_dir = str(cache_dir) if cache_dir else None
        self.batch_size = batch_size
        #: Only use a model that is already in the cache (offline mode never touches the network).
        self.offline = offline
        self.name = "fastembed-" + "".join(c if c.isalnum() else "-" for c in self.model_name).strip("-").lower()
        self._model: object | None = None
        self._lock = threading.Lock()

    def _load(self):  # noqa: ANN202 - fastembed type
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding  # type: ignore[import-not-found]

                    extra = {"local_files_only": True} if self.offline else {}
                    self._model = TextEmbedding(model_name=self.model_name, cache_dir=self.cache_dir, **extra)
        return self._model

    def fit(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 2), dtype=np.float32)
        model = self._load()
        return _unit(np.asarray(list(model.passage_embed(list(texts), batch_size=self.batch_size)), dtype=np.float32))

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        model = self._load()
        return _unit(np.asarray(list(model.query_embed(list(texts))), dtype=np.float32))


def make_embedder(
    spec: str | None, *, model_dir: Path | None = None, offline: bool = False
) -> tuple[Embedder, str | None]:
    """Embedder for a setting such as ``lsa``, ``fastembed``, ``fastembed:multilingual`` or ``fastembed:<model>``.

    Returns the embedder and a note when the requested one is not available
    (the built-in LSA is used instead): the package is missing, or the model
    cannot be loaded - in ``offline`` mode only a model already in ``model_dir``
    is used, nothing is downloaded.
    """
    spec = (spec or "lsa").strip()
    kind, _, model = spec.partition(":")
    if kind.lower() in ("", "lsa"):
        return LsaEmbedder(), None
    if kind.lower() == "fastembed":
        try:
            import fastembed  # type: ignore[import-not-found]  # noqa: F401
        except ImportError:
            return LsaEmbedder(), (
                'ATLAS_EMBEDDER=fastembed needs the optional package: pip install "neuron-atlas-interface[embeddings]"; '
                "using the built-in LSA instead"
            )
        if model.lower() == "multilingual":
            model = FastEmbedEmbedder.MULTILINGUAL_MODEL
        embedder = FastEmbedEmbedder(model or None, cache_dir=model_dir, offline=offline)
        try:
            embedder._load()  # noqa: SLF001 - load now, so that a missing model falls back here and not mid-search
        except Exception as exc:  # noqa: BLE001 - any loader failure (no network, no cache, bad name)
            where = "is not in the local model cache (offline mode)" if offline else "could not be loaded"
            return LsaEmbedder(), (
                f"the embedding model {embedder.model_name} {where}: {type(exc).__name__}: {exc}; "
                "using the built-in LSA instead"
            )
        return embedder, None
    return LsaEmbedder(), f"unknown embedder {spec!r}; using the built-in LSA instead"


class SemanticIndex:
    """Vectors for every mode and pole of the catalog, with nearest-neighbour queries."""

    def __init__(self, catalog: Catalog, *, cache_dir: Path | None = None, embedder: Embedder | None = None):
        self.catalog = catalog
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.embedder: Embedder = embedder or LsaEmbedder()
        self.docs: list[Doc] = []
        self.vectors = np.zeros((0, 0), dtype=np.float32)
        self.pole_ids: list[str] = []
        self.pole_vectors = np.zeros((0, 0), dtype=np.float32)
        self._pole_row: dict[str, int] = {}
        self._doc_rows: dict[str, list[int]] = {}
        self._doc_pole = np.zeros(0, dtype=np.int64)  # text row -> pole row
        self._pole_tfidf: sparse.csr_matrix | None = None
        #: Weight of exact-word overlap where a word model is available (the rest is latent similarity).
        #: Latent similarity alone is noisy on short labels, so exact words lead.
        self.lexical_weight = 0.7
        self._ready = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ building

    def _collect(self) -> list[Doc]:
        docs: list[Doc] = []
        for p in self.catalog.iter_poles():
            if p.modes:
                total = sum(m.share or 0.0 for m in p.modes)
                for m in p.modes:
                    if m.label:
                        weight = (m.share or 0.0) / total if total > 0 else 1.0 / len(p.modes)
                        docs.append(Doc(p.pid, m.index, m.label, weight))
            elif p.description:
                docs.append(Doc(p.pid, None, p.description, 1.0))
        return docs

    def _fingerprint(self, docs: list[Doc]) -> str:
        h = hashlib.sha1(f"{CACHE_VERSION}|{self.embedder.name}|".encode())
        for d in docs:
            h.update(f"{d.pid}\x1f{d.mode_index}\x1f{d.text}\x1e".encode())
        return h.hexdigest()

    def ensure(self) -> SemanticIndex:
        if self._ready:
            return self
        with self._lock:
            if self._ready:
                return self
            docs = self._collect()
            fingerprint = self._fingerprint(docs)
            vectors = self._load_cache(fingerprint)
            if vectors is None:
                vectors = self.embedder.fit([d.text for d in docs]) if docs else np.zeros((0, 2), np.float32)
                self._save_cache(fingerprint, vectors)
            self.docs, self.vectors = docs, vectors
            rows: dict[str, list[int]] = {}
            for i, d in enumerate(docs):
                rows.setdefault(d.pid, []).append(i)
            self._doc_rows = rows
            self.pole_ids = list(rows)
            self._pole_row = {pid: i for i, pid in enumerate(self.pole_ids)}
            self._doc_pole = np.array([self._pole_row[d.pid] for d in docs], dtype=np.int64)
            pole_vectors = np.zeros((len(rows), vectors.shape[1] if vectors.size else 2), dtype=np.float32)
            for i, pid in enumerate(self.pole_ids):
                idx = rows[pid]
                weights = np.array([max(docs[j].weight, 1e-6) for j in idx], dtype=np.float32)
                pole_vectors[i] = (vectors[idx] * weights[:, None]).sum(axis=0)
            self.pole_vectors = _unit(pole_vectors)
            self._pole_tfidf = self._word_profiles(docs)
            self._ready = True
        return self

    def _word_profiles(self, docs: list[Doc]) -> sparse.csr_matrix | None:
        """Unit TF-IDF vector per pole (share-weighted sum over its texts); ``None`` without a word model."""
        tfidf = getattr(self.embedder, "tfidf", None)
        if tfidf is None or tfidf.shape[0] != len(docs) or tfidf.shape[1] == 0 or not docs:
            return None
        weights = np.array([max(d.weight, 1e-6) for d in docs], dtype=np.float32)
        gather = sparse.csr_matrix(
            (weights, (self._doc_pole, np.arange(len(docs)))), shape=(len(self.pole_ids), len(docs))
        )
        profiles = gather @ tfidf
        norms = np.sqrt(np.asarray(profiles.multiply(profiles).sum(axis=1)).ravel())
        norms[norms == 0] = 1.0
        return sparse.csr_matrix(sparse.diags(1.0 / norms) @ profiles, dtype=np.float32)

    def _cache_file(self) -> Path | None:
        return self.cache_dir / f"semantic-{self.embedder.name}.npz" if self.cache_dir else None

    def _load_cache(self, fingerprint: str) -> np.ndarray | None:
        path = self._cache_file()
        if path is None or not path.exists():
            return None
        try:
            with np.load(path, allow_pickle=False) as data:
                meta = json.loads(str(data["meta"]))
                if meta.get("fingerprint") != fingerprint:
                    return None
                if hasattr(self.embedder, "load_state"):  # models fitted here carry their state along
                    keys = getattr(self.embedder, "STATE_KEYS", ())
                    self.embedder.load_state({k: data[k] for k in keys})
                return data["vectors"].astype(np.float32)
        except Exception:  # noqa: BLE001 - a broken cache is simply rebuilt
            return None

    def _save_cache(self, fingerprint: str, vectors: np.ndarray) -> None:
        path = self._cache_file()
        if path is None:
            return
        state = self.embedder.state() if hasattr(self.embedder, "state") else {}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp.npz")
            np.savez(tmp, meta=np.array(json.dumps({"fingerprint": fingerprint})), vectors=vectors, **state)
            tmp.replace(path)
        except OSError:
            pass

    # ------------------------------------------------------------------ queries

    def query(self, text: str, top: int = 50) -> list[tuple[str, int | None, float, str]]:
        """Best matching mode per pole: ``(pid, mode_index, cosine, label)``, best first."""
        self.ensure()
        if not self.docs:
            return []
        q = self.embedder.embed([text])[0]
        if not np.any(q):
            return []
        scores = self.vectors @ q
        lexical = getattr(self.embedder, "lexical", None)
        exact = lexical(text) if lexical else None
        if exact is not None and exact.shape == scores.shape:
            # Latent similarity finds related wording; exact overlap keeps rare query words in charge.
            scores = self.lexical_weight * exact + (1.0 - self.lexical_weight) * scores
        return self._best_per_pole(scores, top)

    def _best_per_pole(
        self, scores: np.ndarray, top: int, skip: str | None = None
    ) -> list[tuple[str, int | None, float, str]]:
        order = np.argsort(-scores)
        seen: set[str] = set()
        out: list[tuple[str, int | None, float, str]] = []
        for i in order:
            d = self.docs[int(i)]
            if d.pid in seen or d.pid == skip:
                continue
            seen.add(d.pid)
            out.append((d.pid, d.mode_index, float(scores[i]), d.text))
            if len(out) >= top:
                break
        return out

    def _doc_scores(self, row: int) -> np.ndarray:
        """Similarity of one text to all texts (latent, blended with exact-word overlap where available)."""
        scores = self.vectors @ self.vectors[row]
        tfidf = getattr(self.embedder, "tfidf", None)
        if tfidf is not None and tfidf.shape[0] == len(self.docs) and tfidf.shape[1]:
            exact = np.asarray((tfidf @ tfidf[row].T).todense()).ravel().astype(np.float32)
            scores = self.lexical_weight * exact + (1.0 - self.lexical_weight) * scores
        return scores

    def similar_poles(self, pid: str, top: int = 20, max_modes: int = 8) -> list[tuple[str, float]]:
        """Poles that share meanings with ``pid``.

        With the built-in LSA embedder, poles are compared as wholes: the
        share-weighted word profile of their modes (exact words count most,
        the latent space adds related wording).  With a sentence-embedding
        model, modes are matched one to one instead: for each main mode of the
        pole the best-matching mode of every other pole is taken, and the
        matches are averaged with the modes' shares as weights.
        """
        self.ensure()
        own = self._pole_row.get(pid)
        if own is None:
            return []
        if self._pole_tfidf is not None:
            exact = np.asarray((self._pole_tfidf @ self._pole_tfidf[own].T).todense()).ravel()
            latent = self.pole_vectors @ self.pole_vectors[own]
            total = self.lexical_weight * exact + (1.0 - self.lexical_weight) * latent
        else:
            rows = sorted(self._doc_rows[pid], key=lambda r: -self.docs[r].weight)[:max_modes]
            weights = np.array([max(self.docs[r].weight, 1e-6) for r in rows], dtype=np.float32)
            weights /= weights.sum()
            total = np.zeros(len(self.pole_ids), dtype=np.float32)
            for row, weight in zip(rows, weights, strict=True):
                best = np.full(len(self.pole_ids), -1.0, dtype=np.float32)
                np.maximum.at(best, self._doc_pole, self._doc_scores(row))
                total += weight * best
        order = np.argsort(-total)
        return [(self.pole_ids[int(i)], float(total[i])) for i in order if int(i) != own][:top]

    def similar_modes(
        self, pid: str, mode_index: int | None, top: int = 20
    ) -> list[tuple[str, int | None, float, str]]:
        """Modes of *other* poles closest to one mode of ``pid`` (best mode per pole)."""
        self.ensure()
        rows = [i for i in self._doc_rows.get(pid, []) if self.docs[i].mode_index == mode_index]
        if not rows:
            return []
        return self._best_per_pole(self._doc_scores(rows[0]), top, skip=pid)

    def vector_of(self, pid: str) -> np.ndarray | None:
        self.ensure()
        row = self._pole_row.get(pid)
        return None if row is None else self.pole_vectors[row]

    def known_terms(self, text: str) -> list[str]:
        self.ensure()
        fn = getattr(self.embedder, "known_terms", None)
        return fn(text) if fn else []
