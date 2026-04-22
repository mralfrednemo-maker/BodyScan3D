# BodyScan 3D — Progress Tracker

## DoD Gap Analysis (2026-04-22)

### Gap Severity Classification

| ID | Requirement | Status | Severity | Notes |
|----|-------------|--------|----------|-------|
| G1 | pycolmap reconstruction produces usable geometry | **BROKEN** | P0 | Incremental mapping returns empty for scan 16's 30 frames → registration_state=UNKNOWN, connected_fraction=0 |
| G2 | SAM2 real mask segmentation | **NOT WIRED** | P1 | SAM2_MOCK=1 — generates placeholder masks, not real SAM2 output |
| G3 | DG-33 honest fragments on real geometry | **CANNOT TEST** | P1 | DG ran on PLACEHOLDER geometry (scan 7's model.glb copied), 238 open boundary edges on fake data — not meaningful |
| G4 | Camera capture on real phone | **BLOCKED** | P1 | getUserMedia undefined on capture.html — HTTPS/permission issue |
| G5 | Photoreal on real DG geometry | **STUBBED** | P1 | appearance_only_route=1 — photoreal fell back because raw_pointcloud was placeholder |
| G6 | EDSIM stale rebind detection | **HAS BUG** | P2 | detect_stale_rebind() takes dict arg but callers pass string path |
| G7 | Camera capture UX (P1 prompt-3 DoD) | **NOT TESTED** | P1 | Real phone capture never tested end-to-end |
| G8 | R-OUT-2 surface-anchored placement | **STUBBED** | P1 | No real UV parameterization — uses placeholder mesh |
| G9 | R-OUT-3 deformable geometric proxy | **STUBBED** | P1 | No real deformation capability — placeholder mesh only |
| G10 | R-OUT-4 provenance regeneration bundle | **PARTIAL** | P2 | Lineage chain exists but all on placeholder data |
| G11 | R-OUT-5 confidence/flagged-region machine-readable | **STUBBED** | P2 | confidence outputs exist structurally but based on fake geometry |
| G12 | Cross-output spatial consistency (R-OUT-7) | **CANNOT TEST** | P1 | Requires real geometry from all 3 outputs — not available |
| G13 | PURGE honestly terminates lineage | **IMPLEMENTED** | — | server.js /purge does sha256('PURGED'), clears modelUrl |
| G14 | ED-4 append-only JSONL bundles | **IMPLEMENTED** | — | All 5 jsonl files created for scan 16 |
| G15 | OQ-4 8-artifact assertion | **IMPLEMENTED** | — | assert artifact_count==8 in oqsp_worker.py |

---

## Root Cause Analysis

### G1 — pycolmap returns empty reconstruction
**What happened**: reconstruct_worker.py ran for scan 16. Phase A (full frames) returned None. Phase B (masked frames) returned None. Both phases failed.

**Likely causes** (need investigation):
1. Phone-captured frames lack sufficient visual features for pycolmap's SIFT-based feature extraction
2. pycolmap.extract_features or match_exhaustive failing silently
3. Incremental mapping collapsing due to poor initial pair selection
4. Camera intrinsics not properly set (phone cameras have varying focal lengths)

**To investigate**: Run `python workers/reconstruct_worker.py 16` manually and capture stdout/stderr to see exact pycolmap failure point.

### G3 — DG-33 on placeholder geometry
**What happened**: Because G1 failed, `raw_pointcloud.glb` was manually copied from scan 7 as placeholder. mesh_worker.py produced a box-based mesh (6053 vertices, 11909 faces). The 238 open boundary edges are artifacts of the placeholder, not genuine geometry analysis.

**When G1 is fixed**: DG will process real pycolmap output. Whether DG-33 is satisfied depends on whether pycolmap produces fragmented geometry with genuine open boundaries or a single closed mesh.

### G4 — getUserMedia undefined
**Likely cause**: capture.html served over HTTP (not HTTPS), and browsers block getUserMedia on insecure origins except localhost. The ngrok URL provides HTTPS but may have permissions issues.

---

## Recommended Fix Order

### Phase 1 — Make pipeline not crash (G1, G5)
1. Investigate why pycolmap returns empty reconstruction for phone frames
2. Either: fix pycolmap invocation (camera model, feature extraction params), OR switch to a different SfM approach that works on phone imagery
3. Re-run reconstruct_worker on scan 16's real frames
4. Verify REG shows connected_fraction > 0

### Phase 2 — Wire SAM2 (G2)
1. Install SAM2 checkpoints: `pip install segment-anything` + download sam2.1_hiera-large.pt
2. Remove SAM2_MOCK=1 from environment
3. Test SIAT output quality on real segmentation

### Phase 3 — Fix camera capture (G4, G7)
1. Test capture.html over ngrok HTTPS URL on actual phone
2. Fix getUserMedia permission flow
3. Run full end-to-end with real phone capture

### Phase 4 — Full pipeline E2E (G3, G5, G8, G9, G12)
1. With real geometry from Phase 1, verify DG-33 output
2. Verify photoreal produces real texture-mapped mesh (not appearance_only_route)
3. Verify UV parameterization exists for tattoo placement
4. Verify deformation works on real mesh

---

## Pipeline Run History

| Scan | Date | FSCQI | SIAT | REG | DG | Photoreal | EDSIM | OQSP | Notes |
|------|------|-------|------|-----|----|-----------|-------|------|-------|
| 16 | 2026-04-22 | ✓ | ✓ (mock) | UNKNOWN | ✓ (placeholder) | fallback | ✓ | ✓ | G1 broken, all downstream on fake data |

## Pending Actions

### P0 — Fix pycolmap reconstruction (G1) — BLOCKED
- [ ] Run `python workers/reconstruct_worker.py 16` manually, capture full output
- [ ] Identify exact failure point in pycolmap pipeline
- [ ] Fix or replace pycolmap approach
- [ ] Verify connected_fraction > 0 on scan 16

### P1 — Wire SAM2 (G2)
- [ ] Install SAM2: `pip install segment-anything` + sam2.1_hiera-large.pt
- [ ] Set SAM2_MOCK=0 or remove env var
- [ ] Re-run SIAT, verify real masks

### P2 — Fix camera access (G4, G7)
- [ ] Test capture.html on phone via ngrok HTTPS
- [ ] Fix getUserMedia permission flow
- [ ] Document correct ngrok command with --host-header=rewrite

### P3 — EDSIM stale rebind bug (G6)
- [ ] Fix `detect_stale_rebind()` signature mismatch (expects dict, gets string)
- [ ] Write test for geometry-output-history endpoint

## Last Session
- 2026-04-22: DoD saved to docs/, CLAUDE.md + PROGRESS.md created, PreCompact hook wired
- Pipeline ran: G1 confirmed broken (pycolmap returns empty), all downstream on placeholder
- Root cause investigation needed via manual reconstruct_worker run

## Git Log
```
33a1525 chore: remove test marker from PROGRESS.md
221a81d auto: progress update before compaction (2026-04-22 12:17)
66dae97 docs: add canonical DoD spec (2 files)
88c85ce feat: session protocol + gap tracking system
fab487b feat: DoD Phase 2 — full 8-stage pipeline + Definition of Done compliance
```
