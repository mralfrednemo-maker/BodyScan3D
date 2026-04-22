"""
Keyframe-count sweep for scan 14's video.

Re-extracts keyframes at multiple K values using the same bin algorithm as
video_worker, runs sparse SfM (pycolmap) on each set, and reports:
  - frames registered / total
  - sparse points
  - mean reprojection error
  - elapsed time

Usage (from inside WSL, bs3d-venv active):
  python keyframe_count_sweep.py
"""
import os
import sys
import time
import uuid
import shutil
import tempfile

import cv2
import pycolmap

# Re-use video_worker's scoring + selection so the experiment matches production
sys.path.insert(0, '/mnt/c/Users/chris/PROJECTS/BodyScan3D/workers')
import importlib
vw = importlib.import_module('video_worker')

VIDEO = '/mnt/c/Users/chris/PROJECTS/BodyScan3D/uploads/videos/14.mp4'
SAMPLE_INTERVAL_MS = 200          # keep parity with production
K_VALUES = [15, 25, 40]           # K=25 is the production baseline


def write_frames(samples, outdir):
    """Write the chosen sample frames as JPGs in outdir using cv2."""
    cap = cv2.VideoCapture(VIDEO)
    if not cap.isOpened():
        raise RuntimeError(f'cannot open {VIDEO}')
    paths = []
    try:
        for t_ms, _ in samples:
            cap.set(cv2.CAP_PROP_POS_MSEC, t_ms)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            p = os.path.join(outdir, f'{uuid.uuid4()}.jpg')
            cv2.imwrite(p, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            paths.append(p)
    finally:
        cap.release()
    return paths


def run_sparse(image_dir, work_dir):
    """Run pycolmap sparse SfM. Returns dict with stats or None on failure."""
    db_path = os.path.join(work_dir, 'database.db')
    sparse_dir = os.path.join(work_dir, 'sparse')
    os.makedirs(sparse_dir, exist_ok=True)

    pycolmap.extract_features(db_path, image_dir)
    pycolmap.match_exhaustive(db_path)
    recs = pycolmap.incremental_mapping(db_path, image_dir, sparse_dir)
    if not recs:
        return None
    # Pick the largest reconstruction
    best = max(recs.values(), key=lambda r: r.num_reg_images())
    return {
        'reg_images': best.num_reg_images(),
        'points':     len(best.points3D),
        'mean_rep':   best.compute_mean_reprojection_error()
                       if hasattr(best, 'compute_mean_reprojection_error')
                       else None,
    }


def main():
    print(f'[sweep] scoring video once @ {SAMPLE_INTERVAL_MS}ms intervals')
    vw.SAMPLE_INTERVAL_MS = SAMPLE_INTERVAL_MS
    scores = vw._score_timestamps(VIDEO)
    print(f'[sweep] {len(scores)} candidate timestamps')

    print()
    print(f'{"K":>5}  {"reg/total":>10}  {"points":>7}  {"rep_err":>8}  {"time_s":>7}')
    print('-' * 50)

    for K in K_VALUES:
        vw.KEYFRAMES_TARGET = K
        chosen = vw._select_timestamps(scores)

        with tempfile.TemporaryDirectory(prefix=f'sweep_K{K}_') as tmp:
            img_dir  = os.path.join(tmp, 'images')
            work_dir = os.path.join(tmp, 'work')
            os.makedirs(img_dir);  os.makedirs(work_dir)

            written = write_frames(chosen, img_dir)
            t0 = time.time()
            try:
                result = run_sparse(img_dir, work_dir)
            except Exception as e:
                print(f'{K:>5}  {"ERROR":>10}  {str(e)[:30]}')
                continue
            elapsed = time.time() - t0

            if result is None:
                print(f'{K:>5}  {"FAILED":>10}  {"-":>7}  {"-":>8}  {elapsed:>7.1f}')
            else:
                rep = f'{result["mean_rep"]:.2f}' if result['mean_rep'] is not None else 'n/a'
                print(f'{K:>5}  {result["reg_images"]:>4}/{len(written):<5}  '
                      f'{result["points"]:>7}  {rep:>8}  {elapsed:>7.1f}')


if __name__ == '__main__':
    main()
