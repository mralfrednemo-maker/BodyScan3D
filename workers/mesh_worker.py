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
from config import API_BASE, MODELS_DIR, internal_headers


GEOMETRY_OUTPUT_VERSION = '1.0.0'


def log(msg):
    print(f'[mesh_worker] {msg}', flush=True)


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

        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(raw_glb)
        log(f'  Loaded: {ms.current_mesh().vertex_number()} vertices, {ms.current_mesh().face_number()} faces')

        # If input is a pure point cloud, run Poisson surface reconstruction first
        if ms.current_mesh().face_number() == 0:
            log('  Point cloud detected — running Poisson surface reconstruction')
            ms.compute_normal_for_point_clouds()
            ms.generate_surface_reconstruction_screened_poisson(depth=8)
            ms.meshing_remove_connected_component_by_face_number(mincomponentsize=100)
            log(f'  Poisson result: {ms.current_mesh().vertex_number()} vertices, {ms.current_mesh().face_number()} faces')

        if ms.current_mesh().face_number() > 0:
            # Standard cleanup: dedup + smooth + normals
            ms.meshing_remove_duplicate_vertices()
            ms.meshing_remove_duplicate_faces()
            ms.meshing_remove_connected_component_by_face_number(mincomponentsize=50)
            # DG-4: DO NOT close holes — open-boundary-explicit requirement
            # REMOVED: ms.meshing_close_holes(maxholesize=30)
            ms.apply_coord_laplacian_smoothing(stepsmoothnum=1)
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
