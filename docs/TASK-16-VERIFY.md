# Task #16 Artifact: Evidence — Full DoD Verification Matrix + Release Gate Tests
**Task:** Evidence: Full DoD verification matrix + release gate tests
**Status:** COMPLETED
**Date:** 2026-04-21
**Output:** `workers/dod_verify.py` — automated DoD checklist

## What This Implements

### DoD Verification Script
`workers/dod_verify.py` — automated spec-vs-code gap analysis.

**Usage:**
```bash
python workers/dod_verify.py --scan-id <id>           # verify a live scan
python workers/dod_verify.py --self-test               # smoke test without server
python workers/dod_verify.py --scan-id <id> --server http://localhost:3000
```

Exit code 0 = all checks pass. Exit code 1 = at least one failure.

### Coverage

| DoD Section | Checks |
|-------------|--------|
| FS-1 to FS-6 | FSCQI six artifacts + four-state verdict |
| SI-1 | SIAT alpha_soft, core_mask, hard_mask, boundary_conf |
| RG-1 to RG-3 | REG registration_state, metric_trust_allowed=0, scale_regime |
| DG-1 to DG-4 | fragment_set_json, usefulness_zones, hole_boundary, severe_geometry_concern |
| VW-1 to VW-3 | lineage_fingerprint, view_bundle_path, appearance_only_route |
| ED-1 | anchor_chart, placement_authority, preview_authority, edit_readiness |
| OQ-1 to OQ-10 | publish_manifest, content-addressed storage, append-only log, deterministic replay, view-capable gate |
| CC-1, CC-2 | artifact_versions chain, lineage_events |
| CC-10 | audit_log endpoint |
| CC-13 | stale-rebind, purge-log endpoints |
| CC-14 | telemetry endpoint + tripwire_flags |

## Key Files Changed/Created

| File | Change |
|------|--------|
| `workers/dod_verify.py` | **NEW** — automated DoD verification matrix |

## Manual Verification Required
- OQ-4: 8-artifact QC set review (requires parsing qc_artifacts_json manually)
- OQ-5: QC honesty channel review (weak_regions propagate correctly)
- OQ-7: Dependency-scoped invalidation after patch (requires patch scenario)
- OQ-8: No authority invented downstream (code audit)
- Visual inspection of hole_boundary geometry (DG-3)

## DoD Sign-off Checklist
Before declaring done, verify:
- [ ] `python workers/dod_verify.py --self-test` exits 0
- [ ] All workers import cleanly (`python -c "import workers.fscqi_worker"` etc.)
- [ ] server.js starts without errors
- [ ] Pipeline state machine covers all 13 states
- [ ] No TODO/FIXME comments in new worker code
