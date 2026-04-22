# CLAUDE.md — BodyScan 3D

## DoD — Mandatory First Reference
**THE** Definition of Done is at `docs/bodyscan-dod-outcomes.txt` (727-line spec).
**NEVER proceed with any implementation without reading the DoD first.**

## Session Protocol (MUST follow every session, no exceptions)

### Step 1: Load context
1. Read `PROGRESS.md` — this is the canonical progress tracker
2. Read `docs/bodyscan-dod-outcomes.txt` — the DoD
3. Check `git log --oneline -5` — where did the last session end?

### Step 2: Gap analysis — repeat until ZERO findings
**GAP ANALYSIS LOOP (do this before ANY code review):**
1. Read `docs/bodyscan-dod-outcomes.txt` — the DoD spec
2. Use the **solutions-architect** agent (subagent_type: solutions-architect). Give it the DoD path. Ask for structured gap list prioritized by severity.
3. **Run solutions-architect TWICE** with different angles: pass 1 on requirements coverage, pass 2 on implementation correctness (API usage, types, error handling).
4. Fix the highest-severity gap.
5. Repeat from step 2.
6. **STOP gap analysis loop only when solutions-architect returns ZERO new gaps.**
7. Only then proceed to Step 3 (code review).

**CODE REVIEW LOOP (only after gap analysis is clean):**
1. Use the **superpowers:code-reviewer** agent after any code change (superpowers:requesting-code-review skill).
2. Use **codex:rescue** for deep root-cause investigation of failures (Agent tool, subagent_type: codex:codex-rescue).
3. Fix findings.
4. Repeat code review until zero findings.
5. Only then move on.

### Step 3: Work structure — trust the persistence layer
**Do NOT manage context manually.** The PreCompact hook + handoff files mean work persists regardless of context remaining. Structure every task as an independent, committable unit:

1. Work in focused subagents (Agent tool) for independent tasks — the subagent output is committed to git, the handoff file is written, and the next session picks up exactly where you left off.
2. Commit **at natural boundaries** — after each fix, after each gap analysis pass, after each feature implementation. Not just at session end.
3. If a subagent is running and context runs low: **let it continue**. The PreCompact hook will fire, save state, and the next session resumes from the handoff file.
4. If context is near 0%: launch the next task as a subagent and exit the main session cleanly. Don't try to squeeze more in.

### Step 4: Work tracking
After each significant action, update `PROGRESS.md`.
On session end (or before compaction), commit progress to git:
```
git add PROGRESS.md HANDOVER.md docs/
git commit -m "session progress update"
```

## Architecture
8-stage pipeline: FSCQI → SIAT → REG → DG → PHOTOREAL → EDSIM → OQSP → PUBLISH
Content-addressed append-only lineage. SHA-256 hashes for each artifact.

## Key Files
| File | Purpose |
|------|---------|
| `server.js` | Express + SQLite, `PORT=5001 node server.js` |
| `workers/pipeline.py` | Orchestrator, `python pipeline.py <scan_id>` |
| `workers/reconstruct_worker.py` | pycolmap SfM + MVS (CORE ENGINE — often missing) |
| `workers/mesh_worker.py` | DG: fragment-preserving honest geometry per DG-33 |
| `workers/siat_worker.py` | SAM2 segmentation (needs `SAM2_MOCK=1` fallback) |
| `docs/bodyscan-dod-outcomes.txt` | THE DoD spec |
| `PROGRESS.md` | Gap tracker, git-committed |

## Credentials
- christo / christo123
- admin / bs3d-admin-2026

## Server
- `PORT=5001 node server.js` (Windows, not WSL)
- Ngrok: `wsl ngrok http --host-header=rewrite 192.168.178.36:5001`
- Database: `data.db` (node:sqlite, DatabaseSync)

## Current Blockers
1. pycolmap reconstruction never executed on real frames (reconstruct_worker.py runs but outputs show UNKNOWN registration)
2. SAM2 server-side mask refinement not wired (SAM2_MOCK=1 fallback)
3. Frontend camera getUserMedia blocked on mobile
