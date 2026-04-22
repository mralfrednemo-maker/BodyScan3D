# Task #10 Artifact: EDSIM — Edit Simulation with Placement Authority Map
**Task:** EDSIM: authority-map + edit-simulation subsystem
**Status:** COMPLETED
**Date:** 2026-04-21
**Output:** `workers/edsim_worker.py` + server.js endpoints + pipeline.py wiring

## What This Implements

### EDSIM Outputs (ED-1)

| Field | Description |
|-------|-------------|
| `anchor_chart_json` | Per-vertex or per-region authority chart — which mesh zones are structural anchors |
| `placement_authority_json` | Zones where placement is permitted (structural_proxy zones only) |
| `preview_authority_json` | Zones where preview is permitted (broader than placement) |
| `appearance_only_routes_json` | View-only routes where metric/structural authority is absent |
| `edit_regions_json` | Regions available for edit (= placement zones minus refusal zones) |
| `stale_rebind_json` | Stale mesh rebind register — detects when identical geometry has different meaning |
| `edit_readiness_summary_json` | Overall readiness flags (no_placement_authority, excessive_refusal_zones, etc.) |

### Placement Authority Rules
- Only granted on `structural_proxy` zones (anchor_chart regions)
- Not granted on `appearance_only_route` geometry
- Not granted where `severe_geometry_concern = 1`
- `auto_rebind_identical` enforced: same geometry hash = same meaning

### Lineage Fingerprint
Derived from PHOTOREAL's `lineage_fingerprint` (SHA-256 of all upstream hashes).
EDSIM reads the same fingerprint to detect stale rebind conditions.

## Pipeline Flow
```
PHOTOREAL (photoreal_worker) → EDSIM (edsim_worker) → OQSP (organize-validate-fuse-publish)
```

## Key Files Changed/Created

| File | Change |
|------|--------|
| `workers/edsim_worker.py` | **NEW** — 13 EDSIM artifacts + authority maps + stale rebind detection |
| `workers/pipeline.py` | Added `EDSIM → edsim_worker` dispatch |
| `server.js` | Added `POST /api/internal/scans/:id/edsim-output` + `GET /api/internal/scans/:id/view-output` |

## Blocks
- Task #11 (OQSP) — uses edit_sim_outputs as input

## OPEN Items
- OQSP (Task #11) not yet implemented — EDSIM transitions to OQSP state but no worker exists yet
