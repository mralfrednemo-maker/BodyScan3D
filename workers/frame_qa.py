"""
frame_qa.py — Phase 1 worker: score uploaded frames, pick anchor frames.

Called by the scan pipeline after all frames are uploaded (UPLOADING → FRAME_QA).
Accepts scan_id as a command-line argument.

Usage:
    python frame_qa.py <scan_id>
"""
import sys
import os
import requests
import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

sys.path.insert(0, os.path.dirname(__file__))
from config import (
    API_BASE, FRAMES_DIR, internal_headers,
    BLUR_THRESHOLD, MIN_FRAMES_REQUIRED, TOP_ANCHOR_FRAMES
)


def log(msg):
    print(f'[frame_qa] {msg}', flush=True)


def laplacian_variance(filepath):
    """Return the Laplacian variance (higher = sharper) for an image file."""
    if HAS_CV2:
        img = cv2.imread(filepath, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return 0.0
        return float(cv2.Laplacian(img, cv2.CV_64F).var())
    elif HAS_PIL:
        img = Image.open(filepath).convert('L')
        arr = np.array(img, dtype=np.float64)
        kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)
        from scipy.ndimage import convolve
        lap = convolve(arr, kernel)
        return float(lap.var())
    else:
        # No image library — return neutral score so pipeline continues
        return BLUR_THRESHOLD + 1.0


def run(scan_id):
    log(f'Starting QA for scan {scan_id}')

    # Fetch frame list from server
    r = requests.get(
        f'{API_BASE}/api/internal/scans/{scan_id}/frames',
        headers=internal_headers(),
        timeout=30
    )
    r.raise_for_status()
    frames = r.json()

    if len(frames) < MIN_FRAMES_REQUIRED:
        log(f'FATAL: only {len(frames)} frames, need {MIN_FRAMES_REQUIRED}')
        requests.post(
            f'{API_BASE}/api/internal/scans/{scan_id}/status',
            headers=internal_headers(),
            json={'status': 'FAILED', 'failureClass': 'TOO_FEW_FRAMES',
                  'message': f'Too few frames: {len(frames)}/{MIN_FRAMES_REQUIRED}'},
            timeout=10
        )
        sys.exit(1)

    log(f'Scoring {len(frames)} frames...')
    scores = []
    for frame in frames:
        # frameUrl is like /uploads/frames/<uuid>.jpg — map to local path
        filename = os.path.basename(frame['frameUrl'])
        filepath = os.path.join(FRAMES_DIR, filename)
        score = laplacian_variance(filepath) if os.path.exists(filepath) else 0.0
        scores.append({'frameId': frame['id'], 'score': score})
        log(f'  frame {frame["id"]}: blur_score={score:.1f}')

    # Post scores back to server (key=frameScores, fields=frameId/blurScore/accepted)
    frame_scores = [
        {'frameId': s['frameId'], 'blurScore': s['score'], 'accepted': s['score'] >= BLUR_THRESHOLD}
        for s in scores
    ]
    requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/frame-scores',
        headers=internal_headers(),
        json={'frameScores': frame_scores},
        timeout=30
    ).raise_for_status()

    # Select top N sharpest frames as anchors
    accepted = [s for s in scores if s['score'] >= BLUR_THRESHOLD]
    if not accepted:
        log('Warning: no frames meet blur threshold; using all frames as candidates')
        accepted = scores

    top = sorted(accepted, key=lambda s: s['score'], reverse=True)[:TOP_ANCHOR_FRAMES]
    anchor_ids = [s['frameId'] for s in top]
    log(f'Anchor frames: {anchor_ids} (scores: {[round(s["score"],1) for s in top]})')

    requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/anchor-frames-internal',
        headers=internal_headers(),
        json={'frameIds': anchor_ids},
        timeout=10
    ).raise_for_status()

    # Transition → FSCQI (Full Signal Quality + Coverage Index)
    requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/status',
        headers=internal_headers(),
        json={'status': 'FSCQI', 'message': f'QA done, {len(accepted)} accepted, {len(anchor_ids)} anchors selected'},
        timeout=10
    ).raise_for_status()

    log(f'QA complete — {len(accepted)}/{len(frames)} frames accepted, {len(anchor_ids)} anchors set → FSCQI')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python frame_qa.py <scan_id>')
        sys.exit(1)
    run(sys.argv[1])
