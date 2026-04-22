# Task #14 Artifact: Cross-cutting — Telemetry Tripwires + Deterministic Replay
**Task:** Cross-cutting: Telemetry tripwires + deterministic replay suite
**Status:** COMPLETED
**Date:** 2026-04-21
**Output:** server.js telemetry endpoints + capture.html patch counter

## What This Implements

### Per-Scan Telemetry (CC-14)
`GET /api/internal/scans/:id/telemetry` returns:
- `intervention_counters` — auto_mode_count, manual_shutter_count, retry_count, patch_count, total_capture_events
- `ratios` — m1m2_ratio (manual/auto), retry_ratio, patch_ratio
- `tripwire_flags` — HIGH_RETRY_INFLATION (>3), HIGH_PATCH_INFLATION (>2), EXCESSIVE_MANUAL_EFFORT (M1/M2 > 5), NO_CAPTURE_EVENTS

### System-Wide Telemetry
`GET /api/internal/telemetry?days=7` returns:
- `scan_summary` — total/published/failed counts over period
- `intervention_aggregates` — sum totals + avg per scan
- `system_m1m2_ratio` — aggregate manual/auto ratio
- `system_tripwire_flags` — SYSTEM_HIGH_RETRY_RATE, SYSTEM_HIGH_PATCH_RATE, SYSTEM_EXCESSIVE_MANUAL

### Deterministic Replay Verification
`GET /api/internal/scans/:id/replay-verify` validates:
- Each artifact's `parent_hash` matches previous artifact's `content_hash` (chain integrity)
- `lineage_fingerprint` equals final artifact hash (fingerprint validity)
- Returns `deterministic_replay_ok` boolean

### Intervention Counters (MB-7 / CC-14)
`patch_count` column added to `capture_metadata` (via ALTER TABLE migration).
Captured in `capture.html` — incremented each time user selects WORTH_PATCH.

## Key Files Changed

| File | Change |
|------|--------|
| `server.js` | Added `patch_count` migration, `GET /api/internal/scans/:id/telemetry`, `GET /api/internal/telemetry`, `GET /api/internal/scans/:id/replay-verify` |
| `public/capture.html` | Added `patchCount` variable, increment on WORTH_PATCH, sent in finalize FormData |

## Tripwire Thresholds
| Flag | Condition |
|------|-----------|
| `HIGH_RETRY_INFLATION` | retry_count > 3 per scan |
| `HIGH_PATCH_INFLATION` | patch_count > 2 per scan |
| `EXCESSIVE_MANUAL_EFFORT` | M1/M2 ratio > 5 |
| `SYSTEM_HIGH_RETRY_RATE` | avg retries/scan > 2 (system) |
| `SYSTEM_HIGH_PATCH_RATE` | avg patches/scan > 1.5 (system) |

## OPEN Items
- Alerting/notifications when tripwires fire (ops concern)
