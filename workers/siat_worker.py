"""
siat_worker.py — Phase 3 worker: Subject Isolation and Targeting.

Runs after FSCQI verdict is PROCESS_CLEAN or PROCESS_WITH_FLAGS (SIAT state).
Accepts scan_id as a command-line argument.

Outputs (SI-1):
  - target_alpha_soft    — soft boundary mask (float PNG, 0-1)
  - target_core_mask     — hard binary subject mask (uint8 PNG)
  - target_mask_hard     — strict subject outline (uint8 PNG, thin)
  - boundary_conf_path   — per-pixel boundary confidence (uint8 PNG)
  - ambiguity_tags       — [{frame_id, type, confidence}] for ambiguous regions
  - occlusion_labels     — [{frame_id, region, depth_order}] for occluded zones

Verdict routing:
  PROCESS_CLEAN | PROCESS_WITH_FLAGS → runs SIAT → transitions to REG
  REVIEW_NEEDED → does NOT run SIAT (operator decides)
  RETRY_RECOMMENDED → does NOT run SIAT (new capture)

Usage:
    python siat_worker.py <scan_id>
"""
import sys
import os
import json
import contextlib
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
    API_BASE, FRAMES_DIR, MASKS_DIR, internal_headers,
    SAM2_MOCK, SAM2_CONFIG, SAM2_CHECKPOINT
)


# ─── Configuration ────────────────────────────────────────────────────────────

SIAT_OUTPUT_VERSION = '1.0.0'
# Per-pixel boundary confidence: pixels within EDGE_BAND_PX of mask boundary get
# lower confidence; 0 = edge, 255 = deep interior
EDGE_BAND_PX = 8


def log(msg):
    print(f'[siat] {msg}', flush=True)


# ─── SAM2 soft mask extraction ───────────────────────────────────────────────

def sam2_soft_masks(scan_id, frames, prompt_anchor, out_dir):
    """
    Run SAM 2 and return soft masks (float32) as numpy arrays.
    Returns list of (frame_id, soft_mask_hwc) where soft_mask_hwc is HxWxC float32.
    """
    import torch
    from sam2.build_sam import build_sam2_video_predictor

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    log(f'SAM 2 device: {device}')

    from hydra import initialize, compose
    import os as _os
    _cfg_path = _os.path.dirname(SAM2_CONFIG)  # relative: sam2/configs/sam2.1
    with initialize(config_path=_cfg_path, version_base='1.2'):
        predictor = build_sam2_video_predictor(
            _os.path.basename(SAM2_CONFIG),
            SAM2_CHECKPOINT,
            device=device
        )

    import tempfile, shutil
    frame_dir = tempfile.mkdtemp(prefix=f'bs3d_siat_{scan_id}_')
    frame_paths = []
    for i, f in enumerate(frames):
        filename = os.path.basename(f['frameUrl'])
        src = os.path.join(FRAMES_DIR, filename)
        dst = os.path.join(frame_dir, f'{i:04d}.jpg')
        if os.path.exists(src):
            try:
                os.symlink(src, dst)
            except OSError:
                shutil.copy2(src, dst)
        frame_paths.append(dst)

    prompt_frame_id = (prompt_anchor or {}).get('frameId')
    prompt_idx = 0
    for i, f in enumerate(frames):
        if f['id'] == prompt_frame_id:
            prompt_idx = i
            break

    norm_box = (prompt_anchor or {}).get('box')
    norm_points = (prompt_anchor or {}).get('points', [])

    autocast_ctx = torch.autocast(device, dtype=torch.bfloat16) if device == 'cuda' else contextlib.nullcontext()
    with torch.inference_mode(), autocast_ctx:
        state = predictor.init_state(video_path=frame_dir)
        img = Image.open(frame_paths[prompt_idx])
        iw, ih = img.size

        kwargs = dict(inference_state=state, frame_idx=prompt_idx, obj_id=1)
        if norm_box:
            kwargs['box'] = np.array([
                norm_box[0]*iw, norm_box[1]*ih, norm_box[2]*iw, norm_box[3]*ih
            ], dtype=np.float32)
        if norm_points:
            coords = np.array([[p['x']*iw, p['y']*ih] for p in norm_points], dtype=np.float32)
            labels = np.array([p.get('label', 1) for p in norm_points], dtype=np.int32)
            kwargs['points'] = coords
            kwargs['labels'] = labels
        if not norm_box and not norm_points:
            kwargs['points'] = np.array([[iw//2, ih//2]], dtype=np.float32)
            kwargs['labels'] = np.array([1], dtype=np.int32)

        predictor.add_new_points_or_box(**kwargs)

        results = []
        for frame_idx, obj_ids, mask_logits in predictor.propagate_in_video(state):
            # mask_logits shape: [1, 1, H, W]
            mask_hwc = torch.sigmoid(mask_logits[0, 0]).cpu().numpy().astype(np.float32)
            frame_id = frames[frame_idx]['id']
            results.append((frame_id, mask_hwc))

    # Cleanup temp dir
    shutil.rmtree(frame_dir, ignore_errors=True)
    return results


def mock_soft_masks(frames, prompt_anchor, out_dir):
    """Fallback soft masks using bounding-box approach when SAM2 unavailable."""
    results = []
    norm_box = (prompt_anchor or {}).get('box')
    for frame in frames:
        filename = os.path.basename(frame['frameUrl'])
        filepath = os.path.join(FRAMES_DIR, filename)
        try:
            if HAS_PIL:
                img = Image.open(filepath)
                w, h = img.size
            else:
                w, h = 1280, 720
        except Exception:
            w, h = 1280, 720

        mask = np.zeros((h, w), dtype=np.float32)
        if norm_box:
            x1,y1,x2,y2 = [int(v) for v in [norm_box[0]*w, norm_box[1]*h, norm_box[2]*w, norm_box[3]*h]]
        else:
            x1,y1,x2,y2 = int(w*0.2), int(h*0.2), int(w*0.8), int(h*0.8)
        # Soft: gradient from edge
        sub = mask[y1:y2, x1:x2]
        sub[:] = 1.0
        results.append((frame['id'], mask))
    return results


# ─── Boundary confidence channel ──────────────────────────────────────────────

def compute_boundary_confidence(soft_mask, edge_band_px=EDGE_BAND_PX):
    """
    Compute boundary confidence: 0 at mask edge, 255 deep in interior.
    Uses distance transform from hard boundary.
    soft_mask: HxW float32 (0-1)
    Returns: HxW uint8 (0-255)
    """
    h, w = soft_mask.shape
    # Hard boundary
    hard = (soft_mask > 0.5).astype(np.uint8)
    # Distance transform from zero (interior distance)
    dist_inside = cv2.distanceTransform(hard, cv2.DIST_L2, cv2.DIST_MASK_3)
    # Normalize and invert: 0 at edge, 255 far from edge
    max_dist = edge_band_px
    conf = np.clip(dist_inside, 0, max_dist) / max_dist * 255
    return conf.astype(np.uint8)


# ─── Ambiguity detection ─────────────────────────────────────────────────────

AMBIGUITY_TYPES = [
    'low_signal',        # blur too low, SAM2 uncertain
    'partial_occlusion', # subject partly occluded
    'edge_ambiguity',   # edge pixels are mixed
    'depth_ambiguity',  # front/back depth ordering unclear
]

def detect_frame_ambiguity(soft_mask, blur_score=None):
    """
    Tag frames with ambiguous subject isolation.
    Returns list of {type, confidence} for this frame.
    """
    tags = []
    h, w = soft_mask.shape

    # 1. Edge ambiguity: fraction of pixels near 0.5
    edge_band = np.abs(soft_mask - 0.5) < 0.15
    edge_fraction = float(edge_band.sum()) / (h * w)
    if edge_fraction > 0.20:
        tags.append({'type': 'edge_ambiguity', 'confidence': round(min(1.0, edge_fraction), 3)})

    # 2. Low signal: most pixels near 0 or 1 (not enough soft boundary)
    interior_fraction = float(((soft_mask < 0.1) | (soft_mask > 0.9)).sum()) / (h * w)
    if interior_fraction > 0.90:
        tags.append({'type': 'low_signal', 'confidence': round(interior_fraction, 3)})

    # 3. Blur-based ambiguity (if blur score available)
    if blur_score is not None and blur_score < 50:
        tags.append({'type': 'low_signal', 'confidence': round(max(0, blur_score / 100), 3)})

    return tags


# ─── Occlusion labels ─────────────────────────────────────────────────────────

def detect_occlusion_regions(soft_mask, frame_id, frame_idx, all_frames_count):
    """
    Estimate occlusion: frames where subject appears partially cut off.
    Returns list of {region, depth_order_hint}.
    """
    h, w = soft_mask.shape
    labels = []

    # Check if subject is cut off at frame edges (possible occlusion)
    border_width = int(min(h, w) * 0.05)
    top_cutoff = float(soft_mask[:border_width, :].mean())
    bottom_cutoff = float(soft_mask[-border_width:, :].mean())
    left_cutoff = float(soft_mask[:, :border_width].mean())
    right_cutoff = float(soft_mask[:, -border_width:].mean())

    if top_cutoff > 0.3:
        labels.append({'region': 'top_edge', 'confidence': round(top_cutoff, 3)})
    if bottom_cutoff > 0.3:
        labels.append({'region': 'bottom_edge', 'confidence': round(bottom_cutoff, 3)})
    if left_cutoff > 0.3:
        labels.append({'region': 'left_edge', 'confidence': round(left_cutoff, 3)})
    if right_cutoff > 0.3:
        labels.append({'region': 'right_edge', 'confidence': round(right_cutoff, 3)})

    # Depth ordering: for now, use frame index as proxy (lower = more frontal)
    # Full depth ordering would require multi-view geometry (deferred to REG)
    if labels:
        labels.append({'region': 'depth_order_hint', 'value': frame_idx / max(1, all_frames_count - 1)})

    return labels


# ─── Output writing ────────────────────────────────────────────────────────────

def write_siat_outputs(scan_id, results, blur_scores_map, all_frames_count, out_dir):
    """
    Write SIAT output artifacts to disk.

    outputs per frame:
      alpha_soft:    HxW float32 → float PNG
      core_mask:     HxW float32 → hard uint8 PNG
      hard_mask:     HxW float32 → thin outline uint8 PNG
      boundary_conf: HxW uint8   → uint8 PNG

    outputs per scan (aggregate):
      static_rigid_core:            HxW uint8 → intersection of core masks (SI-1)
      pose_safe_support:     HxW uint8 → union minus edge-cutoff regions (SI-1)

    Returns dict of output paths.
    """
    os.makedirs(out_dir, exist_ok=True)

    paths = {}
    all_ambiguity_tags = []
    all_occlusion_labels = []
    all_core_masks = []   # for static_rigid_core computation
    all_union_mask = None  # initialized lazily from first frame's dimensions

    for frame_id, soft_mask in results:
        h, w = soft_mask.shape
        # Lazy initialization of union mask using actual frame dimensions
        if all_union_mask is None:
            all_union_mask = np.zeros((h, w), dtype=np.float32)
        blur_score = blur_scores_map.get(frame_id)

        # alpha_soft: save float32 as 16-bit PNG (preserve soft boundary)
        alpha_path = os.path.join(out_dir, f'alpha_soft_{frame_id:06d}.png')
        # Convert 0-1 float to 0-65535 uint16
        alpha_16 = (np.clip(soft_mask, 0, 1) * 65535).astype(np.uint16)
        if HAS_PIL:
            Image.fromarray(alpha_16).save(alpha_path)
        else:
            cv2.imwrite(alpha_path, alpha_16)

        # core_mask: hard binary mask
        core_path = os.path.join(out_dir, f'core_mask_{frame_id:06d}.png')
        core = (soft_mask > 0.5).astype(np.uint8) * 255
        if HAS_PIL:
            Image.fromarray(core, 'L').save(core_path)
        else:
            cv2.imwrite(core_path, core)

        # Track for aggregate artifacts (SI-1 static_rigid_core + pose_safe_support)
        all_core_masks.append(core)
        all_union_mask[:h, :w] = np.maximum(all_union_mask[:h, :w], soft_mask)

        # hard_mask: thin outline using morphological operations
        hard_path = os.path.join(out_dir, f'hard_mask_{frame_id:06d}.png')
        kernel = np.ones((3,3), np.uint8)
        eroded = cv2.erode(core, kernel, iterations=1)
        outline = core - eroded
        if HAS_PIL:
            Image.fromarray(outline, 'L').save(hard_path)
        else:
            cv2.imwrite(hard_path, outline)

        # boundary_confidence_channel
        conf_path = os.path.join(out_dir, f'boundary_conf_{frame_id:06d}.png')
        conf = compute_boundary_confidence(soft_mask)
        if HAS_PIL:
            Image.fromarray(conf, 'L').save(conf_path)
        else:
            cv2.imwrite(conf_path, conf)

        # ambiguity tags
        tags = detect_frame_ambiguity(soft_mask, blur_score)
        for tag in tags:
            all_ambiguity_tags.append({'frame_id': frame_id, **tag})

        # occlusion labels
        frame_idx = next((i for i, f in enumerate(results) if f[0] == frame_id), 0)
        occ_labels = detect_occlusion_regions(soft_mask, frame_id, frame_idx, all_frames_count)
        for lbl in occ_labels:
            all_occlusion_labels.append({'frame_id': frame_id, **lbl})

        paths[frame_id] = {
            'alpha_soft': alpha_path,
            'core_mask': core_path,
            'hard_mask': hard_path,
            'boundary_conf': conf_path
        }

    # Write ambiguity tags + occlusion labels as JSON
    ambig_path = os.path.join(out_dir, 'ambiguity_tags.json')
    with open(ambig_path, 'w') as f:
        json.dump(all_ambiguity_tags, f)
    paths['_ambiguity_tags'] = ambig_path

    occ_path = os.path.join(out_dir, 'occlusion_labels.json')
    with open(occ_path, 'w') as f:
        json.dump(all_occlusion_labels, f)
    paths['_occlusion_labels'] = occ_path

    # ── Aggregate per-scan artifacts (SI-1 required) ─────────────────────────

    # static_rigid_core: pixels that are confident subject in ALL frames — truly rigid
    # (intersection of core binary masks)
    if all_core_masks:
        static_rigid_core = np.bitwise_and.reduce(all_core_masks)  # 0/255 uint8
        rigid_path = os.path.join(out_dir, 'static_rigid_core.png')
        if HAS_PIL:
            Image.fromarray(static_rigid_core, 'L').save(rigid_path)
        else:
            cv2.imwrite(rigid_path, static_rigid_core)
        paths['_static_rigid_core_path'] = rigid_path
        log(f'  static_rigid_core: {(static_rigid_core > 0).sum()} pixels')

    # pose_safe_support_mask: union of soft masks, then mask out edge-cutoff zones.
    # A pixel is pose-safe if: (a) subject appears there in some frame, AND
    # (b) it is NOT cut off at the image border (i.e., not a partial occlusion).
    border_pct = 0.05
    bh = max(1, int(all_union_mask.shape[0] * border_pct))
    bw = max(1, int(all_union_mask.shape[1] * border_pct))
    edge_mask = np.zeros_like(all_union_mask)
    edge_mask[:bh, :] = 1
    edge_mask[-bh:, :] = 1
    edge_mask[:, :bw] = 1
    edge_mask[:, -bw:] = 1
    pose_safe = ((all_union_mask > 0.3) & (edge_mask == 0)).astype(np.uint8) * 255
    pose_safe_path = os.path.join(out_dir, 'pose_safe_support.png')
    if HAS_PIL:
        Image.fromarray(pose_safe, 'L').save(pose_safe_path)
    else:
        cv2.imwrite(pose_safe_path, pose_safe)
    paths['_pose_safe_support_path'] = pose_safe_path
    log(f'  pose_safe_support: {(pose_safe > 0).sum()} pixels')

    return paths


# ─── Main ────────────────────────────────────────────────────────────────────

def run(scan_id):
    log(f'Starting SIAT for scan {scan_id}')

    # ── Step 1: Fetch FSCQI bundle to get primary tier ───────────────────────
    r = requests.get(
        f'{API_BASE}/api/internal/scans/{scan_id}/fscqi-bundle',
        headers=internal_headers(),
        timeout=30
    )
    if not r.ok:
        log(f'FSCQI bundle not found — scan {scan_id} may not have passed FSCQI')
        sys.exit(1)

    bundle = r.json()
    primary_tier = bundle.get('primary_tier', [])
    candidate_tier = bundle.get('candidate_tier', [])
    verdict = bundle.get('verdict', 'REVIEW_NEEDED')

    if verdict in ('REVIEW_NEEDED', 'RETRY_RECOMMENDED'):
        log(f'Verdict is {verdict} — SIAT not run, pipeline blocked')
        sys.exit(0)

    log(f'Verdict={verdict}, primary_tier={len(primary_tier)} frames, candidate_tier={len(candidate_tier)} frames')

    # ── Step 2: Fetch frames and blur scores ──────────────────────────────────
    r2 = requests.get(
        f'{API_BASE}/api/internal/scans/{scan_id}/frames',
        headers=internal_headers(),
        timeout=30
    )
    r2.raise_for_status()
    all_frames = r2.json()

    # Filter to primary tier frames (main processing set)
    primary_frames = [f for f in all_frames if f['id'] in primary_tier]
    if not primary_frames:
        log('WARNING: no primary tier frames — using all frames')
        primary_frames = all_frames

    blur_scores_map = {}
    for f in all_frames:
        if f.get('blurScore') is not None:
            blur_scores_map[f['id']] = f['blurScore']

    log(f'Processing {len(primary_frames)} primary tier frames')

    # ── Step 3: Fetch prompt for SAM2 anchor ──────────────────────────────────
    rp = requests.get(
        f'{API_BASE}/api/internal/scans/{scan_id}/prompt',
        headers=internal_headers(),
        timeout=10
    )
    prompt_data = rp.json() if rp.ok else {}
    anchor_list = prompt_data.get('anchors', [])
    prompt_anchor = anchor_list[0] if anchor_list else None

    # ── Step 4: Run SAM2 soft masking on primary tier ─────────────────────────
    scan_siat_dir = os.path.join(MASKS_DIR, str(scan_id), 'siat', SIAT_OUTPUT_VERSION)
    os.makedirs(scan_siat_dir, exist_ok=True)

    if SAM2_MOCK:
        log('SAM2_MOCK=1 — generating mock soft masks')
        soft_results = mock_soft_masks(primary_frames, prompt_anchor, scan_siat_dir)
    else:
        soft_results = sam2_soft_masks(scan_id, primary_frames, prompt_anchor, scan_siat_dir)

    log(f'Generated {len(soft_results)} soft masks')

    if not soft_results:
        log('FATAL: no soft masks generated — cannot proceed')
        sys.exit(1)

    # ── Step 5: Write output artifacts ───────────────────────────────────────
    out_paths = write_siat_outputs(
        scan_id, soft_results, blur_scores_map, len(all_frames), scan_siat_dir
    )
    log(f'SIAT outputs written to {scan_siat_dir}')

    # Collect paths for server record
    first_frame_id = soft_results[0][0] if soft_results else 0
    siat_ver = SIAT_OUTPUT_VERSION
    base_url = f'/uploads/masks/{scan_id}/siat/{siat_ver}'
    paths_for_server = {
        'alpha_soft_path': f'{base_url}/alpha_soft_{first_frame_id:06d}.png',
        'core_mask_path': f'{base_url}/core_mask_{first_frame_id:06d}.png',
        'hard_mask_path': f'{base_url}/hard_mask_{first_frame_id:06d}.png',
        'boundary_conf_path': f'{base_url}/boundary_conf_{first_frame_id:06d}.png',
        # SI-1 required: rigid core (intersection) + pose-safe support (union minus edges)
        # Only include if actually computed (all_core_masks was non-empty)
        **({} if '_static_rigid_core_path' not in out_paths else {'static_rigid_core_path': f'{base_url}/static_rigid_core.png'}),
        'pose_safe_support_mask_path': f'{base_url}/pose_safe_support.png',
    }

    # ── Step 6: Post SIAT outputs to server ─────────────────────────────────
    r3 = requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/siat-output',
        headers=internal_headers(),
        json={
            'output_version': SIAT_OUTPUT_VERSION,
            **paths_for_server,
            'ambiguity_tags': json.loads(Path(out_paths['_ambiguity_tags']).read_text()),
            'occlusion_labels': json.loads(Path(out_paths['_occlusion_labels']).read_text()),
            'primary_tier_count': len(primary_tier),
            'candidate_tier_count': len(candidate_tier),
        },
        timeout=30
    )
    r3.raise_for_status()
    siat_id = r3.json().get('id')
    log(f'SIAT record saved: id={siat_id}')

    # ── Step 7: Transition to REG ───────────────────────────────────────────
    requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/status',
        headers=internal_headers(),
        json={
            'status': 'REG',
            'message': f'SIAT complete: {len(soft_results)} masks, {siat_id}'
        },
        timeout=10
    ).raise_for_status()

    log(f'SIAT complete for scan {scan_id} → REG')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python siat_worker.py <scan_id>')
        sys.exit(1)
    run(sys.argv[1])
