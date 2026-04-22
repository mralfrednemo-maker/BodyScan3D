# Task #9 Artifact: Photoreal — View-Capable Realization
**Task:** Photoreal: view-capable realization with lineage-addressable view_version
**Status:** COMPLETED
**Date:** 2026-04-21
**Output:** `workers/photoreal_worker.py` + server.js endpoints + pipeline.py wiring

## What This Implements

### Photoreal Outputs (VW-1)

| Field | Description |
|-------|-------------|
| `view_version` | "1.0.0" — lineage-addressable version |
| `view_bundle_path` | Path to viewing bundle (model.glb) |
| `lineage_fingerprint` | SHA-256 of all upstream artifact hashes |
| `appearance_only_route` | 1 if no placement authority (metric_trust=0 or severe_concern=1 or FRAGMENTED) |

### Lineage Fingerprint
Computed from SHA-256 of concatenated hashes of:
1. Frame content hashes (scan_frames.content_hash)
2. FSCQI bundle verdict + health summary
3. SIAT output (version + path hashes)
4. REG output (registration_state + scale_regime + metric_trust)
5. DG output (fragment count + severe_geometry_concern)

### appearance_only_route = 1 when:
- metric_trust_allowed = 0 in reg_outputs
- registration_state = FRAGMENTED
- severe_geometry_concern = 1 in geometry_outputs

## Pipeline Flow
```
PHOTOREAL (photoreal_worker) → EDSIM (edit simulation with placement authority)
```

## Key Files Changed/Created

| File | Change |
|------|--------|
| `workers/photoreal_worker.py` | **NEW** — view bundle creation + lineage fingerprint + appearance_only check |
| `workers/pipeline.py` | Added PHOTOREAL → photoreal_worker dispatch |
| `server.js` | Added POST view-output + GET siat-output/reg-output/geometry-output |

## Blocks
- Task #10 (EDSIM) — uses view_outputs as input

## OPEN Items
- View bundle currently = model.glb (single mesh). Multi-angle view bundle (multiple renders) not yet implemented.
- View degradation fallback (blur kernel) not yet defined (VW-3 degradation-without-tears)
