"""
photoreal_worker.py — Phase 5 worker: View-Capable Realization with Lineage Address.

Runs after PHOTOREAL state (after DG/mesh cleanup).
Accepts scan_id as a command-line argument.

Produces (VW-1):
  - view_version          e.g. "1.0.0"
  - view_bundle_path     path to viewing bundle
  - lineage_fingerprint  SHA-256 of all upstream artifact hashes
  - appearance_only_route  0 or 1 (1 if no placement authority)

The existing model.glb is used as the view bundle. The lineage fingerprint
binds this view to the full artifact chain (capture → FSCQI → SIAT → REG → DG).

Usage:
    python photoreal_worker.py <scan_id>
"""
import sys
import os
import json
import requests
import hashlib

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

    # Copy model.glb as the view bundle
    view_bundle_path = os.path.join(view_bundle_dir, 'model.glb')
    import shutil
    shutil.copy2(model_glb, view_bundle_path)

    # ── Step 2: Compute lineage fingerprint ────────────────────────────────
    lineage_fingerprint = compute_lineage_fingerprint(scan_id)
    log(f'  Lineage fingerprint: {lineage_fingerprint[:16]}...')

    # ── Step 3: Check appearance-only route ───────────────────────────────
    appearance_only_route = check_appearance_only(scan_id)
    log(f'  Appearance-only route: {appearance_only_route}')

    view_url = f'/uploads/models/{scan_id}/view/{VIEW_OUTPUT_VERSION}/model.glb'

    # ── Step 4: Post view_outputs to server ──────────────────────────────
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

    # ── Step 5: Transition to EDSIM ───────────────────────────────────
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
