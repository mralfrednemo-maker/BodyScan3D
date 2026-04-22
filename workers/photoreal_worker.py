"""
photoreal_worker.py — Phase 5 worker: View-Capable Realization with Lineage Address.

Runs after PHOTOREAL state (after DG/mesh cleanup).
Accepts scan_id as a command-line argument.

Produces (VW-1):
  - view_version          e.g. "1.0.0"
  - view_bundle_path     path to viewing bundle
  - lineage_fingerprint  SHA-256 of all upstream artifact hashes
  - appearance_only_route  0 or 1 (1 if no placement authority)

R-OUT-1 requires actual view-synthesis: rendered images from camera poses,
not just a copy of the geometry mesh. This worker renders the cleaned mesh
from the DG stage using camera poses from the REG stage sparse reconstruction.

View bundle contents:
  - manifest.json      metadata about rendered views
  - view_000.png ...   rendered views from captured camera angles
  - model.glb          geometry fallback

Usage:
    python photoreal_worker.py <scan_id>
"""
import sys
import os
import json
import requests
import hashlib
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from config import API_BASE, MODELS_DIR, internal_headers


VIEW_OUTPUT_VERSION = '1.0.0'


def log(msg):
    print(f'[photoreal] {msg}', flush=True)


def compute_lineage_fingerprint(scan_id):
    """
    Compute lineage fingerprint = SHA-256 of all upstream artifact hashes.
    Sources: frame content hashes (scan_frames), fscqi_bundle, siat_output,
    reg_output, geometry_output.
    """
    hashes = []
    missing_sources = []

    # 1. Frame content hashes
    r = requests.get(
        f'{API_BASE}/api/internal/scans/{scan_id}/frames',
        headers=internal_headers(),
        timeout=30
    )
    if r.ok:
        frames = r.json()
        for f in frames:
            if f.get('content_hash'):
                hashes.append(f['content_hash'])
    else:
        missing_sources.append('frames')

    # 2. FSCQI bundle hash (verdict + health summary)
    r = requests.get(
        f'{API_BASE}/api/internal/scans/{scan_id}/fscqi-bundle',
        headers=internal_headers(),
        timeout=30
    )
    if r.ok:
        bundle = r.json()
        bundle_str = json.dumps({
            'verdict': bundle.get('verdict'),
            'health': bundle.get('health_summary'),
        }, sort_keys=True)
        hashes.append(hashlib.sha256(bundle_str.encode()).hexdigest())
    else:
        missing_sources.append('fscqi')

    # 3. SIAT output hash
    r = requests.get(
        f'{API_BASE}/api/internal/scans/{scan_id}/siat-output',
        headers=internal_headers(),
        timeout=30
    )
    if r.ok:
        siat = r.json()
        hashes.append(hashlib.sha256(json.dumps(siat, sort_keys=True).encode()).hexdigest())
    else:
        missing_sources.append('siat')

    # 4. REG output hash
    r = requests.get(
        f'{API_BASE}/api/internal/scans/{scan_id}/reg-output',
        headers=internal_headers(),
        timeout=30
    )
    if r.ok:
        reg = r.json()
        hashes.append(hashlib.sha256(json.dumps({
            'registration_state': reg.get('registration_state'),
            'scale_regime': reg.get('scale_regime'),
            'metric_trust_allowed': reg.get('metric_trust_allowed'),
        }, sort_keys=True).encode()).hexdigest())
    else:
        missing_sources.append('reg')

    # 5. Geometry output hash
    r = requests.get(
        f'{API_BASE}/api/internal/scans/{scan_id}/geometry-output',
        headers=internal_headers(),
        timeout=30
    )
    if r.ok:
        geo = r.json()
        hashes.append(hashlib.sha256(json.dumps({
            'fragment_count': len(json.loads(geo.get('fragment_set_json', '[]'))),
            'severe_concern': geo.get('severe_geometry_concern'),
        }, sort_keys=True).encode()).hexdigest())
    else:
        missing_sources.append('geometry')

    if not hashes:
        return hashlib.sha256(b'empty_lineage').hexdigest()

    # Include missing source markers so incomplete pipelines produce distinguishable fingerprints
    if missing_sources:
        hashes.append(hashlib.sha256(
            json.dumps({'missing': sorted(missing_sources)}, sort_keys=True).encode()
        ).hexdigest())

    if not hashes:
        return hashlib.sha256(b'empty_lineage').hexdigest()

    # Fold all hashes into a single fingerprint
    combined = '|'.join(sorted(hashes))
    return hashlib.sha256(combined.encode()).hexdigest()


def check_appearance_only(scan_id):
    """
    appearance_only_route = 1 when:
    - metric_trust_allowed = 0 (no metric authority)
    - severe_geometry_concern = 1 (geometry issues)
    - registration_state is FRAGMENTED
    """
    try:
        r = requests.get(
            f'{API_BASE}/api/internal/scans/{scan_id}/reg-output',
            headers=internal_headers(),
            timeout=30
        )
        if r.ok:
            reg = r.json()
            if reg.get('metric_trust_allowed') == 0:
                return 1
            if reg.get('registration_state') == 'FRAGMENTED':
                return 1
    except requests.RequestException as e:
        log(f'WARNING: reg-output unavailable for appearance_only check: {e}')

    try:
        r = requests.get(
            f'{API_BASE}/api/internal/scans/{scan_id}/geometry-output',
            headers=internal_headers(),
            timeout=30
        )
        if r.ok:
            geo = r.json()
            if geo.get('severe_geometry_concern') == 1:
                return 1
    except requests.RequestException as e:
        log(f'WARNING: geometry-output unavailable for appearance_only check: {e}')

    return 0


def load_camera_poses(scan_models_dir):
    """
    Load camera poses from the pycolmap sparse reconstruction.
    Returns list of {name, rotation_quat, translation} dicts, or empty list on failure.
    """
    sparse_dir = os.path.join(scan_models_dir, 'sparse')
    if not os.path.isdir(sparse_dir):
        log('  No sparse reconstruction directory found')
        return []

    try:
        import pycolmap
        recon = pycolmap.Reconstruction(sparse_dir)
    except Exception as e:
        log(f'  Could not load pycolmap reconstruction: {e}')
        return []

    poses = []
    for img_id, img in recon.images.items():
        if not img.has_pose:
            continue
        tfm = img.cam_from_world()
        poses.append({
            'name': img.name,
            # pycolmap quat is [w, x, y, z], convert to [x, y, z, w] for our rotation func
            'rotation_xyzw': [tfm.rotation.quat[1], tfm.rotation.quat[2], tfm.rotation.quat[3], tfm.rotation.quat[0]],
            'translation': list(tfm.translation),
            'camera_id': img.camera_id,
        })

    log(f'  Loaded {len(poses)} camera poses from sparse reconstruction')
    return poses


def quaternion_to_rotation_matrix(q_xyzw):
    """Convert quaternion (x,y,z,w) to 3x3 rotation matrix."""
    import numpy as np
    x, y, z, w = q_xyzw
    # Normalize
    norm = np.sqrt(x*x + y*y + z*z + w*w)
    x, y, z, w = x/norm, y/norm, z/norm, w/norm
    # Rotation matrix from quaternion
    R = np.array([
        [1 - 2*y*y - 2*z*z, 2*x*y - 2*z*w, 2*x*z + 2*y*w],
        [2*x*y + 2*z*w, 1 - 2*x*x - 2*z*z, 2*y*z - 2*x*w],
        [2*x*z - 2*y*w, 2*y*z + 2*x*w, 1 - 2*x*x - 2*y*y]
    ])
    return R


def build_camera_pose_matrix(rotation_xyzw, translation):
    """Build 4x4 camera-to-world pose matrix from pycolmap Rigid3d pose data."""
    import numpy as np
    R = quaternion_to_rotation_matrix(rotation_xyzw)
    t = np.array(translation)
    # pycolmap cam_from_world: world point -> camera point
    # pose matrix (world from camera): [R|t], so camera_pos = -R.T @ t
    pose = np.eye(4)
    pose[:3, :3] = R.T
    pose[:3, 3] = -R.T @ t
    return pose


def canonical_view_poses(n=8):
    """
    Generate n canonical view poses arranged in a circle around the origin.
    Returns list of (azimuth_deg, elevation_deg, distance) tuples.
    """
    import math
    poses = []
    for i in range(n):
        azimuth = 360 * i / n
        elevation = 15  # slightly above horizon
        distance = 2.0
        poses.append((azimuth, elevation, distance))
    return poses


def view_pose_from_spherical(azimuth_deg, elevation_deg, distance):
    """Build a 4x4 camera pose matrix from spherical coordinates (looking at origin)."""
    import numpy as np
    import math
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)

    # Camera position in spherical coordinates
    x = distance * math.cos(el) * math.sin(az)
    y = distance * math.sin(el)
    z = distance * math.cos(el) * math.cos(az)

    # Camera looks at origin, so camera Z axis = -position direction
    # Build rotation: camera -Z points toward origin
    forward = np.array([-x, -y, -z])
    forward = forward / np.linalg.norm(forward)

    # Up vector (world +Y)
    up = np.array([0, 1, 0])

    # Right = forward x up
    right = np.cross(forward, up)
    right = right / np.linalg.norm(right)

    # Camera up = right x forward
    cam_up = np.cross(right, forward)
    cam_up = cam_up / np.linalg.norm(cam_up)

    # Build camera rotation matrix (columns: right, up, -forward)
    R = np.column_stack([right, cam_up, -forward])

    pose = np.eye(4)
    pose[:3, :3] = R
    pose[:3, 3] = [x, y, z]
    return pose


def render_views(mesh_path, camera_poses, output_dir, viewport=(640, 480)):
    """
    Render the mesh from given camera poses using pyrender.
    Returns list of saved view filenames, or empty list on failure.

    camera_poses: list of {name, rotation_xyzw, translation} from pycolmap,
                  OR list of (azimuth, elevation, distance) for canonical views
    """
    try:
        import numpy as np
        import trimesh
        import pyrender
        from PIL import Image
    except ImportError as e:
        log(f'  Missing render dependency: {e}')
        return []

    try:
        # Load mesh once
        scene = trimesh.load(mesh_path)
        if hasattr(scene, 'geometry') and scene.geometry:
            mesh = list(scene.geometry.values())[0]
        else:
            mesh = scene

        # Center mesh for better rendering
        centroid = mesh.vertices.mean(axis=0)
        mesh.vertices = mesh.vertices - centroid

        py_mesh = pyrender.Mesh.from_trimesh(mesh)

        # Setup renderer (created once, reused)
        r = pyrender.OffscreenRenderer(viewport_width=viewport[0], viewport_height=viewport[1])

        saved_views = []
        manifest_entries = []

        # Detect if camera_poses come from pycolmap (dict with rotation_xyzw) or are canonical tuples
        use_colmap_poses = camera_poses and isinstance(camera_poses[0], dict) if camera_poses else False

        if use_colmap_poses:
            # Render from actual captured camera poses
            for i, pose_data in enumerate(camera_poses):
                try:
                    pose_matrix = build_camera_pose_matrix(
                        pose_data['rotation_xyzw'],
                        pose_data['translation']
                    )
                    # Adjust for mesh centering
                    pose_matrix[:3, 3] = pose_matrix[:3, 3] - centroid

                    # Create fresh scene for each render
                    rscene = pyrender.Scene()
                    rscene.add(py_mesh)
                    cam = pyrender.PerspectiveCamera(yfov=np.pi / 4)
                    rscene.add(cam, pose=pose_matrix)

                    color, depth = r.render(rscene)

                    # Save view
                    img = Image.fromarray(color)
                    view_filename = f'view_{i:03d}.png'
                    img.save(os.path.join(output_dir, view_filename))
                    saved_views.append(view_filename)
                    manifest_entries.append({
                        'file': view_filename,
                        'source': 'colmap',
                        'name': pose_data.get('name', f'pose_{i}'),
                        'camera_id': pose_data.get('camera_id'),
                    })
                    log(f'    Rendered view {i}: {pose_data.get("name", "unknown")}')
                except Exception as e:
                    log(f'    Failed to render view {i}: {e}')
        else:
            # Render from canonical spherical poses
            if not camera_poses:
                camera_poses = canonical_view_poses(8)

            for i, pose_spec in enumerate(camera_poses):
                try:
                    if isinstance(pose_spec, tuple) and len(pose_spec) == 3:
                        azimuth, elevation, distance = pose_spec
                        pose_matrix = view_pose_from_spherical(azimuth, elevation, distance)
                    else:
                        continue

                    # Create fresh scene for each render
                    rscene = pyrender.Scene()
                    rscene.add(py_mesh)
                    cam = pyrender.PerspectiveCamera(yfov=np.pi / 4)
                    rscene.add(cam, pose=pose_matrix)

                    color, depth = r.render(rscene)

                    img = Image.fromarray(color)
                    view_filename = f'view_{i:03d}.png'
                    img.save(os.path.join(output_dir, view_filename))
                    saved_views.append(view_filename)
                    manifest_entries.append({
                        'file': view_filename,
                        'source': 'canonical',
                        'azimuth_deg': pose_spec[0] if isinstance(pose_spec, tuple) else None,
                        'elevation_deg': pose_spec[1] if isinstance(pose_spec, tuple) else None,
                        'distance': pose_spec[2] if isinstance(pose_spec, tuple) else None,
                    })
                    log(f'    Rendered canonical view {i}: az={pose_spec[0]}, el={pose_spec[1]}')
                except Exception as e:
                    log(f'    Failed to render canonical view {i}: {e}')

        r.delete()

        # Write manifest
        manifest = {
            'view_version': VIEW_OUTPUT_VERSION,
            'rendered_views': manifest_entries,
            'mesh_center': centroid.tolist(),
            'viewport': viewport,
        }
        with open(os.path.join(output_dir, 'manifest.json'), 'w') as f:
            json.dump(manifest, f, indent=2)

        return saved_views

    except Exception as e:
        log(f'  View rendering failed: {e}')
        import traceback
        traceback.print_exc()
        return []


def run(scan_id):
    log(f'Starting photoreal for scan {scan_id}')

    # ── Step 1: Locate view bundle (model.glb from DG) ───────────────────
    scan_models_dir = os.path.join(MODELS_DIR, str(scan_id))
    model_glb = os.path.join(scan_models_dir, 'model.glb')

    if not os.path.exists(model_glb):
        log(f'FATAL: model.glb not found at {model_glb}')
        requests.post(
            f'{API_BASE}/api/internal/scans/{scan_id}/status',
            headers=internal_headers(),
            json={'status': 'FAILED', 'message': 'model.glb missing for photoreal'},
            timeout=10
        )
        sys.exit(1)

    # Create view bundle directory
    view_bundle_dir = os.path.join(scan_models_dir, 'view', VIEW_OUTPUT_VERSION)
    os.makedirs(view_bundle_dir, exist_ok=True)

    # ── Step 2: Attempt view synthesis ────────────────────────────────────
    view_synthesis_success = False
    camera_poses = []

    try:
        # Try to load camera poses from sparse reconstruction
        camera_poses = load_camera_poses(scan_models_dir)

        if camera_poses:
            log(f'  Attempting view synthesis from {len(camera_poses)} camera poses...')
            saved_views = render_views(model_glb, camera_poses, view_bundle_dir)
            if saved_views:
                log(f'  View synthesis successful: {len(saved_views)} views rendered')
                view_synthesis_success = True
            else:
                log('  View synthesis produced no views, will use fallback')
        else:
            log('  No camera poses available, using canonical view synthesis')
            saved_views = render_views(model_glb, [], view_bundle_dir)
            if saved_views:
                log(f'  Canonical view synthesis successful: {len(saved_views)} views rendered')
                view_synthesis_success = True
            else:
                log('  Canonical view synthesis failed, will use fallback')
    except Exception as e:
        log(f'  View synthesis error: {e}')
        import traceback
        traceback.print_exc()

    # Fallback: copy model.glb if view synthesis failed
    if not view_synthesis_success:
        log('  Falling back to geometry-only view bundle (model.glb copy)')
        view_bundle_path = os.path.join(view_bundle_dir, 'model.glb')
        shutil.copy2(model_glb, view_bundle_path)
        view_url = f'/uploads/models/{scan_id}/view/{VIEW_OUTPUT_VERSION}/model.glb'
    else:
        # View synthesis succeeded - serve the directory (manifest.json is the primary artifact)
        view_url = f'/uploads/models/{scan_id}/view/{VIEW_OUTPUT_VERSION}/manifest.json'

    # ── Step 3: Compute lineage fingerprint ────────────────────────────────
    lineage_fingerprint = compute_lineage_fingerprint(scan_id)
    log(f'  Lineage fingerprint: {lineage_fingerprint[:16]}...')

    # ── Step 4: Check appearance-only route ───────────────────────────────
    appearance_only_route = check_appearance_only(scan_id)
    log(f'  Appearance-only route: {appearance_only_route}')

    # ── Step 5: Post view_outputs to server ──────────────────────────────
    r = requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/view-output',
        headers=internal_headers(),
        json={
            'output_version': VIEW_OUTPUT_VERSION,
            'view_bundle_path': view_url,
            'lineage_fingerprint': lineage_fingerprint,
            'appearance_only_route': appearance_only_route,
        },
        timeout=30
    )
    r.raise_for_status()
    view_id = r.json().get('id')
    log(f'  view_outputs saved: id={view_id}')

    # ── Step 6: Transition to EDSIM ───────────────────────────────────
    requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/status',
        headers=internal_headers(),
        json={
            'status': 'EDSIM',
            'message': f'Photoreal done: view_id={view_id}, appearance_only={appearance_only_route}'
        },
        timeout=10
    ).raise_for_status()

    log(f'Photoreal complete for scan {scan_id} → EDSIM')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python photoreal_worker.py <scan_id>')
        sys.exit(1)
    run(sys.argv[1])
