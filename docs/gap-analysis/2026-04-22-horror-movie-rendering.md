# GAP ANALYSIS — Bodyscan3D vs DoD
**Date:** 2026-04-22
**Trigger:** 3D viewer shows horror-movie quality rendering (malformed geometry, no texture)
**Severity:** CRITICAL — rendering broken

---

## ROOT CAUSE HYPOTHESIS

The rendering is broken because **all three stages fail in sequence**:

1. **DG** runs Poisson on an unfiltered point cloud (no SIAT mask applied) with depth=8 → spurious geometry from background points and specular noise merged into the mesh
2. **Laplacian smoothing** at step=1 then collapses fine features (fingers, facial contours) into a blob
3. **PHOTOREAL** has no texture pipeline — it renders the naked smoothed blob with a solid color, producing the horror-movie wireframe

---

## CRITICAL (rendering broken / data loss)

### [DG-2] SIAT subject masks not applied to surface reconstruction
- **Severity:** CRITICAL
- **Location:** `workers/mesh_worker.py` — loads `raw_pointcloud.glb` directly into Poisson
- **DoD:** SI-1 (subject isolation prior for downstream), R12
- **Current behavior:** SIAT produces `target_core_mask` (hard binary) and `alpha_soft` (soft boundary) but these are never used to filter the point cloud before reconstruction. Background walls, ceiling, floor get incorporated into the mesh.
- **Fix:** Apply SIAT core mask to filter the point cloud before Poisson reconstruction

### [DG-1] Poisson reconstruction depth=8 hardcoded
- **Severity:** CRITICAL
- **Location:** `workers/mesh_worker.py` line 193: `ms.generate_surface_reconstruction_screened_poisson(depth=8)`
- **DoD:** R12 (body-part difficulty is first-class input), R6 (backend absorbs complexity)
- **Current behavior:** No adaptation for body-part difficulty. For weakly-featured skin regions, depth=8 is too aggressive and produces geometrically incorrect surfaces.
- **Fix:** Adapt depth parameter based on point cloud density; lower depth for body scans

### [RECONSTRUCT-1] Dense MVS fused.ply loaded as point cloud, not surface mesh
- **Severity:** CRITICAL
- **Location:** `workers/reconstruct_worker.py` lines 149-161
- **DoD:** R-OUT-2 (surface-parameterized representation), O2 UV-mapped mesh
- **Current behavior:** `trimesh.load(fused_ply)` produces a PointCloud, not a mesh. This feeds DG's Poisson with unfiltered 3D noise/outliers.
- **Fix:** Run Statistical Outlier Removal on point cloud before Poisson; consider filter passing through the SIAT mask here

### [PHOTOREAL-1] No texture/appearance data in output mesh
- **Severity:** CRITICAL
- **Location:** `workers/mesh_worker.py` line 263: `mesh.export(final_glb)` — trimesh export has no UV coords, no vertex colors, no texture maps
- **DoD:** R-OUT-1 (photo-like appearance with skin tone/texture/lighting), R7
- **Current behavior:** Viewer renders naked grey mesh. No texture pipeline exists.
- **Fix:** Wire UV parameterization to texture projection; project source images onto mesh using camera poses

### [PHOTOREAL-2] View synthesis falls back to bare geometry
- **Severity:** CRITICAL
- **Location:** `workers/photoreal_worker.py` lines 475-478
- **DoD:** R-OUT-1, VW-1 (view-capable realization with rendered views)
- **Current behavior:** When camera poses unavailable, copies `model.glb` as view bundle — naked geometry with no appearance
- **Fix:** Ensure camera pose loading works; if poses unavailable, use canonical view synthesis instead of geometry copy

### [DG-3] Laplacian smoothing destroys fine geometry
- **Severity:** CRITICAL
- **Location:** `workers/mesh_worker.py` line 204: `ms.apply_coord_laplacian_smoothing(stepsmoothnum=1)`
- **DoD:** R-OUT-3 (deformable proxy must preserve geometry for edit), R12
- **Current behavior:** Applied uniformly — collapses fingers, facial contours, other high-curvature features
- **Fix:** Disable or reduce Laplacian smoothing; apply only to non-anchor zones

---

## HIGH (wrong behavior, degrades quality)

### [DG-5] Appearance scaffold decimated to 15% with no texture
- **Location:** `workers/mesh_worker.py` line 275: `target_faces = max(4, int(len(mesh.faces) * 0.15))`
- **DoD:** VW-1, R-OUT-1
- Grey decimated mesh is not a usable appearance proxy

### [DG-4] UV parameterization computed but never used
- **Location:** `workers/mesh_worker.py` line 312 — `model_uv.glb` saved separately, never used for texture projection
- **DoD:** R-OUT-2 (UV-mapped polygonal mesh)

### [PHOTOREAL-3] Lineage fingerprint uses metadata hashes, not binary content
- **Location:** `workers/photoreal_worker.py` lines 45-138
- **DoD:** CC-1 (content-addressed lineage), OQ-2

### [REG-1] Scale always RELATIVE, never METRIC
- **Location:** `workers/reg_worker.py` lines 150-152
- **DoD:** RG-2 (metric trust only when calibration target present)
- No detection of whether calibration target was used

### [REG-2] No quality gate before DG stage
- **Location:** `workers/reg_worker.py`
- **DoD:** RG-1 (registration_state required output)
- Proceeds even with `connected_fraction = 0.0`

### [SIAT-1] Ambiguity_tags and occlusion_labels not persisted to server
- **Location:** `workers/siat_worker.py` lines 335-343, 496-509
- **DoD:** SI-1 required outputs

### [SIAT-2] Candidate tier frames never used as secondary input
- **Location:** `workers/siat_worker.py` line 436: only `primary_tier` processed
- **DoD:** SI-1, R-OUT-1

### [FSCQI-1] 7-step curation pipeline reduced to blur + centroid/spread
- **Location:** `workers/fscqi_worker.py`
- **DoD:** FSCQI output contract, FSL-AC-1 through FSL-AC-15
- Full 7-step hybrid pipeline not implemented

### [OQSP-1] OQ-6 deterministic replay verification is a skip
- **Location:** `workers/oqsp_worker.py` line 322-324
- **DoD:** OQ-6

### [OQSP-2] artifact-versions endpoint does not exist
- **Location:** `workers/oqsp_worker.py` line 303
- **DoD:** OQ-2, CC-1

---

## MEDIUM

### [DG-6] Severe_geometry_concern always 0
### [DG-7] Usefulness zones use centroid-distance heuristic, not placement analysis
### [PHOTOREAL-4] Camera pose loading fails silently
### [RECONSTRUCT-2] No IMU/AR priors for metric scale recovery
### [REG-3] Frame ID mapping uses fragile string matching
### [SIAT-3] Prompt anchor only first frame, not per-region best
### [FSCQI-2] Coverage grid 4x4, not DoD-specified 12-24 sectors
### [OQSP-3] 8-artifact structure not fully validated

---

## LOW

### [SIAT-4] EDGE_BAND_PX=8 hardcoded, not adaptive to resolution
### [DG-8] Decimation threshold 100K faces hardcoded
### [PHOTOREAL-5] Viewport resolution 640x480 too low for photoreal quality
### [RECONSTRUCT-3] Docker timeout 60 min may trigger silent sparse fallback

---

## PRIORITY FIX ORDER

1. **Apply SIAT core mask to point cloud before Poisson** — eliminates background artifacts
2. **Add Statistical Outlier Removal** before reconstruction
3. **Disable or reduce Laplacian smoothing** (`stepsmoothnum=0`)
4. **Wire UV parameterization to texture projection** in PHOTOREAL
5. **Adapt Poisson depth** based on point cloud density

---

## WHY PRIOR GAP ANALYSES MISSED THESE

*(To be filled in — compare with prior gap analysis files if any exist)*

Prior sessions ran gap analysis but findings were:
- Stored only in session context (lost on compaction)
- Not systematically committed to the codebase
- Focus was on getting the pipeline running end-to-end, not on output quality

**Prevention:** All future gap analyses must be saved to `docs/gap-analysis/` with date + trigger in filename.
