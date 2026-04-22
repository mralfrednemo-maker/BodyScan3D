# Task #5 Artifact: FSCQI — Full Signal Quality + Coverage Index
**Task:** FSCQI: six artifacts + four-state verdict
**Status:** COMPLETED
**Date:** 2026-04-21
**Output:** `workers/fscqi_worker.py` + server.js endpoint + pipeline.py wiring

## What This Implements

### Six FSCQI Artifacts (FS-1)

1. **Curated primary tier** — frame_ids with blur >= BLUR_EXCELLENT (2.5x threshold)
2. **Extended candidate tier** — frame_ids with BLUR_GOOD <= blur < BLUR_EXCELLENT
3. **Coverage descriptor** — dual-level: overall_coverage (0-1) + per_region grid (4x4)
4. **Weak-region register** — list of {frame_id, reason, severity, coverage_zone}
5. **Capture health summary** — overall_score (0-100), avg_blur, coverage_score, flags[]
6. **Processability verdict** — PROCESS_CLEAN | PROCESS_WITH_FLAGS | REVIEW_NEEDED | RETRY_RECOMMENDED

### Verdict Routing (FS-3)

| Verdict | Next State | Action |
|---------|-----------|--------|
| PROCESS_CLEAN | SIAT | Normal pipeline |
| PROCESS_WITH_FLAGS | SIAT | Flagged, downstream aware |
| REVIEW_NEEDED | OPERATOR_REVIEW | Pipeline blocked, operator decides |
| RETRY_RECOMMENDED | CAPTURING | Operator initiates new capture |

### Coverage Estimation
- Uses OpenCV moment-based centroid + spread per frame
- 4x4 grid coverage map
- Adjacent cell coverage via spread radius
- Reports overall_coverage (fraction of cells with ≥1 observation)

### Weak Region Classification
- **severe** — blur < 0.5 * WEAK_REGION_SEVERITY * BLUR_EXCELLENT
- **moderate** — blur below threshold but not severe
- **coverage_gap** — grid cell with zero frame coverage

### Key Files Changed/Created

| File | Change |
|------|--------|
| `workers/fscqi_worker.py` | **NEW** — full FSCQI implementation |
| `workers/pipeline.py` | Added FSCQI → fscqi_worker dispatch |
| `workers/frame_qa.py` | Transition target changed AWAITING_TARGET → FSCQI |
| `server.js` | Added FSCQI bundle endpoint + VALID_SCAN_STATUSES update |

## Implementation Notes

- Uses existing `blurScore` from frame_qa.py where available
- Falls back to PIL if OpenCV unavailable
- Coverage estimation is centroid-based (not pixel-perfect — acceptable for signal quality index)
- `fscqi_bundle_id` stored in `capture_metadata` via INSERT OR REPLACE

## Blocks
- Task #6 (SIAT) — FSCQI verdict gates SIAT input
- Task #17 was prerequisite (DB schema must exist for fscqi_bundles table)

## OPEN Items
- Per-region coverage metric (FS-2) — currently 4x4 grid; could be body-part-aware
- Weak-region severity thresholds — calibrated empirically, not validated against ground truth
