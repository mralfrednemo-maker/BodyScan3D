# Task #12 Artifact: Cross-cutting — Append-Only Lineage + Content-Addressed Storage
**Task:** Cross-cutting: Append-only lineage + content-addressed storage
**Status:** COMPLETED
**Date:** 2026-04-21
**Output:** server.js lineage wiring + GET endpoints

## What This Implements

### Content-Addressed Storage (OQ-2)
All artifacts are stored append-only under content addressing. Each artifact's content is hashed (SHA-256) and stored in `artifact_versions` with:
- `scan_id` — owning scan
- `artifact_type` — fscqi_bundle / siat_output / reg_output / geometry_output / view_output / edit_sim_output / publish_manifest
- `content_hash` — SHA-256 of artifact JSON (UNIQUE constraint = idempotent)
- `storage_path` — file path or URL
- `byte_size` — content size
- `parent_hash` — previous artifact in chain

### Lineage Chain
Each artifact records its parent hash, forming an immutable chain:
```
fscqi_bundle → siat_output → reg_output → geometry_output → view_output → edit_sim_output → publish_manifest
```

### Append-Only Events (CC-1)
`lineage_events` table records every artifact creation with:
- `event_type` — CREATE_FSCQI_BUNDLE, CREATE_SIAT_OUTPUT, etc.
- `payload_json` — full artifact content
- `parent_artifact_hash` / `new_artifact_hash` — chain linkage

### Key Properties
- No mutation: artifacts are never updated, only new versions appended
- Idempotent: same content hash = same artifact (UNIQUE constraint)
- Stale detection: `stale_rebind_json` (from EDSIM) detects when identical geometry has different meaning
- Deterministic replay: same content → same hash (OQ-6)

## Key Files Changed

| File | Change |
|------|--------|
| `server.js` | Added `recordLineageEvent()` helper, `getLastArtifactHash()`, wired into all 7 artifact POST endpoints, added GET lineage/artifact-version endpoints |

## New Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/internal/scans/:id/lineage-events` | Full lineage event log for scan |
| `GET /api/internal/scans/:id/artifact-versions` | All artifact versions for scan |
| `GET /api/internal/scans/:id/artifact-versions/:type` | Artifact versions filtered by type |

## Blocks
- Task #13 (Encryption + access + audit) — audit_log table already exists
