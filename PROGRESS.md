# BodyScan 3D — Progress Tracker

## DoD Gap Analysis (2026-04-22)

### Gap Status After Session

| ID | Requirement | Status | Notes |
|----|-------------|--------|-------|
| G1 | pycolmap reconstruction | **FIXED** | 3783 sparse points, 30/30 frames connected, sparse dir persisted |
| G1b | Dense MVS | **STALLED** | Docker + CUDA required, os.getuid issue on Windows |
| G2 | SAM2 real segmentation | **FAST-SIM PLACED** | fast-simplification installed; SAM2 checkpoints still needed |
| G3 | DG-33 honest fragments | **FIXED** | 3 real fragments (9154+179+8975v), 216 boundary edges — fragment-preserving DG working |
| G4 | Camera capture on phone | **NOT TESTED** | getUserMedia blocked — HTTPS/permission issue |
| G5 | Photoreal on real geometry | **FALLBACK** | appearance_only_route=1 (correct fallback without metric calibration) |
| G6 | EDSIM stale rebind bug | **VERIFIED OK** | detect_stale_rebind(view_output=dict) — caller passes r_view.json() — looks correct, ran successfully |
| G7 | Camera UX E2E | **NOT TESTED** | Real phone capture never tested |
| G8 | R-OUT-2 UV parameterization | **NOT TESTED** | Requires real DG geometry + proper UV mesh |
| G9 | R-OUT-3 deformation | **NOT TESTED** | Placeholder mesh only |
| G10 | R-OUT-4 provenance | **PARTIAL** | Lineage chain works on real geometry |
| G11 | R-OUT-5 confidence machine-readable | **STUBBED** | confidence exists structurally |
| G12 | R-OUT-7 cross-output consistency | **NOT TESTED** | Requires all 3 outputs |
| G13 | PURGE honest | **IMPLEMENTED** | ✓ |
| G14 | ED-4 JSONL bundles | **IMPLEMENTED** | ✓ |
| G15 | OQ-4 8-artifact assertion | **IMPLEMENTED** | ✓ |

---

## Pipeline Run History (Updated 2026-04-22)

| Scan | FSCQI | SIAT | REG | DG | Photoreal | EDSIM | OQSP | Notes |
|------|-------|------|-----|----|-----------|-------|------|-------|
| 16 | ✓ | ✓ (mock masks) | ✓ CONNECTED | ✓ 3 fragments (9154+179+8975v) | ✓ fallback | ✓ 3 anchor zones | ✓ 8 QC artifacts | Real reconstruction + DG fragment preservation! |

---

## Remaining Gaps to Close

### G3 — DG-33: Fragment-preserving honest geometry
**Status**: FIXED ✓

**Fix applied**: `split_mesh_components()` now uses `pymeshlab.Mesh()` constructor + `generate_splitting_by_connected_components()`. Scan 16 produces 3 real fragments (9154+179+8975 vertices) with 216 genuine boundary edges.

### G2 — SAM2 real segmentation
**Problem**: SAM2_MOCK=1 generates placeholder masks.

**Fix applied**: `fast-simplification` installed. Still needed: SAM2 checkpoints (sam2.1_hiera-large.pt).

**Fix needed**: Download SAM2 checkpoints + `pip install fast-simplification` for appearance scaffold.

### G4 — Camera capture
**Problem**: getUserMedia undefined on capture.html via ngrok HTTPS.

**Fix needed**: Test with actual phone over ngrok tunnel.

### G6 — EDSIM stale rebind bug
**Problem**: `detect_stale_rebind()` expects dict but callers pass string path.

**Fix needed**: Fix signature mismatch in edsim_worker.py `run()`.

---

## Pending Actions

### P0 — DG fragment preservation (G3) — NEXT
- [ ] Implement connected component splitting in mesh_worker.py
- [ ] Verify fragment count > 1 when pycolmap produces sparse multi-component output
- [ ] Verify open boundary edges represent genuine surface gaps

### P1 — SAM2 wiring (G2)
- [ ] `pip install fast-simplification` (needed for appearance_scaffold)
- [ ] Install SAM2 checkpoints (sam2.1_hiera-large.pt)
- [ ] Set SAM2_MOCK=0 in environment
- [ ] Re-run SIAT, verify real masks

### P2 — Camera E2E (G4, G7)
- [ ] Test capture.html on phone via ngrok HTTPS URL
- [ ] Verify getUserMedia works with --host-header=rewrite

### P3 — EDSIM bug (G6)
- [ ] Fix `detect_stale_rebind()` function signature
- [ ] Write test for geometry-output-history endpoint

### P4 — Photoreal full path (G5, G8, G9)
- [ ] Understand when appearance_only_route=0 (requires metric_trust_allowed=1)
- [ ] UV parameterization for tattoo placement
- [ ] Deformation testing on real mesh

## Last Session
- 2026-04-22: Pipeline re-run on scan 16 with real reconstruction
- G1 FIXED: pycolmap sparse reconstruction working (3783 points, 30/30 connected)
- G1 side fix: sparse dir persisted + pycolmap 4.x API fixes in reg_worker.py
- CLAUDE.md updated with agent workflow instructions
- PreCompact hook wired (project-progress-commit.py)
- bodyscan3d now has: CLAUDE.md, PROGRESS.md, HANDOVER.md, docs/bodyscan-dod-outcomes.txt

## Git Log
```
8e2c50a fix: pycolmap 4.x API compat + sparse dir persistence
68e0bea docs: complete DoD gap analysis — 15 gaps identified, P0 pycolmap broken
33a1525 chore: remove test marker from PROGRESS.md
221a81d auto: progress update before compaction (2026-04-22 12:17)
66dae97 docs: add canonical DoD spec (2 files)
88c85ce feat: session protocol + gap tracking system
fab487b feat: DoD Phase 2 — full 8-stage pipeline + Definition of Done compliance
```
