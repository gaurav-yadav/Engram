# Design: Semantic Search for Engram

**Status:** Draft
**Date:** 2026-03-17

---

## Problem

Engram's current search relies exclusively on SQLite FTS5, which performs keyword
(BM25) matching against the `memory_items_fts` and `documents_fts` virtual tables.
This works well when the user's query shares literal tokens with the stored content,
but fails on semantic mismatches. For example:

| Query | Stored title | FTS5 result |
|---|---|---|
| "how do we handle authentication" | "JWT token validation approach" | Miss -- zero keyword overlap |
| "database connection pooling" | "SQLAlchemy engine lifecycle and session management" | Miss |
| "error handling strategy" | "retry policy with exponential backoff" | Miss |

The `_safe_fts_query` helper in `query.py` tokenizes the query into quoted phrases
joined by implicit AND, which further narrows recall: every token must appear
somewhere in the FTS document. This is the right default for precision-oriented
keyword search, but it makes the gap wider for conceptual queries.

Semantic (vector) search closes this gap by comparing dense embeddings of the query
and stored content. Two texts that share meaning but no words will have high cosine
similarity in embedding space.

### Design constraints

- Engram is a local-first, zero-mandatory-network tool. The default embedding model
  must run entirely on-device.
- The dependency footprint matters. Engram currently has **zero runtime
  dependencies** (see `pyproject.toml` -- `dependencies = []`). Semantic search must
  be an optional extra.
- Existing FTS5 search must continue to work identically when the semantic extra is
  not installed. No degradation to the current path.

---

## Approach

Semantic search is added as an optional layer. When the `engram[semantic]` extra is
installed, embedding-based search is available alongside FTS5. When it is not
installed, all search paths fall back to pure FTS5 with no user-visible change.

### Embedding Model Selection

Three options were evaluated:

#### Option A: `all-MiniLM-L6-v2` via `sentence-transformers`

- **Size:** ~80 MB model weights, ~22M parameters.
- **Dimensions:** 384.
- **Latency:** ~5 ms per query on CPU (single sentence). Batch encoding of N
  documents is approximately linear.
- **Dependencies:** `sentence-transformers` pulls in `torch` (~150-800 MB depending
  on platform), `transformers`, `tokenizers`, `huggingface-hub`. Total install
  footprint is large but installation is a one-liner.
- **Quality:** Strong general-purpose sentence similarity. Top-5 on MTEB for models
  under 100 MB. Good coverage of code-adjacent English text (documentation,
  commit messages, memory item titles/bodies).
- **Maintenance:** Model is stable and widely used. No custom code needed beyond
  `model.encode()`.

#### Option B: Raw ONNX Runtime

- **Size:** ~30 MB ONNX export of the same MiniLM model.
- **Dimensions:** 384 (same model, different runtime).
- **Latency:** ~3 ms per query. Slightly faster than PyTorch path.
- **Dependencies:** `onnxruntime` (~15-40 MB) plus `tokenizers` for the tokenizer.
  Much lighter than full `torch`.
- **Quality:** Identical to Option A when using the same model weights.
- **Maintenance:** Requires manual tokenizer setup, manual mean-pooling, and ONNX
  model export/download management. More surface area for bugs. Need to handle
  model download, caching, and version pinning ourselves.

#### Option C: Cloud API embeddings (OpenAI / Anthropic)

- **Size:** No local model; requires network access.
- **Dimensions:** 1536 (OpenAI `text-embedding-3-small`) or 1024 (configurable).
- **Latency:** 50-200 ms per query (network round-trip).
- **Dependencies:** `httpx` or `openai` SDK. Lightweight.
- **Quality:** State-of-the-art on benchmarks. Higher dimensional space captures
  more nuance.
- **Maintenance:** Requires API key management, rate limiting, cost tracking.
  Breaks the local-first guarantee.

#### Recommendation

**Default: `all-MiniLM-L6-v2` via `sentence-transformers`.**

Rationale:

1. Zero-network operation is a core Engram principle. The default must work offline.
2. The `sentence-transformers` API is a single function call (`model.encode()`),
   minimizing implementation and maintenance cost.
3. 384 dimensions is small enough for pure-Python cosine similarity to be fast
   (sub-millisecond for thousands of vectors) and small enough that storage overhead
   is manageable.
4. The ONNX path (Option B) is a viable future optimization but adds maintenance
   burden that is not justified at this stage.
5. Cloud embeddings (Option C) should be supported as an **upgrade path** behind the
   existing `models.provider` / `models.base_url` config keys, not as the default.

The provider abstraction (see Search Integration below) makes it straightforward to
add ONNX or cloud backends later without changing the storage or fusion layers.

---

### Storage

#### Schema: `embeddings` table

A new migration (`1003_embeddings`) adds a single table:

```sql
CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,    -- 'memory_item' | 'document' | 'rule'
    target_id INTEGER NOT NULL,
    model_name TEXT NOT NULL,     -- e.g. 'all-MiniLM-L6-v2'
    embedding BLOB NOT NULL,      -- raw float32 bytes, 384 * 4 = 1536 bytes
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(target_type, target_id, model_name)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_target
    ON embeddings(target_type, target_id);
```

Design notes:

- **`target_type` + `target_id`** is a polymorphic foreign key. This avoids three
  separate tables while keeping the index narrow. Valid `target_type` values are
  `'memory_item'`, `'document'`, and `'rule'`.
- **`model_name`** is stored per-row so that re-embedding with a different model does
  not require a table rebuild. Rows with a stale model name are ignored during search
  and lazily re-embedded.
- **`embedding` is a BLOB** of raw little-endian `float32` values. For 384
  dimensions this is exactly 1,536 bytes per row. We store raw bytes rather than
  JSON/base64 to minimize storage and avoid serialization overhead.
- The `UNIQUE` constraint ensures one embedding per (target, model) pair. Upserts
  use `INSERT ... ON CONFLICT ... DO UPDATE`.

#### Optional `sqlite-vec` extension

[`sqlite-vec`](https://github.com/asg017/sqlite-vec) provides an approximate
nearest-neighbor (ANN) index inside SQLite. When available:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS vec_memory USING vec0(
    embedding float[384]
);
```

Queries become:

```sql
SELECT rowid, distance
FROM vec_memory
WHERE embedding MATCH ?
ORDER BY distance
LIMIT 20;
```

When `sqlite-vec` is **not** available, the system falls back to pure-Python cosine
similarity (see below). The extension is detected at runtime:

```python
def _has_sqlite_vec(conn: sqlite3.Connection) -> bool:
    try:
        conn.enable_load_extension(True)
        conn.load_extension("vec0")
        return True
    except (sqlite3.OperationalError, AttributeError):
        return False
```

#### Pure-Python cosine similarity fallback

When `sqlite-vec` is not available, vector search loads all embeddings for the
relevant `target_type` and `project_id` into memory and computes cosine similarity
in Python:

```python
import struct
import math

def _decode_embedding(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f'<{n}f', blob))

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
```

This is O(N * D) where N is the number of stored embeddings and D is the dimension.
For typical Engram usage (hundreds to low thousands of memory items per project),
this is well under 10 ms even in pure Python. numpy is not required.

#### Storage cost estimates

| Content type | Typical count per project | Bytes per embedding | Total |
|---|---|---|---|
| Memory items | 50-500 | 1,536 | 75 KB - 750 KB |
| Documents | 20-200 | 1,536 | 30 KB - 300 KB |
| Rules | 10-50 | 1,536 | 15 KB - 75 KB |
| **Total** | | | **~120 KB - 1.1 MB** |

Negligible relative to the SQLite database size (FTS indexes alone are typically
several MB). The `embedding` BLOB adds no measurable overhead to WAL checkpoint
or backup operations.

---

### Search Integration

#### Reciprocal Rank Fusion (RRF)

FTS5 and vector search each return ranked result lists. These are merged using
Reciprocal Rank Fusion, which is simple, parameter-light, and well-studied:

```
RRF_score(d) = sum over S in {fts, vec}: 1 / (k + rank_S(d))
```

where `k` is a constant (default 60, per the original RRF paper). If a document
appears in only one list, it still gets a score from that list.

Why RRF over alternatives:

- **Simpler than learned reranking.** No training data needed.
- **No score normalization required.** BM25 scores and cosine similarities are on
  incompatible scales. RRF uses only rank positions, sidestepping normalization.
- **Robust.** Works well even when one retriever returns poor results. The other
  retriever's ranking dominates.

#### New module: `src/engram/embeddings.py`

This module owns all embedding logic behind a provider abstraction:

```python
class EmbeddingProvider(Protocol):
    """Encode text into dense vectors."""
    @property
    def model_name(self) -> str: ...
    @property
    def dimensions(self) -> int: ...
    def encode(self, texts: list[str]) -> list[list[float]]: ...

class SentenceTransformerProvider:
    """Default local provider using sentence-transformers."""
    def __init__(self, model_name: str = "all-MiniLM-L6-v2",
                 cache_dir: Path | None = None) -> None: ...

class NoOpProvider:
    """Stub that returns empty vectors. Used when semantic deps missing."""
    ...
```

Key responsibilities:

- **Lazy model loading.** The `SentenceTransformerProvider` does not load the model
  at `__init__` time. The model is loaded on the first call to `encode()`. This
  avoids a ~1-2 second cold-start penalty on every Engram CLI invocation or MCP
  connection that may never issue a search.
- **Model caching.** Models are downloaded to `~/.engram/cache/models/` (the
  `cache` directory is already created by `config.ensure_global_layout()`). The
  `sentence-transformers` library respects a `cache_folder` parameter.
- **Embedding storage.** Helper functions to embed-and-store and to query the
  `embeddings` table.
- **Availability check.** A module-level function to detect whether the semantic
  extra is installed:

```python
def is_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False
```

#### Changes to `query.py`

The `search_memory` and `build_context` functions gain a hybrid search path:

```python
def search_memory(db, repo_root, query, kind=None, limit=10):
    project = _load_project_or_raise(db, repo_root)
    pid = int(project["id"])

    # FTS5 results (always available)
    fts_rows = db.search_memory(
        project_id=pid,
        query=_safe_fts_query(query),
        kind=kind,
        limit=limit * 2,  # over-fetch for fusion
    )

    # Vector results (when available)
    vec_rows = []
    if embeddings.is_available():
        provider = embeddings.get_provider()
        vec_rows = embeddings.search_memory_vectors(
            db, pid, provider, query, kind=kind, limit=limit * 2,
        )

    # Fuse or return FTS-only
    if vec_rows:
        fused = _rrf_fuse(fts_rows, vec_rows, limit=limit)
        return {"results": [_memory_row_to_dict(db, r) for r in fused], ...}
    else:
        return {"results": [_memory_row_to_dict(db, r) for r in fts_rows[:limit]], ...}
```

The `_rrf_fuse` function is a ~20-line pure-Python implementation of RRF that
operates on row IDs, returning the top-k merged results.

The same pattern applies to `search_documents` and `build_context`.

#### Embedding on write

`db.upsert_memory_item` and `db.upsert_document` currently maintain FTS indexes
inline. The embedding step is added as a post-write hook, **not** inline in the
database transaction:

1. The write transaction commits (FTS update happens synchronously as today).
2. If the embedding provider is available, `embeddings.embed_and_store()` is called
   outside the write transaction. This keeps the write path fast and avoids holding
   a long transaction while the model runs.
3. If embedding fails (model not loaded, OOM, etc.), the write still succeeds. The
   embedding is marked stale and will be computed on next search or background
   reindex.

#### Lazy loading sequence

```
CLI / MCP call
  -> query.search_memory()
    -> embeddings.is_available()  # fast import check, cached
    -> embeddings.get_provider()  # first call loads model (~1-2s)
    -> provider.encode([query])   # ~5ms
    -> cosine scan or sqlite-vec  # ~1-10ms
    -> _rrf_fuse(fts, vec)        # <1ms
```

After the first search, the model stays in memory for the lifetime of the process.
For the MCP server (long-lived), this means the cold-start cost is paid once. For
CLI one-shot commands, the ~1-2s load time is noticeable but acceptable; users who
want instant results can disable semantic search via config.

---

### Configuration

#### `pyproject.toml` optional dependencies

```toml
[project.optional-dependencies]
semantic = [
    "sentence-transformers>=2.2.0,<4",
    "torch>=2.0",
]
```

Installation:

```
pip install engram[semantic]
```

This keeps the base `engram` install at zero dependencies.

#### `config.yaml` settings

The existing `config.yaml` (generated by `config.ensure_default_global_config()`)
gains a new `semantic` section:

```yaml
semantic:
  enabled: true                          # master switch; false disables all vector search
  model: all-MiniLM-L6-v2               # sentence-transformers model name
  cache_dir: ~/.engram/cache/models      # where to store downloaded models
  provider: local                        # 'local' | 'openai' | 'custom'
  openai_model: text-embedding-3-small   # used when provider=openai
  sqlite_vec: auto                       # 'auto' | 'enabled' | 'disabled'
```

All keys have sensible defaults. The `enabled: true` default means semantic search
activates automatically when the extra is installed. Setting `enabled: false`
disables it even if the extra is present (useful for CI or constrained environments).

The `sqlite_vec: auto` setting means: try to load the extension, use it if
available, fall back to pure-Python otherwise.

#### Doctor check for semantic capability

A new optional check is added to `doctor.py`:

```python
DoctorCheck(
    name="semantic_search",
    ok=embeddings.is_available(),
    detail="sentence-transformers is installed"
           if embeddings.is_available()
           else "sentence-transformers not found; install engram[semantic] for vector search",
    required=False,
)
```

This surfaces as:

```
[PASS] semantic_search (optional): sentence-transformers is installed
```

or:

```
[WARN] semantic_search (optional): sentence-transformers not found; install engram[semantic] for vector search
```

---

### Implementation Plan

#### Phase 1: Embedding infrastructure (3-5 days)

| Step | Files | Work |
|---|---|---|
| 1. Add `embeddings` migration | `src/engram/db.py` | Add `1003_embeddings` to `SQLITE_MIGRATIONS`. Create `embeddings` table with schema above. |
| 2. Create `embeddings.py` module | `src/engram/embeddings.py` (new) | `EmbeddingProvider` protocol, `SentenceTransformerProvider`, `NoOpProvider`, `is_available()`, `get_provider()`, `embed_and_store()`, `search_vectors()`, pure-Python cosine fallback, BLOB encode/decode. |
| 3. Add optional dependency | `pyproject.toml` | Add `[project.optional-dependencies]` semantic group. |
| 4. Model cache directory | `src/engram/config.py` | Add `model_cache_dir()` returning `~/.engram/cache/models/`. Ensure it is created by `ensure_global_layout()`. |
| 5. Unit tests for embeddings module | `tests/test_embeddings.py` (new) | Test encode/decode BLOB round-trip, cosine similarity math, `NoOpProvider` behavior, `is_available()` with mocked imports. |

#### Phase 2: Write-path integration (2-3 days)

| Step | Files | Work |
|---|---|---|
| 6. Embed on memory upsert | `src/engram/db.py` | After `upsert_memory_item` commits, call `embeddings.embed_and_store()` if provider is available. Same for `upsert_document`. |
| 7. Backfill command | `src/engram/cli.py` | Add `engram reindex --embeddings` subcommand that iterates all memory items and documents for a project and computes missing embeddings. |
| 8. Handle model changes | `src/engram/embeddings.py` | On search, if stored `model_name` does not match the active provider's model name, exclude the row and mark it for re-embedding. |

#### Phase 3: Search fusion (2-3 days)

| Step | Files | Work |
|---|---|---|
| 9. RRF fusion function | `src/engram/embeddings.py` | Implement `rrf_fuse(fts_results, vec_results, k=60, limit=10)`. Operates on row IDs, returns merged ranked list. |
| 10. Hybrid search in `query.py` | `src/engram/query.py` | Modify `search_memory()` and `build_context()` to call vector search when available and fuse with FTS5 results. |
| 11. MCP tool update | `src/engram/mcp.py` | No schema changes needed. The `memory_search` and `context_build` tools automatically benefit since they delegate to `query.py`. |

#### Phase 4: Configuration and polish (1-2 days)

| Step | Files | Work |
|---|---|---|
| 12. Config schema update | `src/engram/config.py` | Add `semantic` section to default config template. Add reader functions for semantic config values. |
| 13. Doctor check | `src/engram/doctor.py` | Add optional `semantic_search` check. |
| 14. `sqlite-vec` detection | `src/engram/embeddings.py` | Probe for `vec0` extension at startup. Use it for search if available, fall back to pure-Python scan. |
| 15. Integration tests | `tests/test_search_integration.py` (new) | End-to-end tests: insert memory items, search with semantic query that has no keyword overlap, verify results are returned. Test graceful degradation when `sentence-transformers` is not installed. |
| 16. Config documentation | Update `config.yaml` comments | Document all `semantic.*` keys with inline comments. |

#### Total estimated effort: 8-13 days

The phases can overlap. Phase 1 is a prerequisite for all others. Phases 2 and 3 can
be developed in parallel. Phase 4 depends on phases 2 and 3.

---

### Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| `torch` install size (~800 MB on some platforms) deters adoption | Medium | Document clearly that this is optional. Investigate ONNX backend (Option B) as a follow-up to reduce footprint. |
| Cold-start latency (~1-2s) on first search | Low | Lazy loading means it only happens once per process. CLI users can disable via config. MCP server is long-lived. |
| `sqlite-vec` not available on all platforms | Low | Pure-Python fallback is the default. `sqlite-vec` is a performance optimization, not a requirement. |
| Embeddings drift when model is upgraded | Low | `model_name` column tracks which model produced each embedding. Stale rows are re-embedded on access or via `reindex`. |
| Memory usage from loaded model (~200 MB resident) | Low | Acceptable for developer workstations. Model is only loaded when semantic search is both installed and enabled. |

### Future work

- **ONNX runtime provider** to cut install size from ~800 MB to ~50 MB.
- **Cloud embedding provider** behind `models.provider` config for users who prefer
  higher-quality embeddings and have network access.
- **Incremental reindex** that processes only items modified since last embedding.
- **Reranking** pass using a cross-encoder model for top-k results, behind the
  existing `models.rerank_model` config key.
- **Chunk-level embeddings** for long documents, with passage-level retrieval.
