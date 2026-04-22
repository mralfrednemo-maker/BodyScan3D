# Task #7 Artifact: REG — Multi-view Registration with Honest Scale Posture
**Task:** REG: multi-view registration + honest scale posture
**Status:** COMPLETED
**Date:** 2026-04-21
**Output:** `workers/reg_worker.py` + server.js endpoint + pipeline.py wiring

## What This Implements

### REG Outputs (RG-1)

| Field | Value |
|-------|-------|
| `registration_state` | CONNECTED (≥90% frames share points) / PARTIAL (50-90%) / FRAGMENTED (<50%) |
| `reg_graph_json` | Which frames share 3D point observations |
| `pose_version` | "1.0.0" |
| `scale_regime` | RELATIVE (no calibration target) / METRIC (calibrated) / UNKNOWN |
| `scale_confidence_band_json` | {mean_depth, std_depth, coefficient_of_variation, unit} |
| `metric_trust_allowed` | **0 (FALSE by default)** — RG-2 |
| `measurement_validity_claim` | VALID / INVALID / INDETERMINATE |
| `measurement_use_prohibited` | 0 or 1 — 1 when feature_support=fallback_wide |
| `feature_support_regime` | core_only / core_plus_context / fallback_wide |

### Key Design Decisions

- `metric_trust_allowed = 0` by default (RG-2: no scale assumed metric)
- `measurement_use_prohibited = 1` when `feature_support = fallback_wide`
- `measurement_validity = INVALID` when `feature_support = fallback_wide`
- `scale_regime = RELATIVE` always (METRIC requires calibration target — not implemented)
- Registration graph built from pycolmap track element sharing

### Pipeline Flow
```
RECONSTRUCTING (pycolmap) → REG (reg_worker extracts honest scale posture)
  → POST_PROCESSING/DG (mesh_worker fragment-preserving geometry) → PHOTOREAL
```

### Key Files Changed/Created

| File | Change |
|------|--------|
| `workers/reg_worker.py` | **NEW** — honest scale posture extraction from pycolmap output |
| `workers/pipeline.py` | Added REG → reg_worker dispatch |
| `workers/reconstruct_worker.py` | Transition target changed POST_PROCESSING → REG |
| `workers/mesh_worker.py` | Transition target changed COMPLETED → PHOTOREAL |
| `server.js` | Added POST reg-output endpoint, VALID_SCAN_STATUSES update |

## Blocks
- Task #8 (DG) — reg_outputs needed for geometry fragment analysis
- Task #17 was prerequisite (reg_outputs table must exist)

## OPEN Items
- METRIC scale regime requires calibration target detection — not yet implemented
- reconstruct_worker.py needs to save pycolmap sparse dir path for reg_worker to find
- recon_meta.json sidecar for reconstruction metadata path passing
