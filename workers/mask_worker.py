"""
mask_worker.py — Phase 2 worker: generate per-frame masks using SAM 2.

Runs after prompt is submitted (MASKING state).
Accepts scan_id as a command-line argument.

GPU: Uses SAM 2 small checkpoint (~2.5GB VRAM) when SAM2_MOCK=0.
CPU fallback: SAM2_MOCK=1 generates solid-mask approximations using the
              bounding box from the prompt (no GPU needed for development).

Usage:
    python mask_worker.py <scan_id>
    SAM2_MOCK=1 python mask_worker.py <scan_id>     # dev / no-GPU mode
"""
import sys
import os
import requests
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from config import (
    API_BASE, FRAMES_DIR, MASKS_DIR, internal_headers,
    SAM2_MOCK, SAM2_CHECKPOINT, SAM2_CONFIG
)


def log(msg):
    print(f'[mask_worker] {msg}', flush=True)


# ---------------------------------------------------------------------------
# Mock segmentation (SAM2_MOCK=1) — solid-fill box mask, no GPU needed
# ---------------------------------------------------------------------------
def mock_mask(frame_path, norm_box, out_path):
    """Generate a solid white mask inside the bounding box."""
    try:
        from PIL import Image
        img = Image.open(frame_path)
        w, h = img.size
    except Exception:
        w, h = 1280, 720

    mask = np.zeros((h, w), dtype=np.uint8)
    if norm_box:
        x1 = int(norm_box[0] * w)
        y1 = int(norm_box[1] * h)
        x2 = int(norm_box[2] * w)
        y2 = int(norm_box[3] * h)
        mask[y1:y2, x1:x2] = 255
    else:
        # No box: mask full centre 60% of frame
        py1, py2 = int(h * 0.2), int(h * 0.8)
        px1, px2 = int(w * 0.2), int(w * 0.8)
        mask[py1:py2, px1:px2] = 255

    try:
        from PIL import Image as PILImage
        PILImage.fromarray(mask, 'L').save(out_path)
    except Exception:
        import cv2
        cv2.imwrite(out_path, mask)


# ---------------------------------------------------------------------------
# Real SAM 2 segmentation
# ---------------------------------------------------------------------------
def sam2_masks(scan_id, frames, prompt_anchor, out_dir):
    """
    Run SAM 2 on all frames.

    prompt_anchor: dict with keys frameId, box (normalised [x1,y1,x2,y2])
    frames: list of frame dicts from server
    Returns: list of (frame_id, mask_path) pairs
    """
    import torch
    from sam2.build_sam import build_sam2_video_predictor

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    log(f'SAM 2 device: {device}')

    predictor = build_sam2_video_predictor(SAM2_CONFIG, SAM2_CHECKPOINT, device=device)

    # Build frame directory with symlinks in sort order (SAM 2 expects a directory)
    import tempfile
    frame_dir = tempfile.mkdtemp(prefix=f'bs3d_sam2_{scan_id}_')
    frame_paths = []
    for i, f in enumerate(frames):
        filename = os.path.basename(f['frameUrl'])
        src = os.path.join(FRAMES_DIR, filename)
        dst = os.path.join(frame_dir, f'{i:04d}.jpg')
        if os.path.exists(src):
            try:
                os.symlink(src, dst)
            except OSError:
                import shutil
                shutil.copy2(src, dst)
        frame_paths.append(dst)

    # Find the index of the prompt frame in the sorted frame list
    prompt_frame_id = prompt_anchor.get('frameId') if prompt_anchor else None
    prompt_idx = 0
    for i, f in enumerate(frames):
        if f['id'] == prompt_frame_id:
            prompt_idx = i
            break

    # Determine prompt type (box, points, or both)
    norm_box = prompt_anchor.get('box') if prompt_anchor else None
    norm_points = prompt_anchor.get('points', []) if prompt_anchor else []

    with torch.inference_mode(), torch.autocast(device, dtype=torch.bfloat16):
        state = predictor.init_state(video_path=frame_dir)

        from PIL import Image
        img = Image.open(frame_paths[prompt_idx])
        iw, ih = img.size

        kwargs = dict(inference_state=state, frame_idx=prompt_idx, obj_id=1)

        if norm_box:
            kwargs['box'] = np.array([
                norm_box[0] * iw, norm_box[1] * ih,
                norm_box[2] * iw, norm_box[3] * ih
            ], dtype=np.float32)

        if norm_points:
            # SAM 2 point prompts: coords + labels (1=positive, 0=negative)
            coords = np.array([[p['x'] * iw, p['y'] * ih] for p in norm_points], dtype=np.float32)
            labels = np.array([p.get('label', 1) for p in norm_points], dtype=np.int32)
            kwargs['points'] = coords
            kwargs['labels'] = labels

        if not norm_box and not norm_points:
            # Auto-prompt: centre point of the frame
            kwargs['points'] = np.array([[iw // 2, ih // 2]], dtype=np.float32)
            kwargs['labels'] = np.array([1], dtype=np.int32)

        predictor.add_new_points_or_box(**kwargs)

        results = []
        for frame_idx, obj_ids, mask_logits in predictor.propagate_in_video(state):
            mask_np = (mask_logits[0, 0] > 0.0).cpu().numpy().astype(np.uint8) * 255
            frame_id = frames[frame_idx]['id']
            out_path = os.path.join(out_dir, f'mask_{frame_id:06d}.png')
            try:
                from PIL import Image as PILImage
                PILImage.fromarray(mask_np, 'L').save(out_path)
            except Exception:
                import cv2
                cv2.imwrite(out_path, mask_np)
            results.append((frame_id, out_path))
        return results


# ---------------------------------------------------------------------------
# Body-part-aware correction rules
# ---------------------------------------------------------------------------
# Anatomy-specific post-filtering: reject masks that violate body-part priors.
# E.g., for 'arm' the mask should be elongated, not nearly square.

BODY_PART_RULES = {
    'arm':      {'min_aspect': 1.5, 'max_area_ratio': 0.35},
    'leg':      {'min_aspect': 1.5, 'max_area_ratio': 0.40},
    'torso':    {'min_aspect': 0.5, 'max_area_ratio': 0.60},
    'back':     {'min_aspect': 0.5, 'max_area_ratio': 0.60},
    'shoulder': {'min_aspect': 0.8, 'max_area_ratio': 0.30},
    'chest':    {'min_aspect': 0.5, 'max_area_ratio': 0.50},
    'abdomen':  {'min_aspect': 0.5, 'max_area_ratio': 0.50},
    'face':     {'min_aspect': 0.6, 'max_area_ratio': 0.25},
}


def apply_body_part_rules(results, body_part):
    """
    Post-filter masks using anatomy-specific heuristics.
    Flags low-confidence masks but doesn't delete them (worker logs warnings).
    """
    rules = BODY_PART_RULES.get(body_part)
    if not rules:
        return results

    flagged = 0
    for frame_id, mask_path in results:
        try:
            from PIL import Image
            mask = np.array(Image.open(mask_path).convert('L'))
            h, w = mask.shape
            total_px = h * w
            mask_px = int((mask > 127).sum())
            area_ratio = mask_px / total_px if total_px > 0 else 0

            # Check area ratio (mask shouldn't cover too much of the frame)
            if area_ratio > rules['max_area_ratio']:
                log(f'  WARN frame {frame_id}: mask covers {area_ratio:.1%} of frame (max {rules["max_area_ratio"]:.0%} for {body_part})')
                flagged += 1

            # Check aspect ratio of mask bounding box
            rows = np.any(mask > 127, axis=1)
            cols = np.any(mask > 127, axis=0)
            if rows.any() and cols.any():
                rmin, rmax = np.where(rows)[0][[0, -1]]
                cmin, cmax = np.where(cols)[0][[0, -1]]
                mh = rmax - rmin + 1
                mw = cmax - cmin + 1
                aspect = max(mh, mw) / (min(mh, mw) + 1)
                if aspect < rules['min_aspect']:
                    log(f'  WARN frame {frame_id}: mask aspect {aspect:.1f} < {rules["min_aspect"]} for {body_part}')
                    flagged += 1
        except Exception as e:
            log(f'  body-part rule check failed for frame {frame_id}: {e}')

    if flagged:
        log(f'Body-part rules: {flagged} warnings for body_part={body_part}')
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run(scan_id):
    log(f'Starting masking for scan {scan_id}')

    # Fetch all frames
    r = requests.get(f'{API_BASE}/api/internal/scans/{scan_id}/frames', headers=internal_headers(), timeout=30)
    r.raise_for_status()
    frames = r.json()
    log(f'{len(frames)} frames loaded')

    # Fetch prompt (includes positive/negative points, box, bodyPart)
    rp = requests.get(f'{API_BASE}/api/internal/scans/{scan_id}/prompt', headers=internal_headers(), timeout=10)
    rp.raise_for_status()
    prompt_data = rp.json()
    anchor_list = prompt_data.get('anchors', [])
    prompt_anchor = anchor_list[0] if anchor_list else None
    body_part = (prompt_anchor or {}).get('bodyPart', 'torso')
    log(f'Prompt anchor: {prompt_anchor} | body_part: {body_part}')

    # Create per-scan mask directory
    scan_mask_dir = os.path.join(MASKS_DIR, str(scan_id))
    os.makedirs(scan_mask_dir, exist_ok=True)

    if SAM2_MOCK:
        log('SAM2_MOCK=1 — generating solid-fill masks (no GPU)')
        results = []
        norm_box = prompt_anchor.get('box') if prompt_anchor else None
        for frame in frames:
            filename = os.path.basename(frame['frameUrl'])
            frame_path = os.path.join(FRAMES_DIR, filename)
            out_path = os.path.join(scan_mask_dir, f'mask_{frame["id"]:06d}.png')
            mock_mask(frame_path, norm_box, out_path)
            results = results + [(frame['id'], out_path)]
    else:
        # Auto-prompt fallback: if no user-supplied anchor (Slice 1.5 has no
        # segmentation UI), SAM 2 gets a centre-point prompt per sam2_masks()
        # defaults. This keeps the pipeline unblocked end-to-end.
        results = sam2_masks(scan_id, frames, prompt_anchor, scan_mask_dir)

    log(f'Generated {len(results)} masks')

    # Body-part-aware correction rules
    results = apply_body_part_rules(results, body_part)

    # Post mask paths back to server
    mask_records = [
        {'frameId': fid, 'maskUrl': f'/uploads/masks/{scan_id}/mask_{fid:06d}.png'}
        for fid, _ in results
    ]
    requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/masks',
        headers=internal_headers(),
        json={'masks': mask_records},
        timeout=30
    ).raise_for_status()

    # Transition → RECONSTRUCTING
    requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/status',
        headers=internal_headers(),
        json={'status': 'RECONSTRUCTING', 'message': f'{len(results)} masks generated'},
        timeout=10
    ).raise_for_status()

    log('Masking complete')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python mask_worker.py <scan_id>')
        sys.exit(1)
    run(sys.argv[1])
