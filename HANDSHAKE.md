# HANDSHAKE — handoff for the next thread

Started: 2026-09-02. Repo: `tortoise-extended`, branch `main`, project dir
`/home/daen/Projects/python-development/tortoise-extended`. Owner session
`ses_f9db07955ffetpFAJyTskO4QpU`. This file exists so a fresh thread can
resume cleanly without re-reading this whole conversation.

---

## 1. Immediate state — VERIFIED, READY TO COMMIT

Two HIGH audit fixes are **complete and verified** (ruff + basedpyright
0/0/0 + full pytest 919 passed/1 skipped + PyCharm problems/lint clean).
**Not yet committed.**

### Fix #4 — `src/tortoise_extended/models/graph_node.py` (DONE, VERIFIED)
- Added `from tortoise.transactions import in_transaction` (line 29).
- Refactored `save()`: the node write + `child_count` maintenance is now a
  single inner coroutine `_persist_and_sync()`, wrapped in
  `async with in_transaction():` **only when `using_db is None`** (caller-scoped
  connections untouched — never a nested tx).
- Refactored `delete()` the same way (`_delete_and_sync()`).
- **Added `super(BaseGraphNodeModel, self)`** (explicit arg form) in both
  `_persist_and_sync()` and `_delete_and_sync()` — Python 3.14 breaks bare
  `super()` inside nested closures.

### Fix #5 — `src/tortoise_extended/models/user.py` (DONE, VERIFIED)
- `**kwargs` on `create_user` / `create_superuser` now typed `CoercedValue`
  (NOT `ModelKwargs` — the latter made each kwarg value a `dict`, wrong).
- `is_active` / `is_staff` / `is_superuser` are explicit keyword-only bools.
- `create_superuser` builds `extra: ModelKwargs = {**kwargs, "is_staff": ..., "is_superuser": ...}`
  then `**extra` into `create_user`. Resolves the prior basedpyright invariance
  error (merged literal stays in `CoercedValue`; no re-inferring `dict()`).
- Verified basedpyright now 0/0/0.

---

## 2. What each fix is FOR (so you don't undo it)

- **#4** — db-engineer audit finding A1/B1 HIGH: `save()`/`delete()` ran the row
  write and the denormalized `child_count` counter maintenance as separate async
  DB ops with no transaction. A partial failure drifts `child_count`/`is_leaf`.
  The fix makes the default-connection path atomic via `in_transaction()`.
- **#5** — python-developer audit finding HIGH: `create_user`/`create_superuser`
  used `**kwargs: object`, erasing the keyword signature and losing call-site
  type checking. Fix: explicit typed boolean flags + `**kwargs: ModelKwargs`
  (the established `dict[str, CoercedValue]` alias from `_types.py`, NOT
  `object`).

---

## 3. Verification gate — do ALL of these before reporting done

```bash
uv run ruff check src tests
uv run basedpyright src/tortoise_extended/
uv run pytest tests/test_user_model.py tests/test_graph.py tests/test_graph_traversal.py -q
```
Plus PyCharm MCP: `pycharm_get_file_problems` + `pycharm_lint_files` on the two
changed files. THEN commit (repo style, one concise message).

---

## 4. The 7 audit-fix TODOs (project todo store, opencode.db)

Owner session `ses_f9db07955ffetpFAJyTskO4QpU`, positions 4-10, all `pending`:

| pos | sev | work |
|-----|-----|------|
| 4 | high | graph_node save/delete child_count in one in_transaction() — **IN PROGRESS (done, unverified)** |
| 5 | high | user create_user/create_superuser typed params — **IN PROGRESS (typing bug mid-fix)** |
| 6 | med | remove library `print()` from timescale managers (retention/compression/continuous_aggregate/hypertable) → logger |
| 7 | med | add composite `(namespace, depth)` index on BaseGraphNodeModel; move CCA validation out of migration into a manager |
| 8 | low | descendants()/ancestors() depth-approx doc; `FOR UPDATE` in move_to() for TOCTOU; subtree() N+1 doc |
| 9 | low | make HybridSearch FTS language configurable (hybrid_search.py:205,208 hardcode 'english') |
| 10 | low | include `migrate_data` in CreateHypertable.describe() (operations.py:113-121) |

Query them anytime:
```bash
sqlite3 /home/daen/.local/share/opencode/opencode.db -separator " || " \
 "SELECT t.position,t.status,t.priority,substr(t.content,1,80) FROM todo t
  JOIN session s ON t.session_id=s.id
  WHERE s.project_id='fe11928eaae08e83eec30af1464e8adb752eb2da'
    AND t.status IN ('pending','in_progress') ORDER BY t.time_created;"
```
Mirror non-completed items into your session via `todowrite` (AGENTS.md §1.1).
Update status back to opencode.db when you claim/complete.

---

## 5. Audit provenance — what the 3-agent audit found and rejected

Three READ-ONLY audit subagents were dispatched (rubber-duck, python-developer,
devel-tortoise-db-engineer). Two delivered full reports; **rubber-duck never
emitted a written report** (it only did reads — a known limitation; its focus
areas were covered by the other two).

**Rejected as FALSE POSITIVE (do not re-add):** python-developer's only CRITICAL
— "raw SQL identifier interpolation without `quote_ident` in
`models/event_stream.py:443-530`." Verified: `latest_per_stream`/`time_series`
pass every identifier through `quote_ident()` and parameterize all values with
`$N` placeholders. This was a verification hole I caught by reading source; it
is NOT a bug.

---

## 6. Key file/location references

- `src/tortoise_extended/_types.py:58` — `ModelKwargs: TypeAlias = dict[str, CoercedValue]`
- `src/tortoise_extended/models/hierarchy_model.py:41,477` — established
  `from tortoise.transactions import in_transaction` + `async with in_transaction():` precedent
- Stub renames `in_transaction`→`n` (a stubs artifact) — the RUNTIME name is
  `tortoise.transactions.in_transaction`; use the runtime name in code.
- `AGENTS.md` §Output Discipline (lines ~362+) — CHAT NARRATION cap ~500 tokens
  (never narrate the act of calling a tool; if stalled, stop narration and say
  "I'm stuck: ..." plainly). Verification thinking is uncapped but
  efficiency-constrained (trace-once-with-delta-checks, depth-by-severity).

---

## 7. Known behavior failure to avoid (read this)

The previous thread had repeated **degenerate output loops**: emitting streams
of near-identical placeholder lines ("Let me read the test file", "Re-running
the gate", etc.) instead of committing to a single tool call, or stopping. If
you catch yourself about to re-state the same intended action in different
words, STOP and either (a) make the one tool call immediately with no narration,
or (b) if you genuinely have no next step, say "I'm stuck: <what>".
