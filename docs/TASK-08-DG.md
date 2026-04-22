# Task #8 Artifact: DG — Fragment-Preserving Geometry Output
**Task:** DG: fragment-preserving geometry
**Status:** COMPLETED
**Date:** 2026-04-21
**Output:** `workers/mesh_worker.py` modifications + server.js endpoint

## What This Implements

### DG Outputs (DG-1)

| Field | Description |
|-------|-------------|
| `fragment_set_json` | List of {fragment_id, vertex_count, face_count, is_anchor_zone} |
| `hole_boundary_json` | open_boundaries, has_open_holes (DG-4: NO holes filled) |
| `usefulness_zones_json` | [{region, suitability_score}] per zone |
| `severe_geometry_concern` | 1 if no anchor zone (deferred full detection to EDSIM) |
| `structural_proxy_path` | Path to structural proxy mesh |
| `appearance_scaffold_path` | Path to appearance scaffold mesh |

### Critical Change: REMOVED Hole Closing

**DG-4 VIOLATION FIXED:**
```python
# REMOVED from cleanup_mesh():
ms.meshing_close_holes(maxholesize=30)  # ← VIOLATED DG-4
```
Open boundaries are now explicitly preserved and reported in `hole_boundary_json`.

### Fragment Detection
- Uses `extract_connected_components` from pymeshlab
- Each component = one fragment
- Anchor zone detection deferred to EDSIM (which has placement authority map)

### Usefulness Zones
- Computed from vertex distribution: interior vs peripheral regions
- Interior vertices (distance from centroid < 30% of max) = higher suitability for placement
- `suitability_score` per zone (0-1)

### Pipeline Flow
```
POST_PROCESSING (DG) → mesh_worker outputs geometry_outputs → PHOTOREAL
```

### Key Files Changed

| File | Change |
|------|--------|
| `workers/mesh_worker.py` | Complete rewrite with DG artifacts, hole-closing REMOVED, geometry_outputs POST |
| `server.js` | Added POST geometry-output endpoint |

## Blocks
- Task #9 (Photoreal) — uses geometry_outputs as input
- Task #17 was prerequisite (geometry_outputs table exists)

## OPEN Items
- structural_proxy / appearance_scaffold split: currently both point to same model.glb — full split requires topology analysis
- severe_geometry_concern full detection: requires EDSIM placement authority map (circular dependency)
- Fragment "same surface" merge strategy: not yet implemented (DG fragment merge)
