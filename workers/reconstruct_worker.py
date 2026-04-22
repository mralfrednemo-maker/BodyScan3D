"""
reconstruct_worker.py — Phase 3 worker: sparse SfM + optional dense MVS.

Pipeline:
  1. pycolmap sparse reconstruction (CPU) — feature extraction, matching, mapping
  2. pycolmap image undistortion → MVS workspace
  3. pycolmap patch-match stereo (CUDA required) → per-image depth maps
  4. pycolmap stereo fusion → dense point cloud
  5. Export densest cloud available (dense → fallback to sparse) as raw_pointcloud.glb

If CUDA is not present, steps 2-4 fail gracefully and we fall back to the sparse cloud.

Usage:
    python reconstruct_worker.py <scan_id>
"""
import sys
import os
import shutil
import tempfile
import traceback
import json
import requests

sys.path.insert(0, os.path.dirname(__file__))
from config import (
    API_BASE, FRAMES_DIR, MASKS_DIR, MODELS_DIR, internal_headers
)


def log(msg):
    print(f'[reconstruct] {msg}', flush=True)


def build_masked_frame(frame_path, mask_path, out_path):
    """Apply mask to a frame image (black-out background)."""
    try:
        import numpy as np
        from PIL import Image
        img  = np.array(Image.open(frame_path).convert('RGB'))
        mask = np.array(Image.open(mask_path).convert('L'))
        img[mask == 0] = 0
        Image.fromarray(img).save(out_path)
        return True
    except Exception as e:
        log(f'  mask apply failed for {frame_path}: {e}')
        shutil.copy2(frame_path, out_path)
        return False


def run_pycolmap(image_dir, output_dir):
    """Sparse SfM. Returns path to the largest reconstruction directory, or None."""
    import pycolmap

    db_path = os.path.join(output_dir, 'database.db')
    sparse_dir = os.path.join(output_dir, 'sparse')
    os.makedirs(sparse_dir, exist_ok=True)

    log('  Extracting features...')
    pycolmap.extract_features(
        database_path=db_path,
        image_path=image_dir,
        camera_mode=pycolmap.CameraMode.SINGLE,
    )

    log('  Matching features...')
    pycolmap.match_exhaustive(database_path=db_path)

    log('  Incremental mapping...')
    maps = pycolmap.incremental_mapping(
        database_path=db_path,
        image_path=image_dir,
        output_path=sparse_dir
    )
    if not maps:
        log('  WARNING: incremental mapping returned empty reconstruction')
        return None

    recon_dir = os.path.join(sparse_dir, '0')
    if not os.path.isdir(recon_dir):
        subdirs = sorted(os.listdir(sparse_dir))
        if subdirs:
            recon_dir = os.path.join(sparse_dir, subdirs[0])
        else:
            return None

    log(f'  Sparse reconstruction saved: {recon_dir}')
    return recon_dir


def run_dense_mvs(sparse_recon_dir, image_dir, workdir):
    """
    Dense MVS via Docker-hosted CUDA-built COLMAP (colmap/colmap:latest).

    pip-installed pycolmap is CPU-only; Windows driver + WSL nvidia runtime +
    colmap/colmap container give us a GPU patch-match + fusion path. On any
    failure we return None and the caller falls back to the sparse cloud.
    """
    import subprocess

    mvs_dir = os.path.join(workdir, 'mvs')
    os.makedirs(mvs_dir, exist_ok=True)

    fused_ply = os.path.join(mvs_dir, 'fused.ply')

    # Container paths: bind-mount sparse recon parent + image dir + workdir.
    # sparse_recon_dir is e.g. /tmp/bs3d_42_.../sparse/0 so we mount workdir
    # at /work and point to /work/sparse/<subdir>.
    try:
        rel_sparse = os.path.relpath(sparse_recon_dir, workdir)
    except ValueError:
        log('  [dense] sparse_recon_dir not under workdir; cannot bind-mount')
        return None

    cmd = [
        'docker', 'run', '--rm', '--gpus', 'all',
        '-v', f'{workdir}:/work',
        '-v', f'{image_dir}:/images:ro',
        '--user', f'{os.getuid()}:{os.getgid()}',
        'colmap/colmap:latest',
        'bash', '-c',
        (
            f'set -e && '
            f'colmap image_undistorter --image_path /images '
            f'  --input_path /work/{rel_sparse} --output_path /work/mvs '
            f'  --output_type COLMAP && '
            f'colmap patch_match_stereo --workspace_path /work/mvs '
            f'  --PatchMatchStereo.geom_consistency true && '
            f'colmap stereo_fusion --workspace_path /work/mvs '
            f'  --output_path /work/mvs/fused.ply'
        ),
    ]

    log('  [dense] Running CUDA COLMAP via Docker (patch-match + fusion)...')
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except FileNotFoundError:
        log('  [dense] docker not available; falling back to sparse.')
        return None
    except subprocess.TimeoutExpired:
        log('  [dense] Docker COLMAP timed out after 60 min; falling back.')
        return None

    if result.returncode != 0:
        log(f'  [dense] Docker COLMAP exit {result.returncode}; stderr tail:')
        for line in (result.stderr or '').splitlines()[-10:]:
            log(f'    {line}')
        return None

    if not os.path.exists(fused_ply):
        log('  [dense] Docker COLMAP succeeded but fused.ply missing.')
        return None

    size_mb = os.path.getsize(fused_ply) / (1024 * 1024)
    log(f'  [dense] Dense fused cloud written: {fused_ply} ({size_mb:.1f} MB)')
    return fused_ply


def export_dense_glb(fused_ply, out_glb):
    """Convert fused.ply dense cloud to GLB via trimesh."""
    import trimesh
    cloud = trimesh.load(fused_ply)
    # trimesh may load it as PointCloud or Trimesh; both have export()
    n_pts = len(cloud.vertices) if hasattr(cloud, 'vertices') else 0
    cloud.export(out_glb)
    log(f'  GLB exported (dense): {out_glb} ({n_pts} points)')
    return n_pts > 0


def export_sparse_glb(recon_dir, out_glb):
    """Export sparse pycolmap reconstruction point cloud to GLB."""
    import pycolmap
    import numpy as np
    import trimesh

    recon = pycolmap.Reconstruction(recon_dir)
    pts = np.array([[pt.xyz[0], pt.xyz[1], pt.xyz[2]]
                    for pt in recon.points3D.values()])
    colors = np.array([[pt.color[0], pt.color[1], pt.color[2], 255]
                       for pt in recon.points3D.values()], dtype=np.uint8)
    if len(pts) == 0:
        log('  WARNING: no 3D points in reconstruction')
        return False

    cloud = trimesh.PointCloud(pts, colors=colors)
    cloud.export(out_glb)
    log(f'  GLB exported (sparse): {out_glb} ({len(pts)} points)')
    return True


def run(scan_id):
    log(f'Starting reconstruction for scan {scan_id}')

    r = requests.get(f'{API_BASE}/api/internal/scans/{scan_id}/frames',
                     headers=internal_headers(), timeout=30)
    r.raise_for_status()
    frames = r.json()
    log(f'{len(frames)} frames')

    scan_mask_dir = os.path.join(MASKS_DIR, str(scan_id))
    workdir = tempfile.mkdtemp(prefix=f'bs3d_{scan_id}_')

    # Phase A: sparse alignment on full (unmasked) frames for best feature matching
    full_image_dir = os.path.join(workdir, 'full_images')
    os.makedirs(full_image_dir)
    for frame in frames:
        filename = os.path.basename(frame['frameUrl'])
        frame_path = os.path.join(FRAMES_DIR, filename)
        if os.path.exists(frame_path):
            shutil.copy2(frame_path, os.path.join(full_image_dir, filename))

    log(f'Phase A: Sparse alignment on {len(frames)} full frames...')
    recon_dir = run_pycolmap(full_image_dir, workdir)
    image_dir_for_mvs = full_image_dir

    if not recon_dir:
        log('Phase A failed on full frames, trying masked frames...')
        workdir_masked = tempfile.mkdtemp(prefix=f'bs3d_{scan_id}_masked_')
        masked_img_dir = os.path.join(workdir_masked, 'images')
        os.makedirs(masked_img_dir)
        applied = 0
        for frame in frames:
            filename = os.path.basename(frame['frameUrl'])
            frame_path = os.path.join(FRAMES_DIR, filename)
            mask_path  = os.path.join(scan_mask_dir, f'mask_{frame["id"]:06d}.png')
            out_path   = os.path.join(masked_img_dir, filename)
            if os.path.exists(mask_path):
                build_masked_frame(frame_path, mask_path, out_path)
                applied += 1
            elif os.path.exists(frame_path):
                shutil.copy2(frame_path, out_path)
        log(f'Masks applied: {applied}/{len(frames)}')
        recon_dir = run_pycolmap(masked_img_dir, workdir_masked)
        shutil.rmtree(workdir, ignore_errors=True)
        workdir = workdir_masked
        image_dir_for_mvs = masked_img_dir

    if not recon_dir:
        requests.post(
            f'{API_BASE}/api/internal/scans/{scan_id}/status',
            headers=internal_headers(),
            json={'status': 'FAILED', 'failureClass': 'RECONSTRUCTION_FAIL',
                  'message': 'pycolmap incremental mapping produced no reconstruction'},
            timeout=10
        )
        shutil.rmtree(workdir, ignore_errors=True)
        sys.exit(1)

    # Phase B: dense MVS (CUDA required). Silently falls back to sparse on failure.
    scan_models_dir = os.path.join(MODELS_DIR, str(scan_id))
    os.makedirs(scan_models_dir, exist_ok=True)
    glb_path = os.path.join(scan_models_dir, 'raw_pointcloud.glb')

    fused_ply = None
    try:
        fused_ply = run_dense_mvs(recon_dir, image_dir_for_mvs, workdir)
    except Exception:
        log('  [dense] Unexpected error during MVS:')
        log(traceback.format_exc())

    exported = False
    if fused_ply:
        try:
            exported = export_dense_glb(fused_ply, glb_path)
        except Exception as e:
            log(f'  [dense] GLB export failed: {e}')

    if not exported:
        log('Using sparse cloud as raw model.')
        exported = export_sparse_glb(recon_dir, glb_path)

    if not exported:
        requests.post(
            f'{API_BASE}/api/internal/scans/{scan_id}/status',
            headers=internal_headers(),
            json={'status': 'FAILED', 'failureClass': 'RECONSTRUCTION_FAIL',
                  'message': 'GLB export failed: empty point cloud'},
            timeout=10
        )
        shutil.rmtree(workdir, ignore_errors=True)
        sys.exit(1)

    # Persist sparse reconstruction directory for REG worker
    # REG needs pycolmap Reconstruction() object to compute connected_fraction
    sparse_persistent_dir = os.path.join(MODELS_DIR, str(scan_id), 'sparse')
    if os.path.exists(recon_dir):
        if os.path.exists(sparse_persistent_dir):
            shutil.rmtree(sparse_persistent_dir)
        shutil.copytree(recon_dir, sparse_persistent_dir)
        sidecar_path = os.path.join(MODELS_DIR, str(scan_id), 'recon_meta.json')
        with open(sidecar_path, 'w') as f:
            json.dump({'recon_dir': sparse_persistent_dir}, f)
        log(f'  Sparse dir persisted: {sparse_persistent_dir}')

    glb_url = f'/uploads/models/{scan_id}/raw_pointcloud.glb'
    requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/model',
        headers=internal_headers(),
        json={'modelUrl': glb_url, 'stage': 'raw'},
        timeout=10
    ).raise_for_status()

    requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/status',
        headers=internal_headers(),
        json={'status': 'REG',
              'message': 'reconstruction complete (dense)' if fused_ply else 'reconstruction complete (sparse)'},
        timeout=10
    ).raise_for_status()

    shutil.rmtree(workdir, ignore_errors=True)
    log('Reconstruction complete')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python reconstruct_worker.py <scan_id>')
        sys.exit(1)
    run(sys.argv[1])
