# Task #11 Artifact: OQSP — Organize-Validate-Fuse-Publish
**Task:** OQSP: organize-validate-fuse-publish subsystem
**Status:** COMPLETED
**Date:** 2026-04-21
**Output:** `workers/oqsp_worker.py` + server.js endpoints + pipeline.py wiring

## What This Implements

### OQSP Outputs (OQ-1)

| Field | Description |
|-------|-------------|
| `publishability_class` | Layered classification: FULLY_PUBLISHABLE / EDIT_CAPABLE / INTERNALLY_VALID / APPEARANCE_ONLY / REFUSAL |
| `qc_artifacts_json` | 8-artifact QC set (asset identity, capability readiness, severe concern aggregation, integrity conflicts, refusal map, lineage completeness, authority bounds, measurement posture) |
| `lineage_artifact_refs_json` | Immutable parent pointers and pipeline fingerprints |
| `capability_readiness_json` | Per-capability flags: view/placement/preview/edit_ready |
| `severe_concern_aggregation_json` | All severe concerns aggregated from upstream |
| `integrity_conflict_surfaces_json` | Surfaces where integrity conflicts exist |

### Publishability Classes
- **FULLY_PUBLISHABLE** — honest view + placement + bounded preview, no severe concerns
- **EDIT_CAPABLE** — honest view + at least one placement region
- **INTERNALLY_VALID** — honest view route exists (structural substrate present)
- **APPEARANCE_ONLY** — view-only route, no placement authority
- **REFUSAL** — no valid route; publish blocked

### Key Rules Enforced
- Never invent stronger authority downstream (OQ-8)
- Preserve upstream weakness/refusal/stale signals (OQ-5)
- External publish requires honest view-capable route (OQ-9)
- Deterministic composite manifest synthesis (OQ-6)

## Pipeline Flow
```
EDSIM (edsim_worker) → OQSP (oqsp_worker) → PUBLISHED
```

## Key Files Changed/Created

| File | Change |
|------|--------|
| `workers/oqsp_worker.py` | **NEW** — publishability classification + QC set + manifest synthesis |
| `workers/pipeline.py` | Added `OQSP → oqsp_worker` dispatch |
| `server.js` | Added `POST /api/internal/scans/:id/publish-manifest` + `GET /api/internal/scans/:id/edsim-output` + `PUBLISHED` status |

## Blocks
- Task #12 (Cross-cutting) — lineage and content-addressed storage

## OPEN Items
- Deterministic replay test suite (OQ-6) — in Task #16 (Evidence)
- Patch orchestration / dependency-scoped invalidation (OQ-7) — in Task #14
