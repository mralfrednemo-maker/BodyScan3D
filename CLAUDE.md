# CLAUDE.md — BodyScan 3D

## DoD — Mandatory First Reference
**THE** Definition of Done is at `docs/bodyscan-dod-outcomes.txt` (727-line spec).
**NEVER proceed with any implementation without reading the DoD first.**

## Session Protocol (MUST follow every session, no exceptions)

### Step 1: Load context
1. Read `PROGRESS.md` — this is the canonical progress tracker
2. Read `docs/bodyscan-dod-outcomes.txt` — the DoD
3. Check `git log --oneline -5` — where did the last session end?

### Step 2: Gap analysis (MANDATORY before any new work)
1. Read `docs/bodyscan-dod-outcomes.txt` — the DoD spec
2. Use the **solutions-architect** agent for gap analysis (Agent tool, subagent_type: solutions-architect). Give it the DoD path and ask for a structured gap list prioritized by severity.
3. Use the **superpowers:code-reviewer** agent after any code change (superpowers:requesting-code-review skill).
4. Use **codex:rescue** for deep root-cause investigation of failures (Agent tool, subagent_type: codex:codex-rescue).
5. Update `PROGRESS.md` with findings before writing any code.

### Step 3: Work tracking
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
