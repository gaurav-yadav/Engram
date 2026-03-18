# TODO

Product priorities for Engram as a developer augmentation tool:

- [x] Remove first-run friction with `auto-init`, hook setup, and clearer not-initialized guidance.
- [x] Add first-class document search so indexed repo docs are directly explorable.
- [x] Reduce CLI friction with repo inference from the current working tree where practical.
- [x] Add `sync` so memory and docs stay fresh after bootstrap.
- [x] Add write-side memory tools (`memory_store`, `memory_list`, `memory_delete`) so agents can learn in-session.
- [ ] Add incremental sync / reindex so refresh work can skip unchanged inputs.
- [ ] Improve retrieval quality with semantic search and path-aware ranking.
- [ ] Add rule authoring helpers (`rules add`, scope templates) instead of file-only authoring.
- [ ] Add freshness and trust controls for memories: stale markers, archive/delete, last-used timestamps.
- [ ] Improve `context` output further with better snippets, ranking rationale, and suggested next actions.
- [ ] Defer non-core surfaces like the HTTP service until activation and retrieval quality are strong.
