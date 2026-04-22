# Task #15 Artifact: Cross-cutting — Purge + Regeneration Truth + Stale/Rebind
**Task:** Cross-cutting: Purge + regeneration truth + stale/rebind behavior
**Status:** COMPLETED
**Date:** 2026-04-21
**Output:** server.js purge endpoints

## What This Implements

### Purge Behavior (CC-13)
Purge is **cascade-safe and auditable**:
1. Scan status → `PURGED`, lineage_root_hash → `PURGED:<scan_id>` (no longer regenerable)
2. All `artifact_versions` rows deleted (content-addressed storage wiped)
3. `lineage_events` **preserved** (append-only audit trail — never deleted)
4. `purge_log` entry written per artifact with reason + operator ID
5. Audit event logged: `ASSET_PURGED`

**Key rule enforced:** After purge, asset must NOT present itself as regenerable. The `PURGED` status + cleared lineage_root_hash ensures any replay attempt fails honestly.

### Stale Rebind Detection (CC-13)
EDSIM's `stale_rebind_json` detects when identical geometry has different meaning:
- `auto_rebind_identical` enforced: same hash = same meaning
- Non-identical downstream meaning → stale or retired

Exposed via `GET /api/internal/scans/:id/stale-rebind`.

### Retire Endpoint (CC-13)
`POST /api/internal/scans/:id/retire` marks a specific artifact version as retired without deleting it:
- Writes a `RETIRE_ARTIFACT` lineage event
- Logs audit event: `ARTIFACT_RETIRED`
- Does NOT delete — preserves audit trail

## Key Files Changed

| File | Change |
|------|--------|
| `server.js` | Added `PURGED` status, `POST /api/internal/scans/:id/purge`, `GET /api/internal/scans/:id/purge-log`, `GET /api/internal/scans/:id/stale-rebind`, `POST /api/internal/scans/:id/retire` |

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/internal/scans/:id/purge` | POST | Cascade purge — clears artifacts, preserves lineage_events, marks scan non-regenerable |
| `/api/internal/scans/:id/purge-log` | GET | Fetch all purge_log entries for scan |
| `/api/internal/scans/:id/stale-rebind` | GET | Fetch stale rebind register from EDSIM output |
| `/api/internal/scans/:id/retire` | POST | Mark specific artifact hash as retired (non-destructive) |

## Purge Rules Enforced
- **Explicit**: requires `purgeReason` field
- **Auditable**: writes to `purge_log` + `audit_log`
- **Lineage-safe**: `lineage_events` never deleted (append-only)
- **Cascade-safe**: `artifact_versions` wiped (content addressed)
- **Non-regenerable after purge**: `lineage_root_hash` cleared, status = `PURGED`

## OPEN Items
- Physical file deletion from disk (uploads/models/:scan_id/*) — currently only DB records are purged
