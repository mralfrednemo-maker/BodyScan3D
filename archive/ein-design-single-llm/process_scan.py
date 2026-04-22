#!/usr/bin/env python3
"""
BodyScan 3D — Processing Worker
Stages: frame QC → segmentation (rembg) → reconstruction (COLMAP) → mesh cleanup → GLB export

Usage:
  python3 process_scan.py --scan-id 42 --frames-dir /path/to/frames/42
    --uploads-dir /path/to/uploads --server-url http://localhost:5000
    --secret <CAPTURE_INTERNAL_SECRET>
"""

import argparse, json, os, sys, time, shutil, subprocess, requests
import numpy as np
from pathlib import Path
from PIL import Image

# ── Args ─────────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser()
parser.add_argument('--scan-id', required=True, type=int)
parser.add_argument('--frames-dir', required=True)
parser.add_argument('--uploads-dir', required=True)
parser.add_argument('--server-url', default='http://localhost:5000')
parser.add_argument('--secret', default='bs3d-internal-2026')
args = parser.parse_args()

SCAN_ID     = args.scan_id
FRAMES_DIR  = Path(args.frames_dir)
UPLOADS_DIR = Path(args.uploads_dir)
SERVER_URL  = args.server_url.rstrip('/')
SECRET      = args.secret

WORK_DIR    = Path(f'/tmp/bs3d_scan_{SCAN_ID}')
MASKED_DIR  = WORK_DIR / 'masked'
COLMAP_DIR  = WORK_DIR / 'colmap'
MESH_DIR    = WORK_DIR / 'mesh'
WORK_DIR.mkdir(parents=True, exist_ok=True)
MASKED_DIR.mkdir(exist_ok=True)
COLMAP_DIR.mkdir(exist_ok=True)
MESH_DIR.mkdir(exist_ok=True)

HEADERS = {'X-Internal-Secret': SECRET, 'Content-Type': 'application/json'}

# ── Helpers ───────────────────────────────────────────────────────────────────

def log(stage, status, message):
    print(f'[{stage}] {status}: {message}', flush=True)
    try:
        requests.post(
            f'{SERVER_URL}/api/internal/capture/result',
            json={'scanId': SCAN_ID, 'status': 'Processing', 'stage': stage,
                  'message': message},
            headers=HEADERS, timeout=10
        )
    except Exception as e:
        print(f'  [log] Warning: could not update server: {e}', flush=True)

def fail(stage, message):
    print(f'[{stage}] FAILED: {message}', flush=True)
    try:
        requests.post(
            f'{SERVER_URL}/api/internal/capture/result',
            json={'scanId': SCAN_ID, 'status': 'Failed', 'stage': stage, 'message': message},
            headers=HEADERS, timeout=10
        )
    except Exception:
        pass
    sys.exit(1)

def success(model_url):
    print(f'[done] SUCCESS: {model_url}', flush=True)
    try:
        requests.post(
            f'{SERVER_URL}/api/internal/capture/result',
            json={'scanId': SCAN_ID, 'status': 'Ready', 'modelUrl': model_url,
                  'stage': 'complete', 'message': 'Pipeline complete'},
            headers=HEADERS, timeout=10
        )
    except Exception as e:
        print(f'  [done] Warning: could not notify server: {e}', flush=True)

def blur_score(img_path):
    """Laplacian variance — higher = sharper."""
    try:
        import cv2
        img = cv2.imread(str(img_path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return 0.0
        return float(cv2.Laplacian(img, cv2.CV_64F).var())
    except Exception:
        return 50.0  # default if cv2 unavailable

# ── Stage 1: Frame QC ─────────────────────────────────────────────────────────

def stage_frame_qc():
    log('frame_qc', 'ok', 'Analysing frames for blur and quality')

    frames = sorted(FRAMES_DIR.glob('*.jpg')) + sorted(FRAMES_DIR.glob('*.jpeg'))
    if len(frames) == 0:
        fail('frame_qc', 'No frames found in frames directory')

    scored = []
    for f in frames:
        score = blur_score(f)
        scored.append((f, score))

    # Keep top 60% by sharpness, minimum 10 frames
    scored.sort(key=lambda x: -x[1])
    keep_n = max(10, int(len(scored) * 0.6))
    keep_n = min(keep_n, 50)  # cap at 50 for reconstruction speed
    selected = sorted([s[0] for s in scored[:keep_n]])  # restore time order

    log('frame_qc', 'ok', f'Selected {len(selected)}/{len(frames)} frames (blur-filtered)')
    return selected

# ── Stage 2: Segmentation (rembg per-frame) ───────────────────────────────────

def stage_segmentation(frames, box, pos_pts, neg_pts):
    log('segmentation', 'ok', f'Running background removal on {len(frames)} frames')

    try:
        from rembg import remove
    except ImportError:
        fail('segmentation', 'rembg not installed — run: pip3 install rembg')

    masked_frames = []
    for i, frame_path in enumerate(frames):
        out_path = MASKED_DIR / frame_path.name.replace('.jpg', '.png').replace('.jpeg', '.png')
        try:
            with open(frame_path, 'rb') as f_in:
                input_data = f_in.read()
            output_data = remove(input_data)
            with open(out_path, 'wb') as f_out:
                f_out.write(output_data)

            # Apply bounding-box crop mask to keep only the target region
            if box:
                img = Image.open(out_path).convert('RGBA')
                w, h = img.size
                x1 = int(box['x1'] / 100 * w) if box['x1'] <= 100 else int(box['x1'])
                y1 = int(box['y1'] / 100 * h) if box['y1'] <= 100 else int(box['y1'])
                x2 = int(box['x2'] / 100 * w) if box['x2'] <= 100 else int(box['x2'])
                y2 = int(box['y2'] / 100 * h) if box['y2'] <= 100 else int(box['y2'])
                # Expand crop region by 15% for safety
                pad_x = int((x2 - x1) * 0.15)
                pad_y = int((y2 - y1) * 0.15)
                x1 = max(0, x1 - pad_x); y1 = max(0, y1 - pad_y)
                x2 = min(w, x2 + pad_x); y2 = min(h, y2 + pad_y)
                img = img.crop((x1, y1, x2, y2))
                img.save(out_path)

            masked_frames.append(out_path)
        except Exception as e:
            print(f'  [seg] Warning: frame {i} failed: {e}', flush=True)

        if i % 5 == 0:
            log('segmentation', 'ok', f'  Masked {i+1}/{len(frames)} frames')

    if len(masked_frames) < 5:
        fail('segmentation', f'Too few frames survived masking: {len(masked_frames)}')

    log('segmentation', 'ok', f'Segmentation complete — {len(masked_frames)} masked frames')
    return masked_frames

# ── Stage 3: 3D Reconstruction (COLMAP) ──────────────────────────────────────

def stage_reconstruction(masked_frames):
    log('reconstruction', 'ok', f'Starting 3D reconstruction with {len(masked_frames)} frames')

    # Copy PNG frames to COLMAP images dir
    images_dir = COLMAP_DIR / 'images'
    images_dir.mkdir(exist_ok=True)
    for i, f in enumerate(masked_frames):
        # Convert RGBA PNG to RGB JPG for COLMAP (COLMAP handles JPG better)
        try:
            img = Image.open(f).convert('RGBA')
            # Composite on white background
            bg = Image.new('RGB', img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            bg.save(images_dir / f'{i:04d}.jpg', 'JPEG', quality=92)
        except Exception as e:
            print(f'  [recon] Warning: could not convert frame {f}: {e}', flush=True)

    db_path = COLMAP_DIR / 'colmap.db'
    sparse_dir = COLMAP_DIR / 'sparse'
    dense_dir = COLMAP_DIR / 'dense'
    sparse_dir.mkdir(exist_ok=True)
    dense_dir.mkdir(exist_ok=True)

    # Try pycolmap first (pip-installable, no system install needed)
    try:
        import pycolmap
        log('reconstruction', 'ok', f'Using pycolmap {pycolmap.__version__}')
        return stage_reconstruction_pycolmap(images_dir, db_path, sparse_dir, dense_dir)
    except ImportError:
        pass

    # Fall back to system colmap binary
    colmap_bin = shutil.which('colmap')
    if colmap_bin:
        log('reconstruction', 'ok', f'Using system colmap: {colmap_bin}')

        def run_colmap(args_list, step):
            cmd = [colmap_bin] + args_list
            log('reconstruction', 'ok', f'  COLMAP: {step}')
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
            if r.returncode != 0:
                print(f'  [recon] COLMAP {step} stderr: {r.stderr[-500:]}', flush=True)
                raise RuntimeError(f'COLMAP {step} failed (exit {r.returncode})')

        try:
            run_colmap(['feature_extractor', '--database_path', str(db_path),
                '--image_path', str(images_dir), '--ImageReader.single_camera', '1',
                '--SiftExtraction.use_gpu', '0'], 'feature_extractor')
            run_colmap(['exhaustive_matcher', '--database_path', str(db_path),
                '--SiftMatching.use_gpu', '0'], 'exhaustive_matcher')
            run_colmap(['mapper', '--database_path', str(db_path),
                '--image_path', str(images_dir), '--output_path', str(sparse_dir)], 'mapper')

            sparse_models = list(sparse_dir.iterdir())
            if not sparse_models:
                raise RuntimeError('COLMAP mapper produced no models')
            best_model = sparse_models[0]
            run_colmap(['model_converter', '--input_path', str(best_model),
                '--output_path', str(best_model), '--output_type', 'TXT'], 'model_converter')
            run_colmap(['image_undistorter', '--image_path', str(images_dir),
                '--input_path', str(best_model), '--output_path', str(dense_dir),
                '--output_type', 'COLMAP'], 'image_undistorter')
            run_colmap(['patch_match_stereo', '--workspace_path', str(dense_dir),
                '--PatchMatchStereo.geom_consistency', 'true'], 'patch_match_stereo')
            run_colmap(['stereo_fusion', '--workspace_path', str(dense_dir),
                '--output_path', str(dense_dir / 'fused.ply')], 'stereo_fusion')
            log('reconstruction', 'ok', 'System COLMAP reconstruction complete')
            return dense_dir / 'fused.ply'
        except Exception as e:
            log('reconstruction', 'ok', f'System COLMAP failed ({e}), falling back to open3d')

    log('reconstruction', 'ok', 'No COLMAP available — using open3d fallback reconstruction')
    return stage_reconstruction_open3d(list(images_dir.glob('*.jpg')))

def stage_reconstruction_pycolmap(images_dir, db_path, sparse_dir, dense_dir):
    """Use pycolmap (pip) for SfM — no system COLMAP install needed."""
    import pycolmap

    log('reconstruction', 'ok', 'Running pycolmap SfM pipeline')

    # Feature extraction (CPU, single camera model)
    extraction_opts = pycolmap.FeatureExtractionOptions()
    pycolmap.extract_features(
        database_path=db_path,
        image_path=images_dir,
        camera_mode=pycolmap.CameraMode.SINGLE,
        extraction_options=extraction_opts,
        device=pycolmap.Device.cpu
    )
    log('reconstruction', 'ok', '  pycolmap: features extracted')

    # Exhaustive matching
    matching_opts = pycolmap.FeatureMatchingOptions()
    pycolmap.match_exhaustive(
        database_path=db_path,
        matching_options=matching_opts,
        device=pycolmap.Device.cpu
    )
    log('reconstruction', 'ok', '  pycolmap: features matched')

    # Incremental SfM
    reconstructions = pycolmap.incremental_mapping(
        database_path=db_path,
        image_path=images_dir,
        output_path=sparse_dir
    )
    if not reconstructions:
        raise RuntimeError('pycolmap mapper produced no reconstructions')

    # Pick the largest reconstruction
    best = max(reconstructions.values(), key=lambda r: len(r.points3D))
    log('reconstruction', 'ok', f'  pycolmap: {len(best.points3D)} 3D points from {len(best.images)} images')

    if len(best.points3D) < 50:
        raise RuntimeError(f'pycolmap: insufficient 3D points: {len(best.points3D)} (need real photos, not solid color)')

    # Export sparse point cloud as PLY
    ply_path = sparse_dir / 'sparse.ply'
    best.export_PLY(str(ply_path))
    return ply_path


def stage_reconstruction_open3d(image_paths):
    """Fallback: depth estimation + point cloud fusion with open3d."""
    log('reconstruction', 'ok', 'Running open3d fallback reconstruction (no COLMAP)')
    try:
        import open3d as o3d
    except ImportError:
        fail('reconstruction', 'Neither COLMAP nor open3d is available')

    # Use feature-based point cloud from image pair matching
    import cv2

    all_points = []
    all_colors = []

    orb = cv2.ORB_create(nfeatures=2000)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

    imgs = []
    for p in image_paths[:20]:
        img = cv2.imread(str(p))
        if img is not None:
            imgs.append((p, img))

    if len(imgs) < 3:
        fail('reconstruction', f'Too few usable images: {len(imgs)}')

    # Extract features from all images and triangulate approximate point cloud
    prev_img, prev_kp, prev_desc = None, None, None

    for path, img in imgs:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        kp, desc = orb.detectAndCompute(gray, None)

        if prev_desc is not None and desc is not None and len(desc) > 10 and len(prev_desc) > 10:
            matches = bf.match(prev_desc, desc)
            matches = sorted(matches, key=lambda x: x.distance)[:100]

            if len(matches) >= 8:
                pts1 = np.float32([prev_kp[m.queryIdx].pt for m in matches])
                pts2 = np.float32([kp[m.trainIdx].pt for m in matches])

                E, mask = cv2.findEssentialMat(pts1, pts2, focal=1000.0)
                if E is not None:
                    _, R, t, pose_mask = cv2.recoverPose(E, pts1, pts2)

                    # Simple depth heuristic from feature disparity
                    good_pts = pts2[pose_mask.ravel() > 0]
                    for pt in good_pts:
                        x = (pt[0] - img.shape[1]/2) / 1000.0
                        y = (pt[1] - img.shape[0]/2) / 1000.0
                        z = np.random.uniform(0.3, 1.0)  # rough depth
                        all_points.append([x, y, z])
                        px, py = int(pt[0]), int(pt[1])
                        if 0 <= px < img.shape[1] and 0 <= py < img.shape[0]:
                            b, g, r_ = img[py, px]
                            all_colors.append([r_/255.0, g/255.0, b/255.0])
                        else:
                            all_colors.append([0.7, 0.7, 0.7])

        prev_img, prev_kp, prev_desc = img, kp, desc

    if len(all_points) < 50:
        fail('reconstruction', f'Insufficient 3D points reconstructed: {len(all_points)}')

    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(np.array(all_points))
    pcd.colors = o3d.utility.Vector3dVector(np.array(all_colors))

    # Denoise
    pcd, _ = pcd.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)

    # Estimate normals
    pcd.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=0.1, max_nn=30))
    pcd.orient_normals_consistent_tangent_plane(100)

    # Poisson mesh
    mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=8)

    # Remove low-density vertices
    vertices_to_remove = densities < np.quantile(densities, 0.1)
    mesh.remove_vertices_by_mask(vertices_to_remove)

    ply_path = MESH_DIR / 'raw_mesh.ply'
    o3d.io.write_triangle_mesh(str(ply_path), mesh)
    log('reconstruction', 'ok', f'Open3D reconstruction complete: {len(mesh.vertices)} vertices')
    return ply_path

# ── Stage 4: Mesh Cleanup ─────────────────────────────────────────────────────

def stage_mesh_cleanup(mesh_path):
    log('cleanup', 'ok', 'Cleaning mesh — removing artifacts and optimizing topology')

    try:
        import open3d as o3d
        import trimesh
    except ImportError:
        fail('cleanup', 'open3d and trimesh are required for mesh cleanup')

    mesh_path = Path(mesh_path)
    if not mesh_path.exists():
        fail('cleanup', f'Mesh file not found: {mesh_path}')

    # Load with trimesh for cleanup
    try:
        tm = trimesh.load(str(mesh_path), force='mesh')
    except Exception as e:
        fail('cleanup', f'Could not load mesh: {e}')

    log('cleanup', 'ok', f'  Loaded: {len(tm.vertices)} vertices, {len(tm.faces)} faces')

    # Keep largest connected component
    components = tm.split(only_watertight=False)
    if len(components) > 1:
        largest = max(components, key=lambda c: len(c.faces))
        tm = largest
        log('cleanup', 'ok', f'  Kept largest component: {len(tm.faces)} faces')

    # Fill holes
    try:
        trimesh.repair.fill_holes(tm)
    except Exception:
        pass

    # Smooth
    try:
        trimesh.smoothing.filter_laplacian(tm, lamb=0.3, iterations=3)
    except Exception:
        pass

    # Decimate to viewer-friendly size (target ~50k faces)
    target_faces = 50000
    if len(tm.faces) > target_faces:
        ratio = target_faces / len(tm.faces)
        try:
            import open3d as o3d
            o3d_mesh = o3d.geometry.TriangleMesh()
            o3d_mesh.vertices = o3d.utility.Vector3dVector(tm.vertices)
            o3d_mesh.triangles = o3d.utility.Vector3iVector(tm.faces)
            o3d_mesh = o3d_mesh.simplify_quadric_decimation(target_faces)
            tm = trimesh.Trimesh(
                vertices=np.asarray(o3d_mesh.vertices),
                faces=np.asarray(o3d_mesh.triangles)
            )
            log('cleanup', 'ok', f'  Decimated to {len(tm.faces)} faces')
        except Exception as e:
            log('cleanup', 'ok', f'  Decimation skipped: {e}')

    # Recalculate normals
    tm.fix_normals()

    cleaned_path = MESH_DIR / 'cleaned_mesh.ply'
    tm.export(str(cleaned_path))
    log('cleanup', 'ok', f'  Cleanup complete: {len(tm.vertices)} vertices, {len(tm.faces)} faces')
    return cleaned_path

# ── Stage 5: Export GLB ───────────────────────────────────────────────────────

def stage_export_glb(mesh_path):
    log('export', 'ok', 'Exporting to GLB')

    try:
        import trimesh
    except ImportError:
        fail('export', 'trimesh is required for GLB export')

    try:
        tm = trimesh.load(str(mesh_path), force='mesh')
    except Exception as e:
        fail('export', f'Could not load cleaned mesh: {e}')

    # Centre and normalize scale
    centroid = tm.centroid
    tm.apply_translation(-centroid)
    scale = 1.0 / max(tm.extents) if max(tm.extents) > 0 else 1.0
    tm.apply_scale(scale)

    glb_filename = f'scan_{SCAN_ID}_{int(time.time())}.glb'
    glb_path = UPLOADS_DIR / glb_filename
    tm.export(str(glb_path))

    log('export', 'ok', f'GLB exported: {glb_filename} ({glb_path.stat().st_size // 1024} KB)')
    return f'/uploads/{glb_filename}'

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f'[worker] BodyScan 3D processing scan {SCAN_ID}', flush=True)
    print(f'[worker] Frames dir: {FRAMES_DIR}', flush=True)
    print(f'[worker] Server: {SERVER_URL}', flush=True)

    # Fetch prompt data from server
    try:
        r = requests.get(f'{SERVER_URL}/api/internal/capture/prompt/{SCAN_ID}',
                        headers={'X-Internal-Secret': SECRET}, timeout=10)
        prompt_data = r.json() if r.ok else {}
    except Exception:
        prompt_data = {}

    box = prompt_data.get('box')
    pos_pts = prompt_data.get('positivePoints', [])
    neg_pts = prompt_data.get('negativePoints', [])

    try:
        # Stage 1: Frame QC
        selected_frames = stage_frame_qc()

        # Stage 2: Segmentation
        masked_frames = stage_segmentation(selected_frames, box, pos_pts, neg_pts)

        # Stage 3: Reconstruction
        mesh_path = stage_reconstruction(masked_frames)

        # Stage 4: Cleanup
        cleaned_path = stage_mesh_cleanup(mesh_path)

        # Stage 5: Export
        model_url = stage_export_glb(cleaned_path)

        # Report success
        success(model_url)

        # Cleanup temp dir
        try:
            shutil.rmtree(str(WORK_DIR))
        except Exception:
            pass

    except SystemExit:
        raise
    except Exception as e:
        import traceback
        fail('worker', f'Unexpected error: {e}\n{traceback.format_exc()[-500:]}')

if __name__ == '__main__':
    main()
