# BodyScan 3D — Progress Tracker

## DoD Gap Analysis Status

| Section | Requirement | Status | Notes |
|---------|-------------|--------|-------|
| **FS-1** | raw_reference_map in fscqi_bundles | DONE | build_raw_reference_map() implemented |
| **FS-2** | curated_primary_tier + extended_candidate_tier | DONE | curate_tiers() implemented |
| **FS-3** | 4-state verdict (PROCESS_CLEAN/WITH_FLAGS/REVIEW/RETRY) | DONE | compute_verdict() implemented |
| **FS-4** | coverage_descriptor + weak_region_register | DONE | compute_coverage_descriptor() + classify_weak_regions() |
| **FS-5** | capture_health_summary | DONE | compute_health_summary() |
| **SI-1** | static_rigid_core + pose_safe_support_mask | DONE | siat_worker produces these paths |
| **SI-2** | dynamic frame dimensions (lazy init from first frame shape) | DONE | siat_worker.py all_union_mask=None lazy init |
| **SI-3** | SAM2 mask refinement | **NOT WIRED** | SAM2_MOCK=1 fallback only |
| **REG-1** | pycolmap sparse reconstruction | **PARTIAL** | reconstruct_worker exists but registration_state=UNKNOWN on scan 16 |
| **REG-2** | fallback_wide feature extraction | DONE | reg_worker.py has fallback_wide |
| **DG-1** | appearance_scaffold.glb distinct from model.glb | DONE | mesh_worker.py checks scaffold vs model |
| **DG-2** | appearance_scaffold = quadric decimation ~15% faces | DONE | trimesh.creation.box fallback if decimation fails |
| **DG-3** | fragment_set_json + hole_boundary_json | DONE | geometry_outputs populated |
| **DG-33** | honest fragments / open boundaries | **PARTIAL** | 238 open boundary edges detected on placeholder geometry |
| **VW-1** | single-hash (not double) for lineage | DONE | fixed in photoreal_worker.py |
| **CC-1** | honest purge (lineage terminates at PURGED, modelUrl=NULL) | DONE | server.js /purge endpoint |
| **ED-4** | append-only .jsonl state bundles | **NOT VERIFIED** | edsim_worker.py writes jsonl but endpoint not checked |
| **ED-5** | stale rebind detection against all prior geometry | **NOT VERIFIED** | detect_stale_rebind() in code but no test |
| **OQ-4** | 8-artifact QC count assertion | DONE | oqsp_worker.py has assert artifact_count==8 |
| **GAP-6** | modelUrl cleared on purge | DONE | server.js UPDATE scans SET modelUrl=NULL |

## Pipeline Run History

| Scan | Date | FSCQI | SIAT | REG | DG | Photoreal | EDSIM | OQSP | Notes |
|------|------|-------|------|-----|----|-----------|-------|------|-------|
| 16 | 2026-04-22 | ✓ | ✓ | UNKNOWN | ✓ | ✓ | ✓ | ✓ | Placeholder geometry (pycolmap not run) |

## Pending Actions (git-committed, survive compaction)

### P0 — Core reconstruction (BLOCKED)
- [ ] `python workers/reconstruct_worker.py 16` — run actual pycolmap on scan 16's 30 frames
- [ ] Verify registration_state != UNKNOWN after reconstruction
- [ ] Re-run DG with real geometry, verify DG-33 open boundaries

### P1 — SAM2 wiring
- [ ] Install SAM2 on Windows (`pip install segment-anything`)
- [ ] Wire `python workers/siat_worker.py <scan_id> --sam2` (remove SAM2_MOCK=1)
- [ ] Verify static_rigid_core from real masks (not mock)

### P2 — Camera access
- [ ] Fix `getUserMedia undefined` on capture.html mobile
- [ ] Verify `--host-header=rewrite` ngrok flag works for phone access

### P3 — EDSIM verification
- [ ] Run `dod_verify.py --self-test` end-to-end
- [ ] Verify edsim jsonl bundles are served via `/uploads`
- [ ] Test stale rebind: make geometry edit, verify refusal_zones fires

## Last Session
- 2026-04-22: HANDOVER.md written, DoD saved to docs/, CLAUDE.md + PROGRESS.md created
- Pipeline ran through all 8 stages on scan 16 but processed placeholder geometry
- DG-33 partially verified: 238 open boundary edges on placeholder mesh

## Git Log
```
fab487b feat: DoD Phase 2 — full 8-stage pipeline + Definition of Done compliance
```
