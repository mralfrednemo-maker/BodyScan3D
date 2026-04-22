# BodyScan3D — DoD Gap Analysis
**Date:** 2026-04-21
**Newton session:** 2026-04-21 18:50 Athens
**DoD:** C:\Users\chris\Downloads\BODYSCAN-DOD.txt

---

## What Exists (Working)

| Component | Status | Notes |
|-----------|--------|-------|
| Video keyframe extraction (video_worker.py) | ✅ Working | Two-pass score + bin-based selection, prevents clustering |
| Blur QA (frame_qa.py) | ✅ Working | Laplacian variance scoring, auto anchor selection |
| SAM2 masking (mask_worker.py) | ✅ Working | Real SAM2 + mock fallback, body-part rules |
| pycolmap sparse/dense reconstruction | ✅ Working | Docker CUDA for dense MVS, graceful CPU fallback |
| PyMeshLab mesh cleanup (mesh_worker.py) | ✅ Working | Poisson surface reconstruction for point clouds |
| Token-auth capture sessions | ✅ Working | 4hr expiry, capture.html with camera + auto-mode |
| Failure classification | ✅ Working | FAILURE_CLASSES map in server.js |
| Pipeline orchestrator (pipeline.py) | ✅ Working | State-based dispatch, polling |
| SQLite persistence (server.js) | ✅ Working | WAL mode, foreign keys, safe migrations |

---

## What's Missing (by DoD Section)

### §3 Whole-System
- **Workflow-alive asset family** — outputs exist but no canonical asset-family identity, no append-only lineage, no cross-output consistency contract
- **Deterministic regeneration** — not implemented; same capture could produce different outputs
- **Self-hosted latency** — no SLA defined or measured
- **Telemetry tripwires** — no intervention/retry/patch counters

### §4 Capture UX (CX-1..CX-11)
- Four-state review outcome UI NOT implemented (only "Capture complete" done screen)
- Patch capture offer NOT implemented
- Bounded M2 fallback NOT implemented (auto-mode only)
- Intervention counter telemetry NOT implemented
- M1/M2 ratio telemetry NOT implemented
- Downstream provenance context in mobile bundle NOT wired

### §5 Mobile Pipeline (MB-1..MB-10)
- SHA-256 per-frame hashing NOT implemented
- Bundle-level Merkle root NOT computed
- Resumable upload NOT implemented (interrupted uploads require re-capture)
- No per-frame hash verification endpoint
- Backend durable-persistence acknowledgment incomplete
- Device privacy enforcement minimal

### §6 FSCQI — ENTIRELY NEW
- Only blur scoring exists (part of frame_qa.py)
- **Missing:** curated primary tier, extended candidate tier, raw reference map, dual-level coverage descriptor, weak-region register, capture-health summary, `fscqi_bundle_version`, four-state processability verdict

### §7 SIAT — ENTIRELY NEW
- SAM2 masks exist but not packaged as SIAT artifacts
- **Missing:** `target_alpha_soft`, `target_core_mask`, `target_mask_hard`, `static_rigid_core`/`pose_safe_support_mask`, `boundary_confidence_channel`, ambiguity tags, occlusion labels
- No ambiguity-preserving boundary representation

### §8 REG — ENTIRELY NEW
- pycolmap outputs point clouds but not packaged as REG
- **Missing:** per-observation intrinsics/extrinsics, registration graph, `pose_version`, scale regime + confidence band, `metric_trust_allowed` (defaults false), `feature_support_regime`

### §9 DG — ENTIRELY NEW
- Mesh cleanup exists (Poisson + decimation) but not DG-compliant
- **Missing:** `geometry_version`, `surface_fragment_set`, `hole_and_open_boundary_map`, `surface_usefulness_zones`, structural_proxy/appearance_scaffold split, `severe_geometry_concern` flag
- Currently "closes holes" — violates open-boundary-explicit requirement

### §10 Photoreal — ENTIRELY NEW
- GLB output exists but no `view_version`, no viewing contract, no lineage-addressable bundle
- **Missing:** lineage-addressable `view_version`, view bundle, pipeline fingerprint, explicit appearance-only route
- No degradation-without-tears behavior defined

### §11 EDSIM — ENTIRELY NEW
- Placement/preview not distinguished in current system
- **Missing:** `placement_authority_map.json`, `preview_authority_map.json`, `appearance_only_routes.json`, `stale_rebind_register.json`, refusal-zone files, all 13 EDSIM artifacts
- No `auto_rebind_identical` enforcement

### §12 OQSP — ENTIRELY NEW
- No publish layer exists
- **Missing:** `publish_manifest.json`, 8-artifact QC set, `publishability_class`, layered publishing, dependency-scoped invalidation, append-only state transitions
- External publish gate NOT enforced

### §13 Cross-Cutting
- **Append-only lineage** — NOT implemented; artifacts overwritten in place
- **Content-addressed storage** — NOT implemented; flat `uploads/` structure
- **Version fields** — no `fscqi_bundle_version`, `isolation_version`, `pose_version`, `geometry_version`, `view_version`, `edit_sim_version` in DB
- **Purge workflow** — NOT implemented
- **Encryption at rest** — NOT implemented (all artifacts plaintext)
- **Role-scoped access** — basic auth exists, audit logging sparse
- **Telemetry tripwires** — NOT implemented
- **Deterministic replay** — NOT implemented
- **"Close but slightly shifted"** — NOT defined as failure

### §14 Release Gates
- Publish gate NOT wired (no honest view-capable route check)
- Internal validity incorrectly requires all outputs non-empty (rejected by DoD majority)
- No `publishability_class` defined

### §15 Evidence Matrix
- **All EV-1..EV-11 tests MISSING** — no verification suite exists

---

## Gap Severity Summary

| Severity | Count | Sections |
|----------|-------|----------|
| 🔴 Entirely new subsystem | 8 | FSCQI, SIAT, REG, DG, Photoreal, EDSIM, OQSP, Verification Suite |
| 🟠 Major missing infrastructure | 3 | Append-only lineage, Encryption, Telemetry |
| 🟡 Missing UX/pipeline behavior | 4 | Capture UX outcomes, Mobile integrity, Purge/rebind, Deterministic replay |
| 🟢 Existing and working | 6 | video_worker, frame_qa, mask_worker, reconstruct, mesh, pipeline, auth |

---

## DoD-Explicit Non-Go Items Present in Codebase

1. **"Closed" holes in mesh_worker.py** — `ms.meshing_close_holes(maxholesize=30)` violates DG-4 open-boundary-explicit requirement
2. **No `metric_trust_allowed` default false** — pycolmap outputs could be treated as metric-valid by default
3. **No weak-region propagation** — FSCQI weak-region signal dropped before downstream use
4. **No four-state processability verdict** — frame_qa.py only outputs accepted/rejected
