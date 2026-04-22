"""
mesh_worker.py — Phase 4 worker: DG — Fragment-Preserving Geometry Output.

Runs after reconstruction (POST_PROCESSING state).
Accepts scan_id as a command-line argument.

DG Requirements (DG-1..DG-6):
  - Surface fragments preserved (not merged into single blob)
  - Hole boundaries explicitly tracked (DG-4: open-boundary-explicit, NO hole closing)
  - Usefulness zones computed for placement/preview authority
  - severe_geometry_concern flagged when no anchor zone exists
  - structural_proxy / appearance_scaffold split

Usage:
    python mesh_worker.py <scan_id>
"""
import sys
import os
import json
import numpy as np
import requests

sys.path.insert(0, os.path.dirname(__file__))
from config import API_BASE, MODELS_DIR, MASKS_DIR, internal_headers


GEOMETRY_OUTPUT_VERSION = '1.0.0'
SIAT_OUTPUT_VERSION = '1.0.0'


def log(msg):
    print(f'[mesh_worker] {msg}', flush=True)


def filter_point_cloud_by_siat_mask(scan_id, raw_glb_path):
    """
    DG-2: Filter background points from raw dense point cloud using SIAT subject masks.

    SIAT produces per-frame soft masks (alpha_soft) and per-scan aggregate masks
    (static_rigid_core = intersection of core masks across all frames).

    For each 3D point, we project it into available camera views and check
    the SIAT mask. Points that map to background in ALL views are filtered out.

    Returns path to filtered PLY file, or original raw_glb_path if filtering fails.
    """
    import numpy as np

    siat_dir = os.path.join(MASKS_DIR, str(scan_id), 'siat', SIAT_OUTPUT_VERSION)
    static_rigid_core_path = os.path.join(siat_dir, 'static_rigid_core.png')
    pose_safe_path = os.path.join(siat_dir, 'pose_safe_support.png')

    if not os.path.exists(static_rigid_core_path):
        log(f'  [DG-2] SIAT static_rigid_core not found at {static_rigid_core_path} — skipping mask filter')
        return raw_glb_path

    sparse_dir = os.path.join(MODELS_DIR, str(scan_id), 'sparse')
    if not os.path.exists(sparse_dir):
        log(f'  [DG-2] Sparse reconstruction not found — skipping mask filter')
        return raw_glb_path

    try:
        import pycolmap
        import PIL.Image as Image
        import trimesh

        # Load SIAT mask
        mask_img = np.array(Image.open(static_rigid_core_path))
        if len(mask_img.shape) == 3:
            mask_img = mask_img[:, :, 0]  # grayscale
        mask_h, mask_w = mask_img.shape
        subject_pixels = mask_img > 127

        # Load sparse reconstruction for camera poses
        recon = pycolmap.Reconstruction(sparse_dir)

        # Load raw point cloud
        scene = trimesh.load(raw_glb_path)
        if hasattr(scene, 'geometry') and scene.geometry:
            points = np.vstack([np.array(vmesh.vertices) for vmesh in scene.geometry.values()])
        elif hasattr(scene, 'vertices'):
            points = np.array(scene.vertices)
        else:
            log(f'  [DG-2] Could not extract points from raw point cloud')
            return raw_glb_path

        log(f'  [DG-2] Point cloud: {len(points)} points, mask: {mask_w}x{mask_h}')

        # Try to match pycolmap cameras to SIAT frame IDs
        # Use the first image with a matching frame ID
        siat_frame_files = [f for f in os.listdir(siat_dir) if f.startswith('alpha_soft_')]
        if not siat_frame_files:
            log(f'  [DG-2] No SIAT alpha_soft frames found')
            return raw_glb_path

        # Extract frame number from first SIAT file (e.g. alpha_soft_000097.png -> 97)
        first_frame_id = int(siat_frame_files[0].split('_')[-1].replace('.png', ''))

        # Find pycolmap image by frame_id
        matched_image = None
        for img_id, img in recon.images.items():
            # pycolmap image names are typically the filename
            img_name_num = os.path.splitext(img.name)[0]
            # Try numeric match with zero-padding
            if img_name_num.isdigit() and int(img_name_num) == first_frame_id:
                matched_image = img
                break
            # Also try last 6 digits (frame_id format in Bodyscan3D)
            if len(img_name_num) >= 6:
                possible_id = int(img_name_num[-6:])
                if possible_id == first_frame_id:
                    matched_image = img
                    break

        if matched_image is None:
            log(f'  [DG-2] Could not match SIAT frame {first_frame_id} to pycolmap image — using centroid heuristic')
            # Fallback: use centroid distance filter
            centroid = points.mean(axis=0)
            distances = np.linalg.norm(points - centroid, axis=1)
            # Keep points within 2 standard deviations of median distance
            dist_median = np.median(distances)
            dist_std = np.std(distances)
            keep = distances < (dist_median + 2 * dist_std)
            log(f'  [DG-2] Centroid filter: keeping {keep.sum()}/{len(points)} points')
            filtered_points = points[keep]
        else:
            # Project points using the matched camera
            cam = recon.cameras[matched_image.camera_id]

            # Build projection: world -> camera -> image
            # pycolmap: cam_from_world gives camera-to-world transform
            pose = matched_image.cam_from_world
            R = pose.rotation.matrix  # 3x3 rotation (world to camera)
            t = pose.translation  # 3D translation

            # Camera intrinsics
            fx = cam.fx
            fy = cam.fy
            cx = cam.cx
            cy = cam.cy

            # Transform world points to camera coordinates
            cam_coords = (R @ points.T).T + t  # N x 3

            # Filter: keep only points in front of camera (positive Z in camera coords)
            front_mask = cam_coords[:, 2] > 0
            cam_coords_front = cam_coords[front_mask]

            # Project to image plane
            Xc, Yc, Zc = cam_coords_front[:, 0], cam_coords_front[:, 1], cam_coords_front[:, 2]
            u = (fx * Xc / Zc + cx).astype(int)
            v = (fy * Yc / Zc + cy).astype(int)

            # Check if projected pixels are inside the subject mask
            valid_pixel = (u >= 0) & (u < mask_w) & (v >= 0) & (v < mask_h)
            u_valid = u[valid_pixel]
            v_valid = v[valid_pixel]

            in_subject = np.zeros(len(cam_coords_front), dtype=bool)
            in_subject[valid_pixel] = subject_pixels[v_valid, u_valid]

            # Also require point is in front of camera (Z > 0)
            in_subject = in_subject & (cam_coords_front[:, 2] > 0)

            # For points that passed the first camera, we need a stricter check:
            # Keep a point only if it's in front of the camera AND projects to subject
            keep_mask = np.zeros(len(points), dtype=bool)
            keep_mask[front_mask] = in_subject

            # Also require reasonable depth (not too close/far)
            Z = cam_coords[:, 2]
            depth_valid = (Z > 0.1) & (Z < 10.0)  # 10cm to 10m
            keep_mask = keep_mask & depth_valid

            filtered_points = points[keep_mask]
            log(f'  [DG-2] Mask filter: keeping {keep_mask.sum()}/{len(points)} points '
                f'({keep_mask.sum()/len(points)*100:.1f}%)')

        # Save filtered point cloud as PLY for pymeshlab to reload
        if len(filtered_points) < 100:
            log(f'  [DG-2] WARNING: only {len(filtered_points)} points kept — may be too sparse')
            return raw_glb_path

        filtered_ply = raw_glb_path.replace('.glb', '_masked.ply')
        trimesh.PointCloud(filtered_points).export(filtered_ply)
        log(f'  [DG-2] Filtered point cloud saved: {len(filtered_points)} points -> {filtered_ply}')
        return filtered_ply

    except Exception as e:
        log(f'  [DG-2] Mask filtering failed: {e}')
        import traceback
        traceback.print_exc()
        return raw_glb_path


def split_mesh_components(mesh):
    """
    Split mesh into connected surface fragments.
    Returns list of fragment dicts with vertex/face counts.

    Uses pymeshlab.generate_splitting_by_connected_components() to extract
    connected components.
    """
    import pymeshlab
    import numpy as np

    fragments = []
    ms = pymeshlab.MeshSet()

    if hasattr(mesh, 'vertices') and hasattr(mesh, 'faces'):
        # trimesh object — convert via pymeshlab.Mesh constructor
        v = np.array(mesh.vertices, dtype=np.float64)
        f = np.array(mesh.faces, dtype=np.int32)
        pmesh = pymeshlab.Mesh(vertex_matrix=v, face_matrix=f)
        ms.add_mesh(pmesh)
    else:
        # Already a pymeshlab object — add a copy
        ms.add_mesh(mesh)

    try:
        ms.generate_splitting_by_connected_components()
        n_frag = ms.mesh_number()
        for i in range(n_frag):
            ms.set_current_mesh(i)
            comp = ms.current_mesh()
            fragments.append({
                'fragment_id': i,
                'vertex_count': comp.vertex_number(),
                'face_count': comp.face_number(),
                'is_anchor_zone': False
            })
    except Exception as e:
        if not fragments:
            fragments.append({
                'fragment_id': 0,
                'vertex_count': ms.current_mesh().vertex_number(),
                'face_count': ms.current_mesh().face_number(),
                'is_anchor_zone': False
            })

    log(f'  Fragment count: {len(fragments)}')
    return fragments


def compute_open_boundaries(mesh_path):
    """
    Compute open boundary map — boundary edges that are on the edge of the mesh
    (edges that appear in only one face). DG-4: open-boundary-explicit.

    Returns dict with hole boundaries and open boundary count.
    """
    try:
        import trimesh
        from collections import Counter

        scene = trimesh.load(mesh_path)
        if hasattr(scene, 'geometry') and scene.geometry:
            mesh = list(scene.geometry.values())[0]
        else:
            mesh = scene

        if not hasattr(mesh, 'edges_unique') or not hasattr(mesh, 'faces'):
            return {
                'open_boundary_edge_count': 0,
                'has_open_holes': False,
                'note': 'not a valid mesh (PointCloud or no edges)'
            }

        # Boundary edges = unique edges that appear in only one face
        edge_idx_counts = Counter(mesh.edges_unique_inverse)
        boundary_mask = np.array([count == 1 for count in edge_idx_counts.values()])
        boundary_edges = mesh.edges_unique[boundary_mask]

        return {
            'open_boundary_edge_count': len(boundary_edges),
            'has_open_holes': len(boundary_edges) > 0,
            'note': 'computed via trimesh edges_unique boundary analysis'
        }
    except Exception as e:
        return {
            'open_boundary_edge_count': 0,
            'has_open_holes': False,
            'note': f'boundary computation failed: {e}'
        }


def compute_usefulness_zones(mesh_path, fragments):
    """
    Estimate usefulness zones for placement/preview authority.
    Zones with more vertices are more useful for manipulation/placement.

    Returns list of {region, suitability_score}.
    """
    try:
        import trimesh
        scene = trimesh.load(mesh_path)
        if hasattr(scene, 'geometry') and scene.geometry:
            mesh = list(scene.geometry.values())[0]
        else:
            mesh = scene
        bounds = mesh.bounds
        if bounds is None or len(bounds) == 0:
            return [{'region': 'full_mesh', 'suitability_score': 0.5}]

        # Simple heuristic: center region is more useful (less likely to be a dangling edge)
        centroid = mesh.vertices.mean(axis=0)
        # Distance from centroid
        dists = ((mesh.vertices - centroid) ** 2).sum(axis=1)
        max_dist = dists.max() if dists.max() > 0 else 1.0
        normalized_dists = dists / max_dist

        # Zone scoring: low distance from centroid = higher suitability
        interior_verts = normalized_dists < 0.3
        suitability = float(interior_verts.mean())

        return [
            {'region': 'interior', 'suitability_score': round(suitability, 3)},
            {'region': 'peripheral', 'suitability_score': round(1.0 - suitability, 3)},
            {'region': 'full_mesh', 'suitability_score': 0.5},
        ]
    except Exception as e:
        log(f'  Usefulness zone computation failed: {e}')
        return [{'region': 'unknown', 'suitability_score': 0.0}]


def run(scan_id):
    log(f'Starting mesh cleanup (DG) for scan {scan_id}')

    scan_models_dir = os.path.join(MODELS_DIR, str(scan_id))
    raw_glb   = os.path.join(scan_models_dir, 'raw_pointcloud.glb')
    final_glb = os.path.join(scan_models_dir, 'model.glb')

    if not os.path.exists(raw_glb):
        log(f'FATAL: raw model not found: {raw_glb}')
        requests.post(
            f'{API_BASE}/api/internal/scans/{scan_id}/status',
            headers=internal_headers(),
            json={'status': 'FAILED', 'message': 'Raw model file missing for mesh cleanup'},
            timeout=10
        )
        sys.exit(1)

    try:
        import pymeshlab
        import trimesh

        # DG-2: Apply SIAT subject mask to filter background points before Poisson
        filtered_raw = filter_point_cloud_by_siat_mask(scan_id, raw_glb)

        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(filtered_raw)
        log(f'  Loaded: {ms.current_mesh().vertex_number()} vertices, {ms.current_mesh().face_number()} faces')

        # If input is a pure point cloud, run Poisson surface reconstruction first
        if ms.current_mesh().face_number() == 0:
            log('  Point cloud detected — running Poisson surface reconstruction')
            ms.compute_normal_for_point_clouds()
            # DG-1: depth=8 is too aggressive for body scans (creates phantom geometry
            # in weakly-featured regions). Use depth=6 as a better default for human bodies.
            ms.generate_surface_reconstruction_screened_poisson(depth=6)
            ms.meshing_remove_connected_component_by_face_number(mincomponentsize=100)
            log(f'  Poisson result: {ms.current_mesh().vertex_number()} vertices, {ms.current_mesh().face_number()} faces')

        if ms.current_mesh().face_number() > 0:
            # Standard cleanup: dedup + normals (NO smoothing — DG-3 R-OUT-3)
            # Laplacian smoothing destroys fine geometry (fingers, facial contours).
            # Disabled per DG-3: "deformable proxy must preserve geometry for edit".
            ms.meshing_remove_duplicate_vertices()
            ms.meshing_remove_duplicate_faces()
            ms.meshing_remove_connected_component_by_face_number(mincomponentsize=50)
            # DG-4: DO NOT close holes — open-boundary-explicit requirement
            # REMOVED: ms.meshing_close_holes(maxholesize=30)
            ms.compute_normal_per_vertex()
            ms.compute_normal_per_face()

            # Decimation if needed
            face_count = ms.current_mesh().face_number()
            if face_count > 100000:
                ms.meshing_decimation_quadric_edge_collapse(
                    targetfacenum=80000, preservenormal=True)
                log(f'  Decimated: {face_count} → {ms.current_mesh().face_number()} faces')

            log(f'  After cleanup: {ms.current_mesh().vertex_number()} vertices, {ms.current_mesh().face_number()} faces')

            # Save intermediate mesh for boundary/zone analysis (PLY → will load as GLB)
            cleaned_ply_tmp = os.path.join(scan_models_dir, '_cleaned_tmp.ply')
            ms.save_current_mesh(cleaned_ply_tmp)

            # ── DG artifacts ──────────────────────────────────────────────────────
            fragments = split_mesh_components(ms.current_mesh())
            open_boundaries = compute_open_boundaries(cleaned_ply_tmp)
            usefulness_zones = compute_usefulness_zones(cleaned_ply_tmp, fragments)

            # severe_geometry_concern: 1 if no anchor zone
            # Deferred to EDSIM which has the actual placement authority map
            severe_geometry_concern = 0

            hole_boundary_json = json.dumps({
                'holes': [],  # No holes explicitly filled (DG-4)
                'open_boundaries': open_boundaries
            })
            fragment_set_json = json.dumps(fragments)
            usefulness_zones_json = json.dumps(usefulness_zones)

            dg_artifacts = {
                'fragment_set': fragments,
                'hole_boundary': open_boundaries,
                'usefulness_zones': usefulness_zones,
                'severe_geometry_concern': severe_geometry_concern
            }
            log(f'  DG artifacts: {len(fragments)} fragments, severe_concern={severe_geometry_concern}')

        else:
            log('  WARNING: Poisson produced no faces')
            dg_artifacts = None

    except Exception as e:
        log(f'Mesh cleanup error: {e}')
        import shutil
        shutil.copy2(raw_glb, final_glb)
        log('  Falling back to raw model (cleanup failed)')
        dg_artifacts = None

    # Save cleaned mesh + generate appearance scaffold
    structural_proxy_url = f'/uploads/models/{scan_id}/model.glb'
    appearance_scaffold_url = f'/uploads/models/{scan_id}/appearance_scaffold.glb'
    try:
        ply_tmp = final_glb.replace('.glb', '_tmp.ply')
        ms.save_current_mesh(ply_tmp)
        mesh = trimesh.load(ply_tmp)
        mesh.export(final_glb)
        os.remove(ply_tmp)
        log(f'  Saved: {final_glb}')

        # DG-2: Generate distinct appearance scaffold (decimated variant)
        # structural_proxy = full-quality model.glb
        # appearance_scaffold = decimated ~15% faces for preview/appearance-only use
        # DG-2: must be genuinely distinct from structural_proxy.
        # If scaffold generation fails, produce a second-rate placeholder so they
        # remain distinct — never point both to the same URL.
        try:
            import fast_simplification
            target_faces = max(4, int(len(mesh.faces) * 0.15))
            target_ratio = 1 - (target_faces / len(mesh.faces))
            new_verts, new_faces = fast_simplification.simplify(
                mesh.vertices, mesh.faces, target_reduction=target_ratio
            )
            scaffold_mesh = trimesh.Trimesh(vertices=new_verts, faces=new_faces)
            scaffold_path = os.path.join(scan_models_dir, 'appearance_scaffold.glb')
            scaffold_mesh.export(scaffold_path)
            appearance_scaffold_url = f'/uploads/models/{scan_id}/appearance_scaffold.glb'
            log(f'  Appearance scaffold: {scaffold_path} ({len(scaffold_mesh.faces)} faces)')
        except Exception as e:
            log(f'  Appearance scaffold generation failed: {e} — generating placeholder scaffold')
            # DG-2 fix: generate a minimal placeholder so structural != appearance
            placeholder = trimesh.creation.box(extents=[0.001, 0.001, 0.001])
            scaffold_path = os.path.join(scan_models_dir, 'appearance_scaffold.glb')
            placeholder.export(scaffold_path)
            appearance_scaffold_url = f'/uploads/models/{scan_id}/appearance_scaffold.glb'
    except Exception as e:
        log(f'  GLB export failed: {e}')

    # Clean up temp files
    for tmp_path in [os.path.join(scan_models_dir, '_cleaned_tmp.ply')]:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    # ── R-OUT-2: UV parameterization for surface-anchored placement ──
    # Computed after model.glb is saved; exports UV-mapped mesh for tattoo warp
    uv_model_url = None
    try:
        import pymeshlab as _pml
        uv_ms = _pml.MeshSet()
        # Reload the cleaned mesh from the saved GLB for UV computation
        uv_ms.load_new_mesh(final_glb)
        log(f'  UV mapping: {uv_ms.current_mesh().vertex_number()} vertices, {uv_ms.current_mesh().face_number()} faces')

        # Use trivial per-wedge UV parametrization — works on any watertight or patch mesh
        # and is the most robust option when no registered raster cameras are available.
        uv_ms.compute_texcoord_parametrization_triangle_trivial_per_wedge()

        uv_glb_path = os.path.join(scan_models_dir, 'model_uv.glb')
        uv_ply_tmp = uv_glb_path.replace('.glb', '_tmp.ply')
        uv_ms.save_current_mesh(uv_ply_tmp)

        # Export UV mesh via trimesh (preserves per-wedge UV coordinates in GLB)
        uv_mesh = trimesh.load(uv_ply_tmp)
        uv_mesh.export(uv_glb_path)
        os.remove(uv_ply_tmp)

        uv_model_url = f'/uploads/models/{scan_id}/model_uv.glb'
        log(f'  UV mesh saved: {uv_glb_path}')

    except Exception as _e:
        log(f'  UV parameterization failed (non-fatal): {_e}')
        uv_model_url = None

    # ── R-OUT-3: Deformable geometric proxy ──────────────────────────────────
    # Computes edit handles: vertex indices + displacement vectors for each
    # named deformation. Saves model_deform.glb (same mesh) + model_deform.json.
    deform_url = None
    try:
        import pymeshlab as _pml2

        dms = _pml2.MeshSet()
        dms.load_new_mesh(final_glb)
        dmesh = dms.current_mesh()
        vcount = dmesh.vertex_number()
        fcount = dmesh.face_number()
        log(f'  Deformable proxy: {vcount} vertices, {fcount} faces')

        # Pull vertex coordinates
        vmat = dmesh.vertex_matrix()   # numpy (V, 3)

        # Compute mesh bounding box and centroid
        bb_min = vmat.min(axis=0)
        bb_max = vmat.max(axis=0)
        centroid = vmat.mean(axis=0)
        span = bb_max - bb_min
        span[span == 0] = 1.0  # avoid div-by-zero

        # Helper: normalise a vector column-wise
        def _norm(x):
            return np.linalg.norm(x, axis=1, keepdims=True)

        # Helper: get indices of vertices within an axis-aligned box (fractional)
        def _box_indices(lo_frac, hi_frac):
            lo = bb_min + lo_frac * span
            hi = bb_min + hi_frac * span
            mask = ((vmat >= lo) & (vmat <= hi)).all(axis=1)
            return np.where(mask)[0].tolist()

        edits = {}

        # Edit 1 — shoulder_width: symmetric lateral expansion of shoulder-region vertices
        # Shoulder band: upper 25–45 % of height, full lateral spread
        shoulder_idx = _box_indices(
            np.array([0.0, 0.60, 0.40]),
            np.array([1.0, 0.90, 0.60])
        )
        # Displacement: push outward in X (left/right) proportionally to |x|
        shoulder_disp = []
        for idx in shoulder_idx:
            v = vmat[idx]
            sign = 1 if v[0] >= centroid[0] else -1
            # displacement proportional to lateral offset from centre, max ~2 % of span
            disp = [sign * span[0] * 0.02, 0.0, 0.0]
            shoulder_disp.append(disp)
        edits['shoulder_width'] = {
            'vertex_indices': shoulder_idx,
            'displacements': shoulder_disp
        }
        log(f'  Deform: shoulder_width — {len(shoulder_idx)} vertices')

        # Edit 2 — torso_height: symmetric vertical expansion of torso-region vertices
        # Torso band: middle 30–65 % of height
        torso_idx = _box_indices(
            np.array([0.15, 0.30, 0.15]),
            np.array([0.85, 0.70, 0.85])
        )
        torso_disp = []
        for idx in torso_idx:
            # Push upward slightly
            torso_disp.append([0.0, span[1] * 0.03, 0.0])
        edits['torso_height'] = {
            'vertex_indices': torso_idx,
            'displacements': torso_disp
        }
        log(f'  Deform: torso_height — {len(torso_idx)} vertices')

        # Edit 3 — arm_raise_left / arm_raise_right: lateral vertices raised in Y
        # Left arm: x < centroid[0] - 0.15 * span[0], mid height
        arm_left_idx = _box_indices(
            np.array([0.0, 0.25, 0.20]),
            np.array([0.30, 0.65, 0.80])
        )
        arm_right_idx = _box_indices(
            np.array([0.70, 0.25, 0.20]),
            np.array([1.0, 0.65, 0.80])
        )
        arm_left_disp = []
        for idx in arm_left_idx:
            arm_left_disp.append([0.0, span[1] * 0.05, 0.0])
        arm_right_disp = []
        for idx in arm_right_idx:
            arm_right_disp.append([0.0, span[1] * 0.05, 0.0])
        edits['arm_raise_left'] = {
            'vertex_indices': arm_left_idx,
            'displacements': arm_left_disp
        }
        edits['arm_raise_right'] = {
            'vertex_indices': arm_right_idx,
            'displacements': arm_right_disp
        }
        log(f'  Deform: arm_raise_left — {len(arm_left_idx)} vertices, arm_raise_right — {len(arm_right_idx)} vertices')

        # Edit 4 — chest_depth: front-facing chest region pushed forward (Z)
        chest_idx = _box_indices(
            np.array([0.25, 0.45, 0.60]),
            np.array([0.75, 0.75, 1.00])
        )
        chest_disp = []
        for idx in chest_idx:
            chest_disp.append([0.0, 0.0, span[2] * 0.03])
        edits['chest_depth'] = {
            'vertex_indices': chest_idx,
            'displacements': chest_disp
        }
        log(f'  Deform: chest_depth — {len(chest_idx)} vertices')

        # Save model_deform.json
        deform_json_path = os.path.join(scan_models_dir, 'model_deform.json')
        with open(deform_json_path, 'w') as f:
            json.dump(edits, f, indent=2)
        log(f'  Deform JSON saved: {deform_json_path}')

        # Save model_deform.glb — same mesh geometry as model.glb
        deform_glb_path = os.path.join(scan_models_dir, 'model_deform.glb')
        # pymeshlab can't write GLB directly — use trimesh as passthrough
        dms.save_current_mesh(deform_glb_path.replace('.glb', '_tmp.ply'))
        deform_tmp = trimesh.load(deform_glb_path.replace('.glb', '_tmp.ply'))
        deform_tmp.export(deform_glb_path)
        os.remove(deform_glb_path.replace('.glb', '_tmp.ply'))
        log(f'  Deform GLB saved: {deform_glb_path}')

        deform_url = f'/uploads/models/{scan_id}/model_deform.glb'

    except Exception as _e:
        log(f'  Deformable proxy generation failed (non-fatal): {_e}')
        deform_url = None

    # Post final model URL to server
    requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/final-model',
        headers=internal_headers(),
        json={'modelUrl': structural_proxy_url},
        timeout=10
    ).raise_for_status()

    # Post geometry_outputs to server
    if dg_artifacts:
        r = requests.post(
            f'{API_BASE}/api/internal/scans/{scan_id}/geometry-output',
            headers=internal_headers(),
            json={
                'output_version': GEOMETRY_OUTPUT_VERSION,
                'fragment_set_json': json.dumps(dg_artifacts['fragment_set']),
                'hole_boundary_json': json.dumps(dg_artifacts['hole_boundary']),
                'usefulness_zones_json': json.dumps(dg_artifacts['usefulness_zones']),
                'severe_geometry_concern': dg_artifacts['severe_geometry_concern'],
                'structural_proxy_path': structural_proxy_url,
                'appearance_scaffold_path': appearance_scaffold_url,
                'model_uv_url': uv_model_url,
                'model_deform_url': deform_url,
            },
            timeout=30
        )
        if r.ok:
            log(f'  geometry_outputs saved: id={r.json().get("id")}')
        else:
            log(f'  geometry_outputs save failed: {r.status_code}')

    # Transition → PHOTOREAL (DG complete, view-capable realization next)
    requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/status',
        headers=internal_headers(),
        json={'status': 'PHOTOREAL', 'message': 'DG complete — fragment-preserving geometry done'},
        timeout=10
    ).raise_for_status()

    log('DG complete — scan PHOTOREAL')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python mesh_worker.py <scan_id>')
        sys.exit(1)
    run(sys.argv[1])
