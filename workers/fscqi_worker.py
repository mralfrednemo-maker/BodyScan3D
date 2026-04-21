"""
fscqi_worker.py — Phase 2 worker: Full Signal Quality + Coverage Index.

Called by the scan pipeline after FRAME_QA (FRAME_QA → FSCQI).
Accepts scan_id as a command-line argument.

Outputs:
  - fscqi_bundles row with six artifacts:
    1. curated_primary_tier     (frame_ids of best quality frames)
    2. extended_candidate_tier  (borderline quality frames)
    3. coverage_descriptor      (overall + per-region spatial coverage)
    4. weak_region_register     (regions with quality problems)
    5. capture_health_summary  (overall signal quality index)
    6. processability_verdict   (PROCESS_CLEAN | PROCESS_WITH_FLAGS | REVIEW_NEEDED | RETRY_RECOMMENDED)

Verdict routing:
  PROCESS_CLEAN     → transitions to SIAT (normal path)
  PROCESS_WITH_FLAGS → transitions to SIAT (flagged, downstream aware)
  REVIEW_NEEDED     → transitions to OPERATOR_REVIEW (blocks pipeline)
  RETRY_RECOMMENDED → transitions to CAPTURING (operator → new capture)

Usage:
    python fscqi_worker.py <scan_id>
"""
import sys
import os
import json
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


# ─── Configuration ────────────────────────────────────────────────────────────

# Quality thresholds
BLUR_EXCELLENT = BLUR_THRESHOLD * 2.5   # top tier
BLUR_GOOD      = BLUR_THRESHOLD          # candidate tier
COVERAGE_THRESHOLD = 0.60               # minimum spatial coverage fraction
WEAK_REGION_SEVERITY = 0.40             # blur < this → severe weak region

# Spatial region grid for coverage (body part coverage grid)
COVERAGE_GRID_ROWS = 4
COVERAGE_GRID_COLS = 4

# ─── Logging ─────────────────────────────────────────────────────────────────

def log(msg):
    print(f'[fscqi] {msg}', flush=True)


# ─── Per-frame quality analysis ──────────────────────────────────────────────

def analyze_frame_quality(filepath):
    """Compute blur score, coverage-relevant signals for a single frame."""
    if HAS_CV2:
        img = cv2.imread(filepath)
        if img is None:
            return {'blur_score': 0.0, 'brightness': 0.0, 'edge_density': 0.0}
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(np.mean(gray))
        edges = cv2.Canny(gray, 50, 150)
        edge_density = float(np.count_nonzero(edges) / edges.size)
        return {'blur_score': blur_score, 'brightness': brightness, 'edge_density': edge_density}
    elif HAS_PIL:
        img = Image.open(filepath).convert('RGB')
        arr = np.array(img, dtype=np.float64)
        gray = np.mean(arr, axis=2)
        blur_score = float(np.var(gray))  # simplified — PIL lacks Laplacian
        brightness = float(np.mean(gray))
        edge_density = 0.0  # fallback
        return {'blur_score': blur_score, 'brightness': brightness, 'edge_density': edge_density}
    else:
        return {'blur_score': BLUR_THRESHOLD + 1.0, 'brightness': 128.0, 'edge_density': 0.1}


def estimate_spatial_coverage(frames):
    """
    Estimate which spatial regions of the subject are covered by which frames.
    Uses image moment-based centroid + spread as a rough proxy for coverage zone.

    Returns a dict: {frame_id: {'centroid_row': 0-1, 'centroid_col': 0-1, 'spread': 0-1}}
    where row/col are grid positions in the coverage grid.
    """
    coverage = {}
    for frame in frames:
        filepath = os.path.join(FRAMES_DIR, os.path.basename(frame['frameUrl']))
        if not os.path.exists(filepath):
            continue
        try:
            if HAS_CV2:
                img = cv2.imread(filepath)
                if img is None:
                    continue
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                h, w = gray.shape
                # Weighted centroid
                y_coords, x_coords = np.where(gray > np.percentile(gray, 30))
                if len(x_coords) == 0:
                    continue
                cx = np.average(x_coords)
                cy = np.average(y_coords)
                # Spread (std of coordinates)
                sx = np.std(x_coords) / w
                sy = np.std(y_coords) / h
                coverage[frame['id']] = {
                    'centroid_col': cx / w,
                    'centroid_row': cy / h,
                    'spread_x': sx,
                    'spread_y': sy
                }
            else:
                # Fallback: assume centered, uniform coverage
                coverage[frame['id']] = {
                    'centroid_col': 0.5,
                    'centroid_row': 0.5,
                    'spread_x': 0.4,
                    'spread_y': 0.4
                }
        except Exception as e:
            log(f'Coverage estimate error for frame {frame["id"]}: {e}')
            continue
    return coverage


def compute_coverage_descriptor(coverage_data, grid_rows=COVERAGE_GRID_ROWS, grid_cols=COVERAGE_GRID_COLS):
    """
    Compute dual-level coverage descriptor:
    - overall: fraction of grid cells covered by at least one frame
    - per_region: {f'{r}_{c}': frame_count} per grid cell
    """
    grid = [[0 for _ in range(grid_cols)] for _ in range(grid_rows)]
    for frame_id, cd in coverage_data.items():
        col = min(grid_cols - 1, int(cd['centroid_col'] * grid_cols))
        row = min(grid_rows - 1, int(cd['centroid_row'] * grid_rows))
        # Add spread coverage to adjacent cells
        spread = max(1, int(cd.get('spread_x', 0.3) * grid_cols))
        for dc in range(-spread, spread + 1):
            for dr in range(-spread, spread + 1):
                r, c = row + dr, col + dc
                if 0 <= r < grid_rows and 0 <= c < grid_cols:
                    grid[r][c] += 1

    covered_cells = sum(1 for r in range(grid_rows) for c in range(grid_cols) if grid[r][c] > 0)
    total_cells = grid_rows * grid_cols
    overall = covered_cells / total_cells

    per_region = {}
    for r in range(grid_rows):
        for c in range(grid_cols):
            per_region[f'{r}_{c}'] = grid[r][c]

    return {
        'overall_coverage': overall,
        'per_region': per_region,
        'grid_dimensions': {'rows': grid_rows, 'cols': grid_cols},
        'frame_count': len(coverage_data)
    }


def classify_weak_regions(frames, blur_scores, coverage_data, severity_threshold=WEAK_REGION_SEVERITY):
    """
    Identify weak regions: frames or coverage zones with quality concerns.

    Returns list of {frame_id, reason, severity, coverage_zone}.
    """
    weak = []
    # Sort frames by blur score
    sorted_frames = sorted(blur_scores, key=lambda x: x['blur_score'])

    # Flag lowest-quartile frames as weak
    cutoff = int(len(sorted_frames) * 0.25)
    for item in sorted_frames[:max(1, cutoff)]:
        if item['blur_score'] < severity_threshold * BLUR_EXCELLENT:
            frame_id = item['frame_id']
            cd = coverage_data.get(frame_id, {})
            weak.append({
                'frame_id': frame_id,
                'reason': 'blur_below_threshold',
                'severity': 'severe' if item['blur_score'] < severity_threshold * BLUR_EXCELLENT * 0.5 else 'moderate',
                'blur_score': item['blur_score'],
                'coverage_zone': {
                    'centroid_row': cd.get('centroid_row'),
                    'centroid_col': cd.get('centroid_col')
                }
            })

    # Flag coverage gaps
    cov_descriptor = compute_coverage_descriptor(coverage_data)
    for region_key, count in cov_descriptor['per_region'].items():
        if count == 0:
            row, col = region_key.split('_')
            weak.append({
                'frame_id': None,
                'reason': 'coverage_gap',
                'severity': 'moderate',
                'coverage_zone': {'grid_row': int(row), 'grid_col': int(col)}
            })

    return weak


def compute_health_summary(blur_scores, coverage_data, weak_regions, coverage_descriptor):
    """Compute overall capture health score 0-100 and flag list."""
    if not blur_scores:
        return {'overall_score': 0, 'flags': ['no_frames']}

    avg_blur = np.mean([f['blur_score'] for f in blur_scores])
    max_blur = max(f['blur_score'] for f in blur_scores)
    cov_score = coverage_descriptor['overall_coverage']

    # Normalize blur to 0-50 scale, coverage to 0-50 scale
    blur_normalized = min(50, (avg_blur / BLUR_EXCELLENT) * 50) if BLUR_EXCELLENT > 0 else 0
    coverage_normalized = cov_score * 50

    overall_score = round(blur_normalized + coverage_normalized, 1)

    flags = []
    if len(weak_regions) > 0:
        severe_count = sum(1 for w in weak_regions if w['severity'] == 'severe')
        moderate_count = len(weak_regions) - severe_count
        if severe_count > 0:
            flags.append(f'{severe_count}_severe_weak_regions')
        if moderate_count > 0:
            flags.append(f'{moderate_count}_moderate_weak_regions')
    if coverage_descriptor['overall_coverage'] < COVERAGE_THRESHOLD:
        flags.append('coverage_below_threshold')
    if avg_blur < BLUR_GOOD:
        flags.append('avg_blur_below_threshold')
    if overall_score < 50:
        flags.append('low_overall_health')

    return {
        'overall_score': overall_score,
        'avg_blur_score': round(avg_blur, 2),
        'max_blur_score': round(max_blur, 2),
        'coverage_score': round(cov_score, 3),
        'total_frames': len(blur_scores),
        'weak_region_count': len(weak_regions),
        'flags': flags
    }


def compute_verdict(health_summary, weak_regions, coverage_descriptor):
    """
    Four-state processability verdict per FS-3.

    PROCESS_CLEAN       — high health, no flags, coverage OK
    PROCESS_WITH_FLAGS  — some flags but pipeline can proceed
    REVIEW_NEEDED      — significant concerns, operator must decide
    RETRY_RECOMMENDED  — fundamental quality problems
    """
    score = health_summary['overall_score']
    flags = health_summary['flags']
    has_severe = any('severe' in f for f in flags)
    coverage_ok = coverage_descriptor['overall_coverage'] >= COVERAGE_THRESHOLD
    frame_count_ok = health_summary['total_frames'] >= MIN_FRAMES_REQUIRED

    if has_severe or (not coverage_ok and not frame_count_ok):
        return 'RETRY_RECOMMENDED'
    elif score < 40 or ('low_overall_health' in flags and score < 60):
        return 'RETRY_RECOMMENDED'
    elif score < 60 or len(weak_regions) > len(health_summary.get('flags', [])) * 2:
        return 'REVIEW_NEEDED'
    elif len(flags) > 0 or not coverage_ok:
        return 'PROCESS_WITH_FLAGS'
    else:
        return 'PROCESS_CLEAN'


# ─── Tier curation ───────────────────────────────────────────────────────────

def curate_tiers(blur_scores, coverage_data):
    """
    Curate primary and candidate tiers.
    Primary tier: top-quality frames (blur >= BLUR_EXCELLENT)
    Candidate tier: borderline frames (BLUR_GOOD <= blur < BLUR_EXCELLENT)
    """
    primary = [f['frame_id'] for f in blur_scores if f['blur_score'] >= BLUR_EXCELLENT]
    borderline = [
        f['frame_id'] for f in blur_scores
        if BLUR_GOOD <= f['blur_score'] < BLUR_EXCELLENT
    ]
    return primary, borderline


def build_raw_reference_map(frames, blur_scores, coverage_data):
    """
    Build raw_reference_map: frame_id → raw frame artifactRef for lineage traceability.
    FS-1 required artifact: maps each analyzed frame to its raw content hash + signals.
    """
    ref_map = {}
    blur_dict = {f['frame_id']: f for f in blur_scores}
    for frame in frames:
        fid = frame['id']
        qs = blur_dict.get(fid, {})
        cd = coverage_data.get(fid, {})
        ref_map[str(fid)] = {
            'frame_id': fid,
            'frame_url': frame.get('frameUrl', ''),
            'content_hash': frame.get('content_hash', ''),
            'blur_score': qs.get('blur_score'),
            'brightness': qs.get('brightness'),
            'edge_density': qs.get('edge_density'),
            'coverage_centroid': {
                'col': cd.get('centroid_col'),
                'row': cd.get('centroid_row'),
            },
            'coverage_spread': {
                'x': cd.get('spread_x'),
                'y': cd.get('spread_y'),
            }
        }
    return ref_map


# ─── Main FSCQI run ──────────────────────────────────────────────────────────

def run(scan_id):
    log(f'Starting FSCQI for scan {scan_id}')

    # ── Step 1: Fetch frame list from server ──────────────────────────────────
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

    # ── Step 2: Per-frame quality analysis ────────────────────────────────────
    blur_scores = []
    for frame in frames:
        filepath = os.path.join(FRAMES_DIR, os.path.basename(frame['frameUrl']))
        if not os.path.exists(filepath):
            log(f'  WARNING: frame file not found: {filepath}')
            continue
        result = analyze_frame_quality(filepath)
        blur_scores.append({
            'frame_id': frame['id'],
            'blur_score': result['blur_score'],
            'brightness': result['brightness'],
            'edge_density': result['edge_density']
        })
        log(f'  frame {frame["id"]}: blur={result["blur_score"]:.1f} bright={result["brightness"]:.1f}')

    if not blur_scores:
        log('FATAL: no frames could be analyzed')
        sys.exit(1)

    # Sort by blur score descending
    blur_scores.sort(key=lambda x: x['blur_score'], reverse=True)

    # ── Step 3: Spatial coverage estimation ────────────────────────────────────
    coverage_data = estimate_spatial_coverage(frames)
    coverage_descriptor = compute_coverage_descriptor(coverage_data)
    log(f'  Coverage: overall={coverage_descriptor["overall_coverage"]:.3f}, '
        f'frames_analyzed={coverage_descriptor["frame_count"]}')

    # ── Step 4: Weak region identification ─────────────────────────────────────
    weak_regions = classify_weak_regions(frames, blur_scores, coverage_data)
    log(f'  Weak regions: {len(weak_regions)} identified '
        f'({sum(1 for w in weak_regions if w["severity"]=="severe")} severe)')

    # ── Step 5: Tier curation ─────────────────────────────────────────────────
    primary_tier, candidate_tier = curate_tiers(blur_scores, coverage_data)
    log(f'  Primary tier: {len(primary_tier)} frames, '
        f'candidate tier: {len(candidate_tier)} frames')

    # ── Step 5b: Raw reference map (FS-1 required artifact) ───────────────────
    raw_reference_map = build_raw_reference_map(frames, blur_scores, coverage_data)
    log(f'  Raw reference map: {len(raw_reference_map)} frames mapped')

    # ── Step 6: Health summary ───────────────────────────────────────────────
    health_summary = compute_health_summary(blur_scores, coverage_data, weak_regions, coverage_descriptor)
    log(f'  Health score: {health_summary["overall_score"]}/100, flags={health_summary["flags"]}')

    # ── Step 7: Verdict ─────────────────────────────────────────────────────
    verdict = compute_verdict(health_summary, weak_regions, coverage_descriptor)
    log(f'  Verdict: {verdict}')

    # ── Step 8: Post fscqi_bundles row to server ───────────────────────────
    bundle_payload = {
        'scan_id': scan_id,
        'bundle_version': '1.0.0',
        'verdict': verdict,
        'primary_tier': primary_tier,
        'candidate_tier': candidate_tier,
        'raw_reference_map': raw_reference_map,
        'coverage_descriptor': coverage_descriptor,
        'weak_regions': weak_regions,
        'health_summary': health_summary
    }

    r2 = requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/fscqi-bundle',
        headers=internal_headers(),
        json=bundle_payload,
        timeout=30
    )
    r2.raise_for_status()
    bundle_id = r2.json().get('id')
    log(f'  FSCQI bundle saved: id={bundle_id}')

    # ── Step 9: Route based on verdict ───────────────────────────────────────
    if verdict in ('PROCESS_CLEAN', 'PROCESS_WITH_FLAGS'):
        next_status = 'SIAT'
        log(f'  → FSCQI verdict={verdict} → transitioning to {next_status}')
    elif verdict == 'REVIEW_NEEDED':
        next_status = 'OPERATOR_REVIEW'
        log(f'  → FSCQI verdict=REVIEW_NEEDED → transitioning to OPERATOR_REVIEW (pipeline blocked)')
    else:  # RETRY_RECOMMENDED
        next_status = 'CAPTURING'
        log(f'  → FSCQI verdict=RETRY_RECOMMENDED → operator must initiate new capture')

    requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/status',
        headers=internal_headers(),
        json={
            'status': next_status,
            'message': f'FSCQI complete: verdict={verdict}, health={health_summary["overall_score"]}/100',
            'fscqi_bundle_id': bundle_id
        },
        timeout=10
    ).raise_for_status()

    log(f'FSCQI complete for scan {scan_id} — verdict={verdict}, bundle_id={bundle_id}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python fscqi_worker.py <scan_id>')
        sys.exit(1)
    run(sys.argv[1])
