"""
video_worker.py — Phase 0 worker: extract sharp, diverse keyframes from an
uploaded walkaround video and hand them to the existing frame-QA pipeline.

Called by pipeline.py when scan state is VIDEO_UPLOADED.

Pipeline integration:
  VIDEO_UPLOADED -> EXTRACTING_KEYFRAMES (claimed) -> FRAME_QA
                                                      ^
                                                      frame_qa.py takes over

  EXTRACTING_KEYFRAMES is NOT in the server's pending-scans query, so the poller
  will not re-dispatch this worker if it crashes mid-run. That eliminates the
  duplicate-frame race (Codex review #3).

Algorithm (two-pass, memory-bounded):
  Pass 1 — score:
    Sample one frame every SAMPLE_INTERVAL_MS, compute Laplacian variance,
    keep ONLY (timestamp_ms, sharpness). The decoded frame is released as soon
    as the score is computed, so RAM is bounded regardless of video length.
  Select:
    Walk candidates in descending sharpness, accept any that is at least
    MIN_TEMPORAL_GAP_MS away from every already-kept timestamp, until
    KEYFRAMES_TARGET frames are kept.
  Pass 2 — decode + write:
    Re-seek to each selected timestamp, decode, write JPG to /uploads/frames.

Usage:
    python video_worker.py <scan_id>
"""
import os
import sys
import uuid
import requests

import cv2  # required — fail loud if missing rather than silently degrading

sys.path.insert(0, os.path.dirname(__file__))
from config import API_BASE, FRAMES_DIR, internal_headers


def log(msg):
    print(f'[video_worker] {msg}', flush=True)


# Tunables (env-overridable so we can A/B without code changes)
SAMPLE_INTERVAL_MS  = int(os.environ.get('VIDEO_SAMPLE_INTERVAL_MS', '200'))
MIN_TEMPORAL_GAP_MS = int(os.environ.get('VIDEO_MIN_TEMPORAL_GAP_MS', '400'))
KEYFRAMES_TARGET    = int(os.environ.get('VIDEO_KEYFRAMES_TARGET', '15'))
JPEG_QUALITY        = int(os.environ.get('VIDEO_JPEG_QUALITY', '92'))
MAX_DURATION_S      = int(os.environ.get('VIDEO_MAX_DURATION_S', '120'))  # hard ceiling


# ---------------------------------------------------------------------------
# Server-state helpers
# ---------------------------------------------------------------------------

def _post_status(scan_id, status, message=None, failure_class=None):
    """POST a status update. Returns True on success, False on network error."""
    body = {'status': status}
    if message:        body['message']      = message
    if failure_class:  body['failureClass'] = failure_class
    try:
        r = requests.post(
            f'{API_BASE}/api/internal/scans/{scan_id}/status',
            headers=internal_headers(),
            json=body,
            timeout=10
        )
        return r.ok
    except Exception as e:
        log(f'status POST failed ({status}): {e}')
        return False


def _fail(scan_id, failure_class, message):
    log(f'FATAL: {message}')
    _post_status(scan_id, 'FAILED', message=message, failure_class=failure_class)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Video resolution
# ---------------------------------------------------------------------------

def _maybe_translate_windows_path(p):
    """Convert C:\\Users\\... to /mnt/c/Users/... only when running inside WSL."""
    if not p:
        return p
    # Only attempt WSL translation when /mnt/c exists (WSL environment indicator)
    if not os.path.exists('/mnt/c'):
        return p
    if len(p) >= 3 and p[1] == ':' and p[2] in ('\\', '/'):
        drive = p[0].lower()
        rest  = p[3:].replace('\\', '/')
        return f'/mnt/{drive}/{rest}'
    return p


def _resolve_video(scan_id):
    r = requests.get(
        f'{API_BASE}/api/internal/scans/{scan_id}/video',
        headers=internal_headers(),
        timeout=15
    )
    if r.status_code == 404:
        raise FileNotFoundError(f'Server has no video on disk for scan {scan_id}')
    r.raise_for_status()
    info = r.json()
    abs_path = info.get('absPath')
    if not abs_path or not os.path.exists(abs_path):
        abs_path = _maybe_translate_windows_path(info.get('absPath'))
    if not abs_path or not os.path.exists(abs_path):
        raise FileNotFoundError(f'Video path not reachable from worker: {info.get("absPath")}')
    return abs_path


# ---------------------------------------------------------------------------
# Pass 1 — score timestamps (frame released immediately, no RAM blowup)
# ---------------------------------------------------------------------------

def _score_timestamps(video_path):
    """Return list of (timestamp_ms, sharpness). Frames are NOT retained."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f'OpenCV could not open video: {video_path}')

    fps   = cap.get(cv2.CAP_PROP_FPS) or 0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if fps <= 0 or total <= 0:
        cap.release()
        raise RuntimeError(f'Video has no readable FPS / frame count (fps={fps}, total={total})')

    duration_ms = (total / fps) * 1000.0
    if duration_ms > MAX_DURATION_S * 1000:
        cap.release()
        raise RuntimeError(
            f'Video duration {duration_ms/1000:.1f}s exceeds limit of {MAX_DURATION_S}s'
        )

    log(f'video opened: {total} frames, {fps:.2f} fps, {duration_ms/1000:.1f}s')

    scores = []
    t_ms   = 0.0
    try:
        while t_ms < duration_ms:
            cap.set(cv2.CAP_PROP_POS_MSEC, t_ms)
            ok, frame = cap.read()
            if ok and frame is not None:
                gray      = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                scores.append((t_ms, sharpness))
            t_ms += SAMPLE_INTERVAL_MS
    finally:
        cap.release()

    log(f'scored {len(scores)} candidate timestamps at {SAMPLE_INTERVAL_MS}ms intervals')
    return scores


def _select_timestamps(scores):
    """
    Bin-based selection: divide the video duration into KEYFRAMES_TARGET equal
    bins and keep the SHARPEST frame from each bin. Guarantees that keyframes
    are spread evenly across the walkaround, which is what photogrammetry needs
    (every adjacent pair has meaningful parallax).

    A pure top-K-sharpness greedy scan tends to pick frames that cluster in
    whichever segment of the video happened to be in best focus, leaving large
    gaps elsewhere — which makes pycolmap's initial-pair search fail.

    Falls back to MIN_TEMPORAL_GAP_MS gap-greedy only if there are fewer
    candidates than bins.
    """
    if not scores:
        return []

    if len(scores) < KEYFRAMES_TARGET:
        # Not enough samples for binning — keep all of them, sorted temporally.
        return sorted(scores, key=lambda s: s[0])

    t_min = scores[0][0]
    t_max = scores[-1][0]   # _score_timestamps yields in temporal order
    span  = max(t_max - t_min, 1.0)
    bin_w = span / KEYFRAMES_TARGET

    # Pre-bucket candidates so each bin keeps only its sharpest frame.
    best_per_bin = {}
    for t_ms, sharpness in scores:
        idx = min(int((t_ms - t_min) / bin_w), KEYFRAMES_TARGET - 1)
        cur = best_per_bin.get(idx)
        if (cur is None) or (sharpness > cur[1]):
            best_per_bin[idx] = (t_ms, sharpness)

    kept = sorted(best_per_bin.values(), key=lambda s: s[0])
    return kept


# ---------------------------------------------------------------------------
# Pass 2 — decode + write only the selected timestamps
# ---------------------------------------------------------------------------

def _write_selected_frames(video_path, selected):
    """
    selected: list of (timestamp_ms, sharpness) in temporal order.
    Returns list of {frameUrl, blurScore, sortOrder} dicts and the paths
    written so they can be cleaned up if the registration call fails.
    """
    os.makedirs(FRAMES_DIR, exist_ok=True)
    encoded = [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f'OpenCV could not reopen video for pass 2: {video_path}')

    out, paths = [], []
    try:
        for sort_order, (t_ms, sharpness) in enumerate(selected):
            cap.set(cv2.CAP_PROP_POS_MSEC, t_ms)
            ok, frame = cap.read()
            if not ok or frame is None:
                log(f'  skip t={t_ms/1000:.2f}s — could not re-decode')
                continue
            fname = f'{uuid.uuid4()}.jpg'
            fpath = os.path.join(FRAMES_DIR, fname)
            if not cv2.imwrite(fpath, frame, encoded):
                raise RuntimeError(f'Failed to write keyframe to {fpath}')
            paths.append(fpath)
            out.append({
                'frameUrl':  f'/uploads/frames/{fname}',
                'blurScore': sharpness,
                'sortOrder': sort_order
            })
    finally:
        cap.release()

    return out, paths


def _cleanup_paths(paths):
    """Delete paths — but only if they're inside FRAMES_DIR (defence-in-depth)."""
    frames_root = os.path.realpath(FRAMES_DIR)
    for p in paths:
        try:
            real = os.path.realpath(p)
            if os.path.commonpath([real, frames_root]) != frames_root:
                log(f'refusing to unlink path outside FRAMES_DIR: {p}')
                continue
            os.unlink(real)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(scan_id):
    log(f'starting video keyframe extraction for scan {scan_id}')

    # Atomically claim the scan via compare-and-swap. Only one worker can win
    # the VIDEO_UPLOADED -> EXTRACTING_KEYFRAMES transition; concurrent callers
    # get HTTP 409. Eliminates the duplicate-extract race the unconditional
    # status update could not prevent.
    try:
        r = requests.post(
            f'{API_BASE}/api/internal/scans/{scan_id}/claim-state',
            headers=internal_headers(),
            json={'from': 'VIDEO_UPLOADED', 'to': 'EXTRACTING_KEYFRAMES'},
            timeout=10
        )
    except Exception as e:
        log(f'FATAL: claim-state POST failed: {e}. Poller will retry next cycle.')
        sys.exit(1)

    if r.status_code == 409:
        # Already claimed by another worker — clean exit, not a failure.
        body = (r.json() if r.headers.get('content-type','').startswith('application/json') else {})
        log(f'scan already claimed (current state: {body.get("current")}); exiting cleanly')
        sys.exit(0)
    if not r.ok:
        log(f'FATAL: claim-state returned HTTP {r.status_code}. Poller will retry next cycle.')
        sys.exit(1)

    try:
        video_path = _resolve_video(scan_id)
    except FileNotFoundError as e:
        _fail(scan_id, 'INTERNAL_ERROR', str(e))

    log(f'source: {video_path}')

    try:
        scores = _score_timestamps(video_path)
    except RuntimeError as e:
        _fail(scan_id, 'INTERNAL_ERROR', str(e))

    if not scores:
        _fail(scan_id, 'TOO_FEW_FRAMES', 'Video produced no decodable frames')

    selected = _select_timestamps(scores)
    if len(selected) < 5:
        _fail(scan_id, 'TOO_FEW_FRAMES',
              f'Only {len(selected)} usable keyframes extracted — record a longer or steadier walkaround')

    log(f'selected {len(selected)} keyframes (target={KEYFRAMES_TARGET})')
    for t_ms, sharpness in selected:
        log(f'  t={t_ms/1000:.2f}s  sharpness={sharpness:.1f}')

    try:
        written, paths = _write_selected_frames(video_path, selected)
    except RuntimeError as e:
        _fail(scan_id, 'INTERNAL_ERROR', str(e))

    if len(written) < 5:
        _cleanup_paths(paths)
        _fail(scan_id, 'TOO_FEW_FRAMES',
              f'Only {len(written)} keyframes survived re-decode — video may be corrupt')

    # Register frames with server. If this fails, clean up so we don't leave
    # orphan JPEGs and so the FAILED state is consistent with disk.
    try:
        r = requests.post(
            f'{API_BASE}/api/internal/scans/{scan_id}/frames-register',
            headers=internal_headers(),
            json={'frames': written},
            timeout=30
        )
        r.raise_for_status()
    except Exception as e:
        _cleanup_paths(paths)
        _fail(scan_id, 'INTERNAL_ERROR', f'frames-register POST failed: {e}')

    # Transition to FRAME_QA — existing pipeline takes over from here.
    if not _post_status(scan_id, 'FRAME_QA',
                        message=f'Extracted {len(written)} keyframes from video'):
        # Frames are already registered. Worker exits non-zero so logs flag it,
        # but JPEGs and DB rows stay (they're correct). Operator can manually
        # PATCH the scan to FRAME_QA.
        log('ERROR: frames registered but FRAME_QA transition failed — manual fix needed')
        sys.exit(2)

    log(f'done — {len(written)} keyframes registered, scan -> FRAME_QA')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python video_worker.py <scan_id>')
        sys.exit(1)
    run(sys.argv[1])
