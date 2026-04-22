# Task #6 Artifact: SIAT — Subject Isolation and Targeting
**Task:** SIAT: target isolation + ambiguity preservation
**Status:** COMPLETED
**Date:** 2026-04-21
**Output:** `workers/siat_worker.py` + server.js endpoints + pipeline.py wiring

## What This Implements

### SIAT Outputs (SI-1)

| Output | Description |
|--------|-------------|
| `target_alpha_soft` | Soft boundary mask — float32 16-bit PNG per frame |
| `target_core_mask` | Hard binary subject mask — uint8 PNG |
| `target_mask_hard` | Thin outline mask — morphological outline of core mask |
| `boundary_conf_path` | Per-pixel boundary confidence — uint8 PNG (0=edge, 255=interior) |
| `ambiguity_tags` | [{frame_id, type, confidence}] — edge_ambiguity, low_signal, occlusion |
| `occlusion_labels` | [{frame_id, region, depth_order_hint}] — edge cutoff detection |

### Boundary Confidence Channel
- Uses OpenCV distance transform from hard mask boundary
- EDGE_BAND_PX=8: pixels within 8px of boundary → lower confidence
- Normalized to 0-255 (0 at edge, 255 deep interior)

### Ambiguity Detection
- `edge_ambiguity`: >20% pixels in mixed boundary band
- `low_signal`: >90% pixels are hard 0 or 1 (no soft boundary)
- `blur_ambiguity`: blur score < 50 → tagged as low_signal

### Occlusion Detection
- Edge cutoff: >30% mean intensity in border band (5% of frame) → possible occlusion
- Depth ordering: frame index / total frames as proxy (deferred to REG for true depth)

### Pipeline Routing
- FSCQI verdict=PROCESS_CLEAN|PROCESS_WITH_FLAGS → SIAT worker runs
- SIAT → transitions to REG
- FSCQI verdict=REVIEW_NEEDED → SIAT skipped, pipeline blocked
- FSCQI verdict=RETRY_RECOMMENDED → SIAT skipped

### Key Files Changed/Created

| File | Change |
|------|--------|
| `workers/siat_worker.py` | **NEW** — full SIAT implementation |
| `workers/pipeline.py` | Added SIAT → siat_worker dispatch |
| `server.js` | Added GET fscqi-bundle + POST siat-output endpoints |
| `server.js` | Added SIAT, REG to VALID_SCAN_STATUSES |

## Blocks
- Task #7 (REG) — uses SIAT masks as input

## OPEN Items
- boundary_confidence_channel: per-pixel (current) vs per-region granularity — per-pixel chosen
- SIAT rigid_core / pose_safe_support_mask: deferred to future iteration
- Occlusion depth ordering: currently uses frame index proxy, not true multi-view depth
