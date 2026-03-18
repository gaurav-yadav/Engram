# Design: Richer Memory Extraction in Engram

**Status:** Proposal
**Date:** 2026-03-17
**Author:** --

## Background

Engram currently extracts two kinds of memory from Claude Code session archives:

- **command** -- Bash commands run via `assistant_tool_use` events with `tool_name=Bash`, promoted when they appear frequently or match `USEFUL_COMMAND_HINTS` patterns. Implemented in `_promote_command_memories`.
- **preference** -- User-stated preferences extracted from `user_text` events by matching against `PREFERENCE_HINTS` phrase patterns. Implemented in `_promote_preference_memories`.

Both follow the same promoter pattern defined in `src/engram/claude.py`:

1. Query `archive_events` joined to `archive_sessions` for a given `project_id`.
2. Parse `content_json` or `content_text` to extract candidates.
3. Count occurrences, normalize, and filter through heuristics (`_is_useful_command`, `_is_useful_preference`).
4. Delete all existing memory items of that kind for the project+scope, then bulk-insert promoted items via `db.upsert_memory_item`.
5. Attach provenance via `db.replace_memory_provenance` linking back to specific `archive_session_id` / `archive_event_id` rows.

Each memory item carries `confidence`, `importance`, and `stability` scores (floats 0..1), a `source_key` for dedup (SHA-1 of normalized content), and is indexed in the `memory_items_fts` FTS5 table.

This document proposes two approaches for extracting richer memory kinds beyond commands and preferences, then recommends a hybrid strategy.

---

## Approach A: Pattern-Based Extraction (No LLM)

Five new memory kinds, each following the existing promoter pattern. All extraction runs during `import_claude_history` after archive ingestion, operating over the `archive_events` table. No external API calls required.

### A.1 `lesson` -- Insights and Learnings

Captures moments where the user or assistant states a realized truth: a root cause discovered, a misconception corrected, or a technique learned.

**Signal patterns (case-insensitive substring/regex on `content_text`):**

```
user_text, assistant_text events:
  "turns out"
  "the fix was"
  "the issue was"
  "root cause was"
  "root cause is"
  "the problem was"
  "the problem is"
  "i learned"
  "we learned"
  "lesson learned"
  "TIL "
  "key insight"
  "the trick is"
  "the trick was"
  "what worked"
  "what actually worked"
  "gotcha:"
  "pitfall:"
```

**Extraction strategy:**

- Scan `archive_events` where `event_type IN ('user_text', 'assistant_text')` and `content_text` matches any signal pattern.
- Extract the sentence or paragraph containing the signal phrase. Use sentence boundary splitting (`re.split(r'(?<=[.!?])\s+', text)`) and take the matching sentence plus one sentence of preceding context.
- Normalize via `_normalize_text` for dedup key.
- `source_key`: `lesson:{sha1(normalized)}`.

**Dedup approach:**

- Normalize whitespace and case, strip trailing punctuation.
- Group by SHA-1 of normalized text. If two lessons from different sessions have identical normalized text, merge into one with the higher occurrence count.
- Near-duplicate detection: compute 3-gram Jaccard similarity between candidate pairs within the same project. Suppress candidates with Jaccard > 0.7 against an already-promoted lesson, keeping the one with more occurrences.

**Filtering heuristics:**

- Minimum length: 30 characters (after normalization).
- Maximum length: 300 characters. Truncate to the first 300 characters at a sentence boundary if longer.
- Discard candidates that are questions (contain `?` and no declarative signal phrase after the `?`).
- Discard candidates where the signal phrase appears inside a code block (``` fencing).
- Require at least one occurrence from a `user_text` event OR at least two occurrences from `assistant_text` events (user-stated lessons are higher signal).

**Confidence scoring:**

```python
confidence = min(0.95, 0.50 + count * 0.10 + (0.10 if from_user else 0.0))
importance = min(0.95, 0.55 + count * 0.08)
stability  = min(0.95, 0.60 + count * 0.06)
```

**Effort estimate:** ~120 lines of Python. 1-2 days including tests.

---

### A.2 `arch_decision` -- Architectural and Design Decisions

Captures declarative statements about technology choices, constraints, and design rationale.

**Signal patterns:**

```
user_text, assistant_text events:
  "we chose"
  "we decided"
  "we're using"
  "we use"
  "we went with"
  "instead of"
  "rather than"
  "stdlib only"
  "no dependencies"
  "zero dependencies"
  "because it"
  "because we"
  "the architecture"
  "design decision"
  "convention is"
  "convention:"
  "pattern:"
  "stack:"
  "tech stack"
  "monorepo"
  "microservice"
```

Compound signal: require at least one "rationale" token (`because`, `since`, `so that`, `to avoid`, `instead of`, `rather than`) within 200 characters of the primary signal. This filters out passing mentions ("we use pytest" with no rationale) from actual decisions ("we use pytest because it handles fixtures better than unittest").

**Extraction strategy:**

- Same scan approach as `lesson`, but with compound signal requirement.
- Extract the full sentence containing the primary signal plus up to two following sentences if they contain rationale tokens.
- Title: first 80 characters. Body: full extracted text.

**Dedup approach:**

- Same SHA-1 normalization as `lesson`.
- Additional semantic dedup: if two candidates mention the same tool/library name (extracted via simple noun-after-signal heuristic) with the same rationale verb, keep only the more complete one.

**Filtering heuristics:**

- Minimum length: 40 characters.
- Maximum length: 400 characters.
- Discard if the statement is negated and immediately contradicted (e.g., "we chose X... actually no, we went with Y" -- keep only the final decision by preferring the later timestamp).
- Discard candidates from `assistant_text` unless they are restating something the user said (check for echo pattern: same decision text appearing in both `user_text` and `assistant_text` within the same session).

**Confidence scoring:**

```python
confidence = min(0.95, 0.55 + count * 0.10 + (0.10 if has_rationale else 0.0))
importance = min(0.95, 0.65 + count * 0.06)
stability  = min(0.95, 0.70 + count * 0.04)  # decisions tend to be stable
```

**Effort estimate:** ~150 lines of Python. 2 days including tests.

---

### A.3 `tool_workflow` -- Repeated Tool Sequences

Detects recurring patterns in tool usage sequences, capturing the implicit workflows the user and assistant follow repeatedly (e.g., Read -> Edit -> Bash for the edit-test cycle).

**Extraction strategy:**

- Build a sequence of `tool_name` values from `assistant_tool_use` events per session, ordered by `event_index`.
- Run n-gram analysis (n=2, 3, 4) over the tool-name sequence for each session.
- Aggregate n-gram counts across all sessions for the project.
- Promote n-grams that appear in >= 3 distinct sessions.

Example output:

```
tool_workflow: "Read -> Edit -> Bash" (seen in 12/15 sessions)
tool_workflow: "Glob -> Read -> Edit" (seen in 8/15 sessions)
tool_workflow: "Bash -> Read -> Edit -> Bash" (seen in 6/15 sessions)
```

**Signal patterns:**

No text patterns. Signal is purely statistical: n-gram frequency across sessions.

**Dedup approach:**

- Subsumption: if trigram `A -> B -> C` is promoted and bigram `A -> B` always appears as a prefix of the trigram, suppress the bigram.
- Normalize tool names to lowercase.

**Filtering heuristics:**

- Minimum distinct sessions: 3.
- Minimum total occurrences: 5.
- Maximum n-gram length: 5 (longer sequences fragment into noise).
- Suppress tool-name sequences that are entirely `Read` (pure browsing, not a workflow).
- Suppress sequences where all tools are the same (e.g., `Bash -> Bash -> Bash` -- just repeated commands, not a workflow pattern).

**Confidence scoring:**

```python
session_ratio = distinct_sessions / total_sessions
confidence = min(0.95, 0.40 + session_ratio * 0.50)
importance = min(0.95, 0.40 + session_ratio * 0.40 + total_count * 0.01)
stability  = min(0.95, 0.50 + session_ratio * 0.40)
```

**Effort estimate:** ~100 lines of Python. 1 day including tests.

---

### A.4 `debug_pattern` -- Error-Investigation-Fix Sequences

Captures recurring debugging patterns: an error appears in tool output, investigation steps follow, and a resolution is reached.

**Extraction strategy:**

- Identify "error events": `assistant_tool_use` events where `tool_name = 'Bash'` and the subsequent `user_tool_result` (matched by `parent_uuid` / event ordering) contains error signals: `error:`, `Error:`, `FAILED`, `traceback`, `exception`, non-zero exit codes, `command not found`, `No such file`.
- Define an event window: from the error event to the next successful Bash execution (exit 0, no error signals) or end of session, up to a maximum of 20 events.
- Extract the error signature (first line of error text, normalized) and the fix signature (the Bash command or Edit content of the final successful step).
- Group by normalized error signature across sessions. Promote error-fix pairs that appear in >= 2 sessions.

**Signal patterns (in tool result content):**

```
r"(?i)(error|exception|traceback|FAILED|FAILURE|panic|fatal)"
r"(?i)(command not found|no such file|permission denied)"
r"exit code [1-9]"
r"CalledProcessError"
```

**Dedup approach:**

- Normalize error signatures: strip file paths (replace `/path/to/...` with `<path>`), strip line numbers, strip timestamps.
- Group by normalized error signature. Within a group, keep the fix that appears most frequently.

**Filtering heuristics:**

- Error window must contain at least 2 events and at most 20.
- The fix event must exist (discard unresolved error windows).
- Discard error signatures shorter than 15 characters (too generic).
- Discard if the error is a simple typo correction (edit distance between error-causing command and fix command <= 3 characters).

**Confidence scoring:**

```python
confidence = min(0.95, 0.45 + count * 0.12)
importance = min(0.95, 0.55 + count * 0.08)
stability  = min(0.95, 0.50 + count * 0.08)
```

**Memory body format:**

```
Error: <normalized error signature>
Fix: <fix command or description>
Sessions: <count>
```

**Effort estimate:** ~200 lines of Python. 2-3 days including tests. This is the most complex pattern-based extractor due to the event-window logic.

---

### A.5 `code_pattern` -- Recurring Code Structures

Detects recurring code patterns from file-write tool usage: common import blocks, test structure templates, error handling idioms.

**Extraction strategy:**

- Scan `assistant_tool_use` events where `tool_name IN ('Edit', 'Write')`.
- Parse `content_json` to extract the `new_string` (for Edit) or `content` (for Write) field.
- Extract structural fragments:
  - **Import blocks:** consecutive lines starting with `import`, `from ... import`, `require(`, `use `, `#include`. Group by exact text.
  - **Test structures:** function/method signatures matching `def test_`, `it(`, `describe(`, `#[test]`, `@Test`. Extract the function signature plus the first 3 lines of body.
  - **Error handling:** `try/except`, `try/catch`, `.catch(`, `if err != nil`, `rescue`. Extract the full try/catch block up to 10 lines.
- Count occurrences of each normalized fragment across sessions.

**Dedup approach:**

- Normalize whitespace within code fragments but preserve structure (newlines matter in code).
- Hash the normalized fragment for `source_key`.
- Suppress fragments that are strict substrings of a longer promoted fragment.

**Filtering heuristics:**

- Minimum occurrences: 3.
- Minimum fragment length: 2 lines.
- Maximum fragment length: 15 lines.
- Discard fragments that are standard boilerplate with no project-specific content (e.g., bare `import os`). Maintain a small blocklist of trivially common single imports.
- Only promote if the fragment appears in >= 2 distinct sessions.

**Confidence scoring:**

```python
confidence = min(0.95, 0.40 + count * 0.08 + (0.10 if multi_session else 0.0))
importance = min(0.90, 0.40 + count * 0.06)
stability  = min(0.95, 0.55 + count * 0.06)
```

**Effort estimate:** ~180 lines of Python. 2 days including tests.

---

### Approach A Summary

| Kind            | Source events          | Signal type          | Dedup strategy     | Effort  |
|-----------------|----------------------|----------------------|--------------------|---------|
| `lesson`        | user/assistant text  | Keyword phrases      | SHA-1 + Jaccard    | 1-2 days|
| `arch_decision` | user/assistant text  | Compound phrases     | SHA-1 + semantic   | 2 days  |
| `tool_workflow`  | tool_use sequences   | N-gram frequency     | Subsumption        | 1 day   |
| `debug_pattern` | tool_use + results   | Error/fix windows    | Error sig grouping | 2-3 days|
| `code_pattern`  | Edit/Write content   | Structural fragments | Substring + hash   | 2 days  |
| **Total**       |                      |                      |                    | **8-10 days** |

---

## Approach B: LLM-Powered Extraction (Recommended)

Instead of building increasingly complex regex-based extractors, leverage Claude Code itself -- the agent already understands the conversation semantics. This approach treats memory extraction as a tool-use and instruction problem rather than a parsing problem.

### B.1 `memory_store` MCP Tool

Add a new MCP tool that Claude Code can call to persist a memory during a conversation.

**Tool definition:**

```python
"memory_store": (
    lambda arguments: _with_db(_memory_store_tool, arguments),
    {
        "description": (
            "Store a memory about this project. Use this to save notable insights, "
            "decisions, patterns, and lessons learned during the conversation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Absolute path to the repo root"},
                "kind": {
                    "type": "string",
                    "enum": [
                        "lesson", "arch_decision", "tool_workflow",
                        "debug_pattern", "code_pattern", "preference",
                        "command", "note"
                    ],
                    "description": "Category of memory"
                },
                "title": {
                    "type": "string",
                    "description": "Short title (max 80 chars)",
                    "maxLength": 80
                },
                "body": {
                    "type": "string",
                    "description": "Full memory content with context",
                    "maxLength": 2000
                },
                "source_context": {
                    "type": "string",
                    "description": "Optional: relevant code snippet, error message, or conversation excerpt",
                    "maxLength": 500
                }
            },
            "required": ["repo", "kind", "title", "body"],
            "additionalProperties": False,
        },
    },
),
```

**Backend handler:**

```python
def _memory_store_tool(db: Database, arguments: dict[str, Any]) -> dict[str, Any]:
    repo = Path(arguments["repo"]).expanduser().resolve()
    project = _load_project_or_raise(db, repo)
    project_id = int(project["id"])
    scope_id = db.ensure_scope(project_id, "repo", str(repo))

    kind = arguments["kind"]
    title = arguments["title"][:80]
    body = arguments["body"][:2000]
    source_context = arguments.get("source_context", "")[:500]

    source_key = f"llm:{hashlib.sha1((kind + ':' + title + ':' + body).encode()).hexdigest()}"

    memory_id = db.upsert_memory_item(
        project_id=project_id,
        scope_id=scope_id,
        kind=kind,
        title=title,
        body=body,
        source_key=source_key,
        confidence=0.80,  # LLM-extracted memories start with higher confidence
        importance=0.70,
        stability=0.60,
    )

    if source_context:
        db.replace_memory_provenance(memory_id, [(None, None, None, source_context)])

    return {"stored": True, "memory_id": memory_id, "kind": kind, "title": title}
```

**Effort estimate:** ~50 lines of Python in `mcp.py`. Half a day.

### B.2 `memory_list` and `memory_delete` Tools

Allow Claude Code to manage memories: list what is already stored (to avoid duplicates) and delete outdated or incorrect entries.

**`memory_list`:**

```python
"memory_list": (
    lambda arguments: _with_db(_memory_list_tool, arguments),
    {
        "description": "List stored memories for a project, optionally filtered by kind.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "kind": {"type": "string"},
            },
            "required": ["repo"],
            "additionalProperties": False,
        },
    },
),
```

**`memory_delete`:**

```python
"memory_delete": (
    lambda arguments: _with_db(_memory_delete_tool, arguments),
    {
        "description": "Delete a stored memory by ID. Use when a memory is outdated or incorrect.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "memory_id": {"type": "integer"},
            },
            "required": ["repo", "memory_id"],
            "additionalProperties": False,
        },
    },
),
```

The `memory_delete` handler should verify the memory belongs to the specified project before deleting to prevent cross-project deletion.

**Effort estimate:** ~60 lines of Python. Half a day.

### B.3 Instruction-Driven Extraction

Add instructions in `AGENTS.md` or the system prompt (via Engram rules) that tell Claude Code when and what to store as memories.

**Example AGENTS.md instruction block:**

```markdown
## Memory

When working on this project, store notable memories using the `memory_store` tool:

- **lesson**: When you discover a root cause, a non-obvious fix, or a "gotcha" that
  would save time in future sessions.
- **arch_decision**: When a design choice is made with rationale (e.g., "use X instead
  of Y because Z").
- **debug_pattern**: When you resolve a recurring error -- store the error signature
  and the fix.
- **code_pattern**: When you notice or establish a recurring code structure the project
  follows.
- **preference**: When the user states a preference about coding style, tooling, or
  workflow.

Before storing, call `memory_list` to check if a similar memory already exists.
Do not store trivial or ephemeral information.
```

This approach works today with zero Engram code changes beyond the tools in B.1/B.2. The quality of extraction depends on the instruction clarity and the model's judgment.

**Effort estimate:** Documentation only (assuming B.1 and B.2 are implemented). A few hours.

### B.4 Hook-Driven Extraction

Use Claude Code's hook system (`Stop` or `SessionEnd` hooks) to trigger a reflection pass at the end of a conversation.

**Mechanism:**

The hook invokes a small script or Claude Code subagent that:

1. Reads the current session transcript (or a summary of it).
2. Prompts Claude to identify extractable memories.
3. Calls `memory_store` for each identified memory.

**Example hook configuration (`.claude/settings.json`):**

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "command": "engram extract-session --repo $CWD --session $SESSION_ID"
      }
    ]
  }
}
```

The `engram extract-session` command would:

1. Load the session events from the archive.
2. Build a prompt summarizing the session.
3. Call the Claude API (or use a local model) to extract structured memories.
4. Insert them via the same `db.upsert_memory_item` path.

**Prompt template for extraction:**

```
Given the following conversation transcript from a coding session, extract
any notable memories. For each memory, provide:
- kind: one of lesson, arch_decision, debug_pattern, code_pattern, preference
- title: concise title (max 80 chars)
- body: full description with context (max 2000 chars)
- source_context: relevant excerpt from the conversation

Only extract memories that would be valuable in future sessions. Do not
extract trivial or ephemeral information.

Respond as a JSON array of memory objects.

Transcript:
{transcript}
```

**Effort estimate:** ~150 lines of Python for the CLI command + prompt template. 2-3 days, mostly in prompt engineering and testing.

### B.5 Hybrid Approach

Combine pattern-based extraction for high-precision/low-cost signals with LLM-powered extraction for semantic understanding.

**Pattern-based (automated baseline):**

- Keep existing `command` and `preference` extractors unchanged. They are cheap, fast, and already working.
- Add `tool_workflow` (A.3) since it is purely statistical and requires no NLP.

**LLM-powered (semantic layer):**

- Implement `memory_store`, `memory_list`, `memory_delete` MCP tools (B.1, B.2).
- Add instruction-driven extraction (B.3) for real-time capture during conversations.
- Add hook-driven extraction (B.4) as a session-end reflection pass for anything missed during the conversation.

This gives three extraction tiers:

| Tier | Mechanism | When it runs | What it captures | Cost |
|------|-----------|-------------|------------------|------|
| 1 - Automated | Pattern matching | `engram init` / import | commands, preferences, tool_workflows | Zero (local CPU) |
| 2 - Real-time | LLM tool calls | During conversation | lessons, decisions, patterns | Per-call (part of existing conversation token budget) |
| 3 - Reflective | Hook + LLM | Session end | Anything missed by tiers 1-2 | ~2-5K tokens per session |

---

## Approach Comparison

| Dimension | A: Pattern-Only | B: LLM-Only | Hybrid (Recommended) |
|-----------|----------------|-------------|---------------------|
| **Quality** | Low-medium. Regex catches surface patterns but misses nuance. "The fix was to restart Docker" matches the signal but "After investigating for an hour, we realized the container was using a stale image" does not. | High. The LLM understands semantics, context, and can judge importance. | High. LLM handles nuance; patterns handle volume. |
| **Precision** | High for simple patterns, degrades for complex kinds (`debug_pattern`, `code_pattern`). | High. Can be instructed to avoid false positives. | High across all kinds. |
| **Recall** | Low. Only captures memories matching predefined phrase lists. Every novel phrasing is missed. | Medium-high. Depends on instruction quality and model attention. Real-time extraction may miss items if the model is focused on the primary task. Hook pass catches stragglers. | High. Three tiers provide defense in depth. |
| **Cost** | Zero marginal cost. All processing is local. | Tier 2 is free (uses existing conversation tokens). Tier 3 costs ~2-5K tokens per session for the reflection pass. | Low. Pattern extraction is free. Tier 2 is free. Tier 3 is ~$0.01-0.03 per session at current API pricing. |
| **Latency** | Milliseconds per session during import. | Tier 2: zero added latency (tool calls happen naturally in conversation flow). Tier 3: 2-5 seconds at session end (async, non-blocking). | Negligible. |
| **Complexity** | 8-10 days of implementation. Each new kind requires custom parsing logic, heuristics, and tuning. The `debug_pattern` extractor alone requires event-window logic with edge cases. | 3-4 days of implementation. Most complexity is in prompt engineering, not code. | 4-5 days of implementation. Reuses existing pattern extractors, adds tools and hooks. |
| **Maintenance** | High. Patterns are brittle. New phrasings, new tool names, new languages all require manual updates. Every false positive or missed pattern requires code changes. | Low. Prompt updates are cheaper than code changes. Model improvements automatically improve extraction quality. | Low. Pattern-based extractors are simple and stable (commands, preferences, tool sequences). LLM handles the long tail. |
| **Determinism** | Fully deterministic. Same input always produces same output. | Non-deterministic. Same session may produce slightly different memories on re-extraction. | Mixed. Tier 1 is deterministic. Tiers 2-3 are non-deterministic but idempotent via `source_key` dedup. |
| **Offline** | Works fully offline. | Requires API access (or local model). | Tier 1 works offline. Tiers 2-3 require a model. |

---

## Recommendation: Hybrid Approach

The hybrid approach provides the best quality-to-effort ratio. Pattern-based extraction handles the well-structured, high-volume signals (commands, preferences, tool sequences) where regex is sufficient and LLM calls would be wasteful. LLM-powered extraction handles the semantic, context-dependent signals (lessons, decisions, debug patterns) where pattern matching is inadequate.

### Implementation Plan

#### Phase 1: MCP Write Tools (Week 1)

**Goal:** Enable Claude Code to store memories during conversations.

1. **Add `memory_store` tool to `mcp.py`** (B.1)
   - Implement handler with validation, dedup via `source_key`, and provenance tracking.
   - Add `note` as a catch-all kind for memories that do not fit existing categories.
   - Wire into the existing `TOOLS` dict.

2. **Add `memory_list` tool to `mcp.py`** (B.2)
   - Return memories grouped by kind, with ID, title, and truncated body.
   - Support optional `kind` filter.

3. **Add `memory_delete` tool to `mcp.py`** (B.2)
   - Verify project ownership before deletion.
   - Clean up FTS index and provenance rows.

4. **Update `ImportResult` model**
   - Add fields for LLM-extracted memory counts.

**Deliverables:** Three new MCP tools, passing tests, updated tool list in MCP server.

#### Phase 2: Instruction-Driven Extraction (Week 1-2)

**Goal:** Make Claude Code aware it should store memories.

1. **Write default AGENTS.md instructions** (B.3)
   - Document when to store each memory kind.
   - Include examples of good and bad memories.
   - Add "check before storing" guidance to prevent duplicates.

2. **Add a default Engram rule** that injects memory-extraction instructions
   - Ship as a rule file in `~/.engram/rules/memory-extraction.md`.
   - The rule is loaded by the existing `rules_show` / `context_build` tools and surfaced to Claude Code.

**Deliverables:** Instruction templates, default rule file.

#### Phase 3: `tool_workflow` Pattern Extractor (Week 2)

**Goal:** Capture tool-use sequences automatically.

1. **Implement `_promote_tool_workflow_memories`** in `claude.py` (A.3)
   - N-gram analysis over tool-name sequences per session.
   - Cross-session aggregation and subsumption filtering.
   - Follow the existing promoter pattern.

2. **Wire into `import_claude_history`**
   - Call after the existing command and preference promoters.
   - Update `ImportResult` with `tool_workflow_memories_added`.

3. **Update `summary.py`** to include tool workflows in summaries.

**Deliverables:** New promoter function, updated import pipeline, summary output.

#### Phase 4: Hook-Driven Reflection (Week 3)

**Goal:** Extract memories from completed sessions that were not captured in real time.

1. **Implement `engram extract-session` CLI command** (B.4)
   - Load session events from archive.
   - Build a condensed transcript (tool calls summarized, full user/assistant text).
   - Call Claude API with extraction prompt.
   - Parse structured response and insert via `db.upsert_memory_item`.
   - Dedup against existing memories using `memory_list` before inserting.

2. **Document hook configuration**
   - Provide example `.claude/settings.json` for Stop hook.
   - Support `--dry-run` flag for testing extraction without persistence.

3. **Add transcript condensation logic**
   - Compress tool_use events to `[Tool: Edit src/foo.py]` summaries.
   - Keep full text for user messages, assistant text, and error outputs.
   - Target: fit a session into ~4K tokens for the extraction prompt.

**Deliverables:** CLI command, prompt template, hook documentation.

#### Phase 5: Consolidation and Quality (Week 4)

**Goal:** Ensure memory quality and prevent unbounded growth.

1. **Memory decay and garbage collection**
   - Add `last_accessed_at` column to `memory_items`.
   - Update read paths (`search_memory`, `context_build`) to touch `last_accessed_at`.
   - Add `engram gc` command that archives memories not accessed in N days with low confidence.

2. **Duplicate detection across tiers**
   - Before inserting an LLM-extracted memory, FTS-search existing memories.
   - If a match with BM25 rank above threshold exists, skip or merge.

3. **Memory quality metrics**
   - Track extraction source (pattern vs. real-time-llm vs. hook-llm) in a new `source_type` column.
   - Add `engram stats` output showing memory counts by kind and source type.
   - Log extraction events for debugging.

4. **Testing**
   - Unit tests for each extractor.
   - Integration test: ingest a synthetic session, run all extractors, verify memory contents.
   - Golden-file tests for prompt-based extraction: fixed input transcript, expected memory output.

**Deliverables:** GC command, dedup logic, metrics, test suite.

---

### Schema Changes

Add to the `memory_items` table:

```sql
ALTER TABLE memory_items ADD COLUMN source_type TEXT NOT NULL DEFAULT 'pattern';
-- values: 'pattern', 'llm_realtime', 'llm_hook', 'manual'

ALTER TABLE memory_items ADD COLUMN last_accessed_at TEXT;
```

Add a new migration in `db.py`:

```python
(
    "0002_memory_source_type",
    """
    ALTER TABLE memory_items ADD COLUMN source_type TEXT NOT NULL DEFAULT 'pattern';
    ALTER TABLE memory_items ADD COLUMN last_accessed_at TEXT;
    """,
),
```

No changes to the `memory_items_fts` virtual table (FTS5 does not support ALTER).

---

### Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| LLM extracts low-quality memories | Default confidence for LLM memories starts at 0.80, decays if not accessed. GC cleans up. Instructions include negative examples. |
| Memory table grows unbounded | Per-project cap of 200 memories. GC archives low-confidence, stale items. Pattern-based extractors already cap at 50 (commands) and 30 (preferences). |
| Hook adds latency to session end | Hook runs asynchronously. Extraction is not blocking. If the hook fails, no data is lost -- the session archive is still available for later batch extraction. |
| Dedup between tiers creates conflicts | `source_key` prefixes distinguish origins: `cmd:`, `pref:`, `llm:`, `hook:`. FTS-based similarity check before insertion prevents semantic duplicates across prefixes. |
| Model API unavailable | Tier 1 (pattern) always works offline. Tier 2 (real-time) only works when Claude Code is already running (so API is available). Tier 3 (hook) gracefully fails and can be retried with `engram extract-session --retry-failed`. |

---

### Open Questions

1. **Should `memory_store` require confirmation from the user?** Adding a confirmation step ("I'd like to store this as a memory: ...") would increase trust but add friction. Recommendation: no confirmation by default, but mention the store action in the assistant response so the user can `memory_delete` if unwanted.

2. **Should hook-driven extraction use the same model as the conversation or a cheaper model?** A smaller/cheaper model (e.g., Haiku) may suffice for extraction since the task is structured. This would reduce tier 3 cost by ~10x. Recommendation: make the extraction model configurable via `config.yaml` (`models.extraction_model`), defaulting to the conversation model.

3. **Should memories be scoped to branches?** The current schema supports arbitrary scopes. Branch-scoped memories (e.g., "on feature-x, we use mock API") could be useful but add complexity. Recommendation: defer branch scoping to a later iteration. All memories are repo-scoped for now.

4. **How should conflicting memories be handled?** If a pattern extractor says "always use unittest" and an LLM-extracted memory says "we switched to pytest", which wins? Recommendation: newer `updated_at` wins. The LLM memory would have a later timestamp and would supersede. Add a `supersedes_id` foreign key in a future iteration for explicit conflict resolution.
