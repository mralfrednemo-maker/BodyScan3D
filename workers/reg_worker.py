"""
reg_worker.py — Phase 4 worker: Multi-view Registration with Honest Scale Posture.

Runs after REG state is set (after SIAT, before DG/mesh generation).
Accepts scan_id as a command-line argument.

Produces (RG-1):
  - registration_state    CONNECTED | PARTIAL | FRAGMENTED
  - reg_graph_json        which frames share 3D point observations
  - pose_version         e.g. "1.0.0"
  - scale_regime         RELATIVE | METRIC | UNKNOWN
  - scale_confidence_band_json  {mean, std, unit}
  - metric_trust_allowed INTEGER DEFAULT 0  (FALSE unless validated)
  - measurement_validity_claim  VALID | INVALID | INDETERMINATE
  - measurement_use_prohibited INTEGER DEFAULT 0
  - feature_support_regime  core_only | core_plus_context | fallback_wide

RG-2: No scale is assumed metric by default. Scale is computed from the
reconstruction but NOT trusted as metric unless a calibration target was used.
metric_trust_allowed = 0 by default.

Usage:
    python reg_worker.py <scan_id>
"""
import sys
import os
import json
import requests

sys.path.insert(0, os.path.dirname(__file__))
from config import (
    API_BASE, MODELS_DIR, internal_headers
)


REG_OUTPUT_VERSION = '1.0.0'


def log(msg):
    print(f'[reg] {msg}', flush=True)


def compute_reg_graph(pycolmap_recon, frames):
    """
    Build registration graph: which frames share 3D point observations.
    Returns dict: {frame_id: [point3D_ids], ...}
    Frames that see the same 3D point are "connected" in the graph.
    """
    import pycolmap

    try:
        recon = pycolmap.Reconstruction(pycolmap_recon)
    except Exception as e:
        log(f'Could not load pycolmap reconstruction: {e}')
        return {'error': str(e)}

    # Build frame → point mapping
    frame_points = {}  # frame_id -> set of point3D_ids
    for point_id, point in recon.points3D.items():
        for track_el in point.track.elements:
            frame_id = track_el.image_id
            if frame_id not in frame_points:
                frame_points[frame_id] = set()
            frame_points[frame_id].add(point_id)

    # Find connected components via shared points
    # For each frame, list other frames it shares points with
    connections = {}
    frame_ids_in_recon = set(frame_points.keys())
    for fid in frame_ids_in_recon:
        shared = set()
        for other_fid, points in frame_points.items():
            if other_fid != fid and frame_points[fid] & points:
                shared.add(other_fid)
        connections[fid] = list(shared)

    # Determine registration state
    if not connections:
        reg_state = 'FRAGMENTED'
    else:
        # Check if graph is fully connected
        visited = set()
        frontier = [next(iter(connections.keys()))]
        while frontier:
            node = frontier.pop()
            if node in visited:
                continue
            visited.add(node)
            frontier.extend(connections.get(node, []))

        connected_fraction = len(visited) / len(frame_ids_in_recon) if frame_ids_in_recon else 0
        if connected_fraction >= 0.9:
            reg_state = 'CONNECTED'
        elif connected_fraction >= 0.5:
            reg_state = 'PARTIAL'
        else:
            reg_state = 'FRAGMENTED'

    # Map pycolmap image_ids to our frame_ids using frame_name matching
    # pycolmap uses the image filename as name
    frame_id_map = {}
    for img_id, img in recon.images.items():
        img_name = img.name  # e.g. "frame_0001.jpg"
        for frame in frames:
            if frame['frameUrl'].endswith(img_name) or img_name in frame['frameUrl']:
                frame_id_map[img_id] = frame['id']
                break

    graph_json = {}
    for img_id, connected_ids in connections.items():
        our_fid = frame_id_map.get(img_id)
        if our_fid is None:
            continue
        graph_json[str(our_fid)] = [frame_id_map.get(cid) for cid in connected_ids if cid in frame_id_map]

    return {
        'registration_state': reg_state,
        'reg_graph': graph_json,
        'connected_frames': len(visited),
        'total_frames': len(frame_ids_in_recon),
        'connected_fraction': round(connected_fraction, 3),
        'total_points': len(recon.points3D),
    }


def estimate_scale_regime(recon_dir, frames):
    """
    Determine scale regime from reconstruction characteristics.
    Returns: RELATIVE | METRIC | UNKNOWN
    """
    import pycolmap
    import numpy as np

    try:
        recon = pycolmap.Reconstruction(recon_dir)
    except Exception:
        return 'UNKNOWN'

    if not recon.points3D:
        return 'UNKNOWN'

    # Compute baseline statistics: average 3D point spread
    pts = np.array([[pt.xyz[0], pt.xyz[1], pt.xyz[2]] for pt in recon.points3D.values()])
    if len(pts) < 10:
        return 'UNKNOWN'

    # Metric check: if cameras have known intrinsics (prior calibration),
    # we can compute approximate scale. pycolmap's reconstruction is up-to-scale.
    # Without a calibration target, scale is RELATIVE.
    # METRIC only if we detect a known scale reference (not implemented here).
    # Default: RELATIVE (scale is not metric)
    scale_regime = 'RELATIVE'

    # Estimate scale confidence: coefficient of variation of point depths
    depths = []
    for img_id, img in recon.images.items():
        cam = recon.cameras[img.camera_id]
        for point_id in img.point3D_ids:
            if point_id == -1:
                continue
            pt = recon.points3D.get(point_id)
            if pt is None:
                continue
            # Approximate depth
            xyz = pt.xyz
            # Simplified: use point spread as proxy
            depths.append(np.linalg.norm(xyz))

    if depths:
        mean_depth = np.mean(depths)
        std_depth = np.std(depths) if len(depths) > 1 else mean_depth
        cv = std_depth / mean_depth if mean_depth > 0 else 1.0
        scale_confidence = {
            'mean_depth_px': round(mean_depth, 2) if mean_depth else 0,
            'std_depth_px': round(std_depth, 2) if std_depth else 0,
            'coefficient_of_variation': round(cv, 3),
            'unit': 'pixel_equivalent',  # NOT real-world units
        }
    else:
        scale_confidence = {'error': 'no depth data'}

    return scale_regime, scale_confidence


def classify_feature_support(recon_dir, frames):
    """
    Classify feature support quality:
    - core_only: sparse points, mostly on primary subject
    - core_plus_context: good coverage including background context
    - fallback_wide: too sparse, unreliable geometry
    """
    import pycolmap
    import numpy as np

    try:
        recon = pycolmap.Reconstruction(recon_dir)
    except Exception:
        return 'fallback_wide'

    n_points = len(recon.points3D)
    n_images = len(recon.images)

    if n_images == 0:
        return 'fallback_wide'

    pts_per_image = n_points / n_images if n_images > 0 else 0

    # Heuristic thresholds (tune empirically)
    if pts_per_image >= 500:
        return 'core_plus_context'
    elif pts_per_image >= 100:
        return 'core_only'
    else:
        return 'fallback_wide'


def run(scan_id):
    log(f'Starting REG for scan {scan_id}')

    # ── Step 1: Fetch frames ───────────────────────────────────────────────
    r = requests.get(
        f'{API_BASE}/api/internal/scans/{scan_id}/frames',
        headers=internal_headers(),
        timeout=30
    )
    r.raise_for_status()
    frames = r.json()

    # ── Step 2: Locate pycolmap reconstruction directory ────────────────────
    # reconstruct_worker creates: uploads/models/<scan_id>/raw_pointcloud.glb
    # The pycolmap output dir is stored in a JSON sidecar
    scan_models_dir = os.path.join(MODELS_DIR, str(scan_id))
    meta_path = os.path.join(scan_models_dir, 'recon_meta.json')
    glb_path = os.path.join(scan_models_dir, 'raw_pointcloud.glb')

    recon_dir = None
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
            recon_dir = meta.get('recon_dir')

    if not recon_dir or not os.path.exists(recon_dir):
        # Try to find the sparse directory from reconstruct workdir
        # reconstruct_worker saves to MODELS_DIR/<scan_id>/ but doesn't keep sparse dir
        # We need to reconstruct from GLB or skip
        log('WARNING: pycolmap reconstruction directory not found — using GLB point cloud')
        # Fallback: compute approximate reg metrics from GLB
        recon_dir = None

    # ── Step 3: Compute registration metrics ───────────────────────────────
    if recon_dir and os.path.exists(recon_dir):
        reg_graph_result = compute_reg_graph(recon_dir, frames)
        scale_result = estimate_scale_regime(recon_dir, frames)
        feature_support = classify_feature_support(recon_dir, frames)
    else:
        # Fallback: degraded metrics from frames alone
        reg_graph_result = {
            'registration_state': 'UNKNOWN',
            'reg_graph': {},
            'connected_frames': 0,
            'total_frames': len(frames),
            'connected_fraction': 0.0,
            'total_points': 0,
        }
        scale_result = 'UNKNOWN'
        feature_support = 'fallback_wide'

    reg_state = reg_graph_result.get('registration_state', 'FRAGMENTED')
    if isinstance(scale_result, tuple):
        scale_regime = scale_result[0]
        scale_confidence = scale_result[1]
    else:
        scale_regime = scale_result
        scale_confidence = {}

    # ── Step 4: Determine measurement validity ───────────────────────────────
    # RG-4: metric_trust_allowed defaults to FALSE
    # Only set to TRUE if scale_regime == METRIC (requires calibration)
    metric_trust_allowed = 1 if scale_regime == 'METRIC' else 0

    # RG-5: measurement validity claim
    if feature_support == 'fallback_wide':
        measurement_validity = 'INVALID'
        measurement_use_prohibited = 1
    elif metric_trust_allowed == 0:
        measurement_validity = 'INDETERMINATE'  # scale not metric
        measurement_use_prohibited = 0
    else:
        measurement_validity = 'VALID'
        measurement_use_prohibited = 0

    log(f'  Registration state: {reg_state}')
    log(f'  Scale regime: {scale_regime}')
    log(f'  Feature support: {feature_support}')
    log(f'  Metric trust: {metric_trust_allowed} (measurement_validity={measurement_validity})')

    # ── Step 5: Post reg_outputs to server ────────────────────────────────
    r2 = requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/reg-output',
        headers=internal_headers(),
        json={
            'output_version': REG_OUTPUT_VERSION,
            'registration_state': reg_state,
            'reg_graph_json': reg_graph_result,
            'pose_version': REG_OUTPUT_VERSION,
            'scale_regime': scale_regime,
            'scale_confidence_band_json': scale_confidence,
            'metric_trust_allowed': metric_trust_allowed,
            'measurement_validity_claim': measurement_validity,
            'measurement_use_prohibited': measurement_use_prohibited,
            'feature_support_regime': feature_support,
        },
        timeout=30
    )
    r2.raise_for_status()
    reg_id = r2.json().get('id')
    log(f'  REG record saved: id={reg_id}')

    # ── Step 6: Transition to POST_PROCESSING (DG) ───────────────────────
    requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/status',
        headers=internal_headers(),
        json={
            'status': 'POST_PROCESSING',
            'message': f'REG done: {reg_state}, {feature_support}, scale={scale_regime}'
        },
        timeout=10
    ).raise_for_status()

    log(f'REG complete for scan {scan_id} → POST_PROCESSING')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python reg_worker.py <scan_id>')
        sys.exit(1)
    run(sys.argv[1])
