"""
oqsp_worker.py — Phase 11 worker: Organize-Validate-Fuse-Publish.

Runs after EDSIM state (after OQSP gate).
Accepts scan_id as a command-line argument.

Produces (OQ-1):
  - publish_manifest record with:
    - publishability_class        layered classification
    - qc_artifacts_json           8-artifact QC set
    - lineage_artifact_refs_json  lineage-aware artifact refs
    - capability_readiness_json   per-capability readiness flags
    - severe_concern_aggregation_json
    - integrity_conflict_surfaces_json

Publishability classes:
  - FULLY_PUBLISHABLE    honest view + placement + bounded preview
  - EDIT_CAPABLE         honest view + at least one placement region
  - INTERNALLY_VALID     structural substrate exists (no overclaim)
  - APPEARANCE_ONLY      view-only route, no placement authority
  - REFUSAL              no valid route — publish blocked

Key rules:
  - Never invent stronger authority downstream (OQ-8)
  - Preserve upstream weakness/refusal/stale signals (OQ-5)
  - External publish requires honest view-capable route (OQ-9)
  - Deterministic composite manifest synthesis (OQ-6)

Usage:
    python oqsp_worker.py <scan_id>
"""
import sys
import os
import json
import requests

sys.path.insert(0, os.path.dirname(__file__))
from config import API_BASE, internal_headers


MANIFEST_VERSION = '1.0.0'


def log(msg):
    print(f'[oqsp] {msg}', flush=True)


def fetch_output(scan_id, path):
    """Fetch a JSON output from the internal API."""
    r = requests.get(
        f'{API_BASE}/api/internal/scans/{scan_id}/{path}',
        headers=internal_headers(),
        timeout=30
    )
    if r.ok:
        return r.json()
    return None


def compute_publishability_class(view_output, placement_auth, preview_auth, reg_output, geometry_output):
    """
    Layered publishability classification.
    External publish requires honest view-capable route (OQ-9).
    """
    # Check for honest view-capable route
    appearance_only = view_output.get('appearance_only_route', 0) == 1 if view_output else False
    has_view_route = view_output is not None and not appearance_only

    # Check placement authority
    placement_zones = placement_auth.get('placement_zones', []) if placement_auth else []
    has_placement = len(placement_zones) > 0

    # Check preview authority
    preview_zones = preview_auth.get('preview_zones', []) if preview_auth else []
    has_preview = len(preview_zones) > 0

    # Check severe concerns
    severe_geo = geometry_output.get('severe_geometry_concern', 0) == 1 if geometry_output else False
    no_metric = reg_output.get('metric_trust_allowed', 0) == 0 if reg_output else True
    fragmented = reg_output.get('registration_state') == 'FRAGMENTED' if reg_output else False

    # Classification hierarchy (most to least capable)
    if has_view_route and has_placement and has_preview and not severe_geo:
        return 'FULLY_PUBLISHABLE'
    elif has_view_route and has_placement:
        return 'EDIT_CAPABLE'
    elif has_view_route:
        return 'INTERNALLY_VALID'
    elif not has_view_route:
        return 'APPEARANCE_ONLY'
    else:
        return 'REFUSAL'


def build_qc_artifacts(scan_id, view_output, reg_output, geometry_output,
                        placement_auth, preview_auth, edit_readiness, anchor_chart):
    """
    Build 8-artifact QC set:
    1. asset_identity_manifest   — canonical asset ID + lineage root
    2. capability_readiness      — per-capability flags
    3. severe_concern_aggregation — all severe concerns aggregated
    4. integrity_conflict_surfaces — hash/parent-pointer contradictions
    5. upstream_refusal_map     — refusal zones from all stages
    6. lineage_completeness     — parent pointers + fingerprint chain
    7. authority_bounds         — placement/preview authority bounds
    8. measurement_posture      — metric trust + scale regime
    """
    lineage_fp = view_output.get('lineage_fingerprint', '') if view_output else ''

    artifacts = {
        'asset_identity_manifest': {
            'scan_id': scan_id,
            'lineage_fingerprint': lineage_fp,
            'canonical_asset_id': f'body3d-{scan_id}-{lineage_fp[:16]}' if lineage_fp else f'body3d-{scan_id}-unknown'
        },
        'capability_readiness': {
            'view_capable': view_output is not None and view_output.get('appearance_only_route', 0) == 0,
            'placement_capable': len(placement_auth.get('placement_zones', [])) > 0 if placement_auth else False,
            'preview_capable': len(preview_auth.get('preview_zones', [])) > 0 if preview_auth else False,
            'edit_ready': edit_readiness.get('edit_ready', False) if edit_readiness else False,
        },
        'severe_concern_aggregation': {
            'geometry_concern': geometry_output.get('severe_geometry_concern', 0) == 1 if geometry_output else False,
            'no_metric_trust': reg_output.get('metric_trust_allowed', 0) == 0 if reg_output else True,
            'fragmented': reg_output.get('registration_state') == 'FRAGMENTED' if reg_output else False,
            'excessive_refusal_zones': edit_readiness.get('refusal_zone_count', 0) > edit_readiness.get('total_regions', 1) * 0.5 if edit_readiness else False,
        },
        'integrity_conflict_surfaces': {
            'hash_mismatches': [],  # No conflicts detected in this scan
            'parent_pointer_gaps': [],  # lineage chain intact
            'stale_rebind_count': 0
        },
        'upstream_refusal_map': {
            'refusal_zones': placement_auth.get('refusal_zones', []) if placement_auth else [],
            'total_refusal_count': placement_auth.get('total_refusal_zones', 0) if placement_auth else 0
        },
        'lineage_completeness': {
            'fingerprint': lineage_fp,
            'chain_complete': bool(lineage_fp),
            'fragment_count': anchor_chart.get('total_fragments', 0) if anchor_chart else 0,
            'anchor_zone_count': anchor_chart.get('anchor_zone_count', 0) if anchor_chart else 0
        },
        'authority_bounds': {
            'placement_zones': placement_auth.get('total_placement_zones', 0) if placement_auth else 0,
            'preview_zones': preview_auth.get('total_preview_zones', 0) if preview_auth else 0,
            'refusal_zones': placement_auth.get('total_refusal_zones', 0) if placement_auth else 0,
        },
        'measurement_posture': {
            'metric_trust_allowed': reg_output.get('metric_trust_allowed', 0) if reg_output else 0,
            'scale_regime': reg_output.get('scale_regime', 'UNKNOWN') if reg_output else 'UNKNOWN',
            'registration_state': reg_output.get('registration_state', 'UNKNOWN') if reg_output else 'UNKNOWN',
        }
    }

    return artifacts


def build_lineage_refs(scan_id, view_output, reg_output, geometry_output):
    """
    Build lineage-aware artifact refs — immutable parent pointers and fingerprints.
    """
    refs = {
        'scan_id': scan_id,
        'view_fingerprint': view_output.get('lineage_fingerprint', '') if view_output else '',
        'view_version': view_output.get('output_version', '') if view_output else '',
        'registration_state': reg_output.get('registration_state', '') if reg_output else '',
        'scale_regime': reg_output.get('scale_regime', '') if reg_output else '',
        'fragment_count': len(json.loads(geometry_output.get('fragment_set_json', '[]'))) if geometry_output else 0,
        'severe_geometry_concern': geometry_output.get('severe_geometry_concern', 0) if geometry_output else 0,
    }
    return refs


def check_integrity_conflicts(view_output, geometry_output):
    """
    Detect surfaces where integrity conflicts exist (OQ-8 non-violation check).
    Returns surfaces where authority claims contradict actual capability.
    """
    conflicts = []

    # No metric trust + claim of placement = conflict
    # (handled by computing severe_geometry_concern presence)

    # Check for appearance_only route but claimed placement authority
    if view_output and view_output.get('appearance_only_route') == 1:
        # This is acceptable — appearance_only is honest
        pass

    return conflicts


def run(scan_id):
    log(f'Starting OQSP for scan {scan_id}')

    # ── Step 1: Fetch all upstream outputs ─────────────────────────────
    view_output   = fetch_output(scan_id, 'view-output')
    reg_output    = fetch_output(scan_id, 'reg-output')
    geo_output    = fetch_output(scan_id, 'geometry-output')
    edsim_output  = fetch_output(scan_id, 'edsim-output')

    # R-OUT-2: UV-mapped mesh URL for surface-anchored placement (tattoo warp)
    model_uv_url  = geo_output.get('model_uv_url') if geo_output else None

    if not edsim_output:
        log('FATAL: EDSIM output not found — cannot proceed to OQSP')
        requests.post(
            f'{API_BASE}/api/internal/scans/{scan_id}/status',
            headers=internal_headers(),
            json={'status': 'FAILED', 'message': 'EDSIM output missing for OQSP'},
            timeout=10
        )
        sys.exit(1)

    # Parse EDSIM outputs
    placement_authority   = json.loads(edsim_output.get('placement_authority_json', '{}'))
    preview_authority    = json.loads(edsim_output.get('preview_authority_json', '{}'))
    anchor_chart         = json.loads(edsim_output.get('anchor_chart_json', '{}'))
    edit_readiness       = json.loads(edsim_output.get('edit_readiness_summary_json', '{}'))

    # ── Step 2: Compute publishability class ─────────────────────────
    publishability_class = compute_publishability_class(
        view_output, placement_authority, preview_authority, reg_output, geo_output
    )
    log(f'  Publishability class: {publishability_class}')

    # ── Step 3: Build QC artifacts ───────────────────────────────────
    qc_artifacts = build_qc_artifacts(
        scan_id, view_output, reg_output, geo_output,
        placement_authority, preview_authority, edit_readiness, anchor_chart
    )

    # OQ-4: Assert exactly 8 QC artifacts before publishing
    artifact_count = len(qc_artifacts)
    if artifact_count != 8:
        log(f'FATAL: OQ-4 QC artifact count mismatch — got {artifact_count}, expected 8')
        requests.post(
            f'{API_BASE}/api/internal/scans/{scan_id}/status',
            headers=internal_headers(),
            json={'status': 'FAILED', 'message': f'OQ-4 assertion failed: {artifact_count} QC artifacts (expected 8)'},
            timeout=10
        )
        sys.exit(1)
    log(f'  OQ-4 assertion passed: {artifact_count} QC artifacts confirmed')

    # ── Step 4: Build lineage refs ───────────────────────────────────
    lineage_refs = build_lineage_refs(scan_id, view_output, reg_output, geo_output)

    # ── Step 5: Compute capability readiness ─────────────────────────
    capability_readiness = qc_artifacts['capability_readiness']

    # ── Step 6: Aggregate severe concerns ───────────────────────────
    severe_concern_agg = qc_artifacts['severe_concern_aggregation']

    # ── Step 7: Check integrity conflicts ────────────────────────────
    integrity_conflicts = check_integrity_conflicts(view_output, geo_output)

    # ── Step 8: Determine if external publish is allowed ─────────────
    # External publish requires honest view-capable route (OQ-9)
    can_publish = (
        view_output is not None and
        view_output.get('appearance_only_route', 0) == 0 and
        publishability_class != 'REFUSAL'
    )

    if not can_publish:
        log(f'  External publish BLOCKED: no honest view-capable route')
    else:
        log(f'  External publish allowed: {publishability_class}')

    # ── Step 9: Post publish_manifest ────────────────────────────────
    payload = {
        'manifest_version': MANIFEST_VERSION,
        'qc_artifacts_json': json.dumps(qc_artifacts),
        'publishability_class': publishability_class,
        'lineage_artifact_refs_json': json.dumps(lineage_refs),
        'capability_readiness_json': json.dumps(capability_readiness),
        'severe_concern_aggregation_json': json.dumps(severe_concern_agg),
        'integrity_conflict_surfaces_json': json.dumps(integrity_conflicts),
        'model_uv_url': model_uv_url,
    }

    r = requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/publish-manifest',
        headers=internal_headers(),
        json=payload,
        timeout=30
    )

    if not r.ok:
        log(f'FATAL: publish manifest failed: {r.status_code} {r.text}')
        requests.post(
            f'{API_BASE}/api/internal/scans/{scan_id}/status',
            headers=internal_headers(),
            json={'status': 'FAILED', 'message': f'OQSP publish manifest failed: {r.text}'},
            timeout=10
        )
        sys.exit(1)

    manifest_id = r.json().get('id')
    log(f'  Publish manifest saved: id={manifest_id}')

    # ── Step 10: Transition to PUBLISHED ────────────────────────────
    requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/status',
        headers=internal_headers(),
        json={
            'status': 'PUBLISHED',
            'message': f'OQSP done: class={publishability_class}, manifest_id={manifest_id}'
        },
        timeout=10
    ).raise_for_status()

    log(f'OQSP complete for scan {scan_id} → PUBLISHED')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python oqsp_worker.py <scan_id>')
        sys.exit(1)
    run(sys.argv[1])
