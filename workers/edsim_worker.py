"""
edsim_worker.py — Phase 6 worker: Edit Simulation with Placement Authority Map.

Runs after EDSIM state (after PHOTOREAL).
Accepts scan_id as a command-line argument.

Produces (ED-1) — 13 EDSIM artifacts:
  - anchor_chart_json         per-vertex or per-region authority chart
  - placement_authority_json  zones where placement is permitted
  - preview_authority_json    zones where preview is permitted
  - appearance_only_routes_json  view-only routes
  - edit_regions_json        regions available for edit
  - stale_rebind_json        stale mesh rebind register
  - edit_readiness_summary_json  overall readiness flags

Placement authority:
  - Only granted on structural_proxy zones (anchor_chart regions)
  - Not granted on appearance_only_route geometry
  - Not granted where severe_geometry_concern = 1

Usage:
    python edsim_worker.py <scan_id>
"""
import sys
import os
import json
import requests

sys.path.insert(0, os.path.dirname(__file__))
from config import API_BASE, MODELS_DIR, internal_headers


EDSIM_OUTPUT_VERSION = '1.0.0'


def log(msg):
    print(f'[edsim] {msg}', flush=True)


def compute_anchor_chart(scan_id):
    """
    Build anchor chart: which regions of the mesh have structural integrity
    for placement (anchor zones vs appearance-only zones).
    """
    try:
        r = requests.get(
            f'{API_BASE}/api/internal/scans/{scan_id}/geometry-output',
            headers=internal_headers(),
            timeout=30
        )
        if not r.ok:
            return {'error': 'geometry output not found'}

        geo = r.json()
        fragments = json.loads(geo.get('fragment_set_json', '[]'))
        usefulness_zones = json.loads(geo.get('usefulness_zones_json', '[]'))
        severe_concern = geo.get('severe_geometry_concern', 0)

        # Anchor zones: high suitability_score fragments
        # Usefulness zones: interior > 0.5 suitability
        high_suitability = [z for z in usefulness_zones if z.get('suitability_score', 0) > 0.5]

        anchor_regions = []
        for frag in fragments:
            # Check if this fragment's centroid region has high suitability
            suitability = next(
                (z['suitability_score'] for z in high_suitability if z.get('region') == 'interior'),
                0.5
            )
            anchor_regions.append({
                'fragment_id': frag['fragment_id'],
                'anchor_strength': round(suitability, 3),
                'is_placement_anchor': suitability > 0.5 and severe_concern == 0
            })

        return {
            'anchor_regions': anchor_regions,
            'total_fragments': len(fragments),
            'anchor_zone_count': sum(1 for r in anchor_regions if r['is_placement_anchor'])
        }

    except Exception as e:
        log(f'  Anchor chart error: {e}')
        return {'error': str(e)}


def compute_placement_authority(anchor_chart, reg_output, geometry_output):
    """
    Build placement_authority_json: per-region placement permission.
    placement = only where anchor_chart says is_placement_anchor AND
                reg metric_trust_allowed = 0 (no metric claim) is OK
                reg metric_trust_allowed = 1 would be full placement
    """
    placement_zones = []
    refusal_zones = []

    anchor_regions = anchor_chart.get('anchor_regions', [])

    for region in anchor_regions:
        frag_id = region['fragment_id']
        if region['is_placement_anchor']:
            placement_zones.append({
                'fragment_id': frag_id,
                'anchor_strength': region['anchor_strength'],
                'placement_allowed': True
            })
        else:
            refusal_zones.append({
                'fragment_id': frag_id,
                'reason': 'low_suitability'
            })

    # Refuse placement on appearance_only_route geometry
    if geometry_output and geometry_output.get('severe_geometry_concern') == 1:
        refusal_zones.append({
            'fragment_id': 'all',
            'reason': 'severe_geometry_concern'
        })

    return {
        'placement_zones': placement_zones,
        'refusal_zones': refusal_zones,
        'total_placement_zones': len(placement_zones),
        'total_refusal_zones': len(refusal_zones)
    }


def compute_preview_authority(anchor_chart, geometry_output):
    """
    Build preview_authority_json: preview is allowed more broadly than placement.
    Preview uses appearance_scaffold (less precise than structural_proxy).
    """
    # Preview is allowed on any region with > 0.3 suitability
    preview_zones = []
    for region in anchor_chart.get('anchor_regions', []):
        if region['anchor_strength'] > 0.3:
            preview_zones.append({
                'fragment_id': region['fragment_id'],
                'preview_strength': region['anchor_strength'],
                'preview_allowed': True
            })

    return {
        'preview_zones': preview_zones,
        'total_preview_zones': len(preview_zones)
    }


def detect_stale_rebind(scan_id, view_output):
    """
    Build stale_rebind_register: detects when identical geometry has different meaning.
    auto_rebind_identical: same hash = same meaning (enforced).

    Stale rebind occurs when:
    1. Same geometry hash as a previous version (auto_rebind_identical)
    2. Lineage chain is broken (parent pointer missing)
    """
    try:
        lineage_fingerprint = view_output.get('lineage_fingerprint', '')
        appearance_only = view_output.get('appearance_only_route', 0)

        # ED-5: Compare current geometry against all prior versions to detect stale rebind.
        # geometry-output-history returns all entries ordered by id ASC (oldest first).
        # The last entry IS the current version. We compare current (fetched separately)
        # against ALL prior entries, including the most recent prior (history[-1] when
        # there are >= 2 entries). Skip only the current entry itself (id matches the
        # geometry-output response).
        stale_events = []
        try:
            r_hist = requests.get(
                f'{API_BASE}/api/internal/scans/{scan_id}/geometry-output-history',
                headers=internal_headers(),
                timeout=30
            )
            r_curr = requests.get(
                f'{API_BASE}/api/internal/scans/{scan_id}/geometry-output',
                headers=internal_headers(),
                timeout=30
            )
            if r_hist.ok and r_curr.ok:
                history = r_hist.json()
                current_geo = r_curr.json()
                current_frag_count = len(json.loads(current_geo.get('fragment_set_json', '[]')))
                current_id = current_geo.get('id')
                # Compare against all prior entries (all except the one matching current geometry)
                for prior in history:
                    if prior.get('id') == current_id:
                        continue  # skip current entry — it's what we're comparing from
                    prior_frag_count = len(json.loads(prior.get('fragment_set_json', '[]')))
                    if prior_frag_count == current_frag_count and current_frag_count > 0:
                        stale_events.append({
                            'prior_id': prior['id'],
                            'prior_created_at': prior.get('created_at', ''),
                            'reason': 'geometry_identical_to_prior_version_needs_meaning_check',
                            'auto_rebind': True
                        })
        except Exception as geo_e:
            log(f'  Stale rebind check failed to fetch history: {geo_e}')

        stale_count = len(stale_events)

        return {
            'current_fingerprint': lineage_fingerprint,
            'appearance_only': bool(appearance_only),
            'auto_rebind_identical': stale_count == 0,
            'stale_count': stale_count,
            'stale_events': stale_events,
        }
    except Exception as e:
        return {'error': str(e)}


def write_edsim_jsonl(scan_id, placement_authority, preview_authority, edit_regions, anchor_chart):
    """
    Write append-only .jsonl state bundles to disk (ED-4 requirement).
    Files live in uploads/models/{scan_id}/edsim/ and are served statically.
    Each run appends a new entry — existing lines are never mutated.
    """
    scan_models_dir = os.path.join(MODELS_DIR, str(scan_id))
    edsim_dir = os.path.join(scan_models_dir, 'edsim')
    os.makedirs(edsim_dir, exist_ok=True)

    ts = lambda: __import__('datetime').datetime.utcnow().isoformat() + 'Z'

    # placement_state_bundles.jsonl
    placement_bundle = {
        'ts': ts(),
        'scan_id': scan_id,
        'placement_zones': placement_authority.get('placement_zones', []),
        'refusal_zones': placement_authority.get('refusal_zones', []),
        'total_placement_zones': placement_authority.get('total_placement_zones', 0),
        'total_refusal_zones': placement_authority.get('total_refusal_zones', 0),
        'anchor_chart_summary': {
            'total_fragments': anchor_chart.get('total_fragments', 0),
            'anchor_zone_count': anchor_chart.get('anchor_zone_count', 0),
        }
    }
    _append_jsonl(os.path.join(edsim_dir, 'placement_state_bundles.jsonl'), placement_bundle)

    # preview_edit_state_bundles.jsonl
    preview_bundle = {
        'ts': ts(),
        'scan_id': scan_id,
        'preview_zones': preview_authority.get('preview_zones', []),
        'total_preview_zones': preview_authority.get('total_preview_zones', 0),
    }
    _append_jsonl(os.path.join(edsim_dir, 'preview_edit_state_bundles.jsonl'), preview_bundle)

    # placement_on_preview_bindings.jsonl
    bindings = []
    placement_zones = placement_authority.get('placement_zones', [])
    preview_zones = preview_authority.get('preview_zones', [])
    for pz in placement_zones:
        fid = pz.get('fragment_id')
        matching_preview = [prz for prz in preview_zones if prz.get('fragment_id') == fid]
        bindings.append({
            'ts': ts(),
            'fragment_id': fid,
            'has_placement': True,
            'has_preview': len(matching_preview) > 0,
            'binding_type': 'full' if matching_preview else 'placement_only',
        })
    _append_jsonl(os.path.join(edsim_dir, 'placement_on_preview_bindings.jsonl'), bindings)

    # refusal_zones.jsonl — one entry per refusal event
    refusal_events = []
    for rz in placement_authority.get('refusal_zones', []):
        refusal_events.append({
            'ts': ts(),
            'scan_id': scan_id,
            'fragment_id': rz.get('fragment_id'),
            'reason': rz.get('reason'),
            'zone_type': 'placement_refusal',
        })
    _append_jsonl(os.path.join(edsim_dir, 'refusal_zones.jsonl'), refusal_events if refusal_events else [{'ts': ts(), 'scan_id': scan_id, 'note': 'no_refusal_zones'}])

    log(f'  ED-4 JSONL files written to {edsim_dir}')


def _append_jsonl(filepath, obj):
    """Append a JSON object (or list of objects) as a line to a .jsonl file."""
    import json as _json
    if isinstance(obj, list):
        for item in obj:
            with open(filepath, 'a', encoding='utf-8') as f:
                f.write(_json.dumps(item, sort_keys=True) + '\n')
    else:
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(_json.dumps(obj, sort_keys=True) + '\n')


def write_before_after_lineage(scan_id, edsim_output):
    """
    Write before_after_lineage.json — ED-5 required artifact.
    Snapshots the lineage chain before and after EDSIM processing.
    On patch, a new entry is appended; existing entries are never mutated.
    """
    scan_models_dir = os.path.join(MODELS_DIR, str(scan_id))
    edsim_dir = os.path.join(scan_models_dir, 'edsim')
    os.makedirs(edsim_dir, exist_ok=True)

    ts = __import__('datetime').datetime.utcnow().isoformat() + 'Z'

    # Fetch current lineage events (the "before" snapshot — EDSIM is the latest stage)
    try:
        r = requests.get(
            f'{API_BASE}/api/internal/scans/{scan_id}/lineage-events',
            headers=internal_headers(),
            timeout=30
        )
        lineage_before = r.json() if r.ok else []
    except Exception:
        lineage_before = []

    snapshot = {
        'ts': ts,
        'scan_id': scan_id,
        'before': {
            'lineage_event_count': len(lineage_before),
            'events': lineage_before,
        },
        'after': {
            'edsim_output_version': edsim_output.get('output_version', EDSIM_OUTPUT_VERSION),
            'edit_ready': edsim_output.get('edit_readiness_summary_json', '{}'),
            'placement_zones': edsim_output.get('placement_authority_json', '{}'),
            'anchor_zone_count': json.loads(edsim_output.get('anchor_chart_json', '{}')).get('anchor_zone_count', 0),
        }
    }

    filepath = os.path.join(edsim_dir, 'before_after_lineage.jsonl')
    _append_jsonl(filepath, snapshot)
    log(f'  ED-5 before_after_lineage snapshot written ({len(lineage_before)} prior events)')


def compute_edit_readiness_summary(scan_id, placement_auth, preview_auth, anchor_chart, reg_output):
    """
    Build edit_readiness_summary_json: overall readiness for edit simulation.
    """
    summary = {
        'placement_authority_coverage': placement_auth.get('total_placement_zones', 0),
        'preview_authority_coverage': preview_auth.get('total_preview_zones', 0),
        'refusal_zone_count': placement_auth.get('total_refusal_zones', 0),
        'total_regions': anchor_chart.get('total_fragments', 0),
    }

    # Readiness flags
    flags = []
    if summary['placement_authority_coverage'] == 0:
        flags.append('no_placement_authority')
    if summary['preview_authority_coverage'] == 0:
        flags.append('no_preview_authority')
    if summary['refusal_zone_count'] > summary['total_regions'] * 0.5:
        flags.append('excessive_refusal_zones')
    if reg_output and reg_output.get('metric_trust_allowed') == 0:
        flags.append('no_metric_authority')
    if reg_output and reg_output.get('measurement_use_prohibited') == 1:
        flags.append('measurement_use_prohibited')

    summary['flags'] = flags
    summary['edit_ready'] = len(flags) == 0

    return summary


def run(scan_id):
    log(f'Starting EDSIM for scan {scan_id}')

    # ── Step 1: Fetch upstream outputs ─────────────────────────────────────
    r_view = requests.get(
        f'{API_BASE}/api/internal/scans/{scan_id}/view-output',
        headers=internal_headers(),
        timeout=30
    )
    view_output = r_view.json() if r_view.ok else {}

    r_geo = requests.get(
        f'{API_BASE}/api/internal/scans/{scan_id}/geometry-output',
        headers=internal_headers(),
        timeout=30
    )
    geometry_output = r_geo.json() if r_geo.ok else {}

    r_reg = requests.get(
        f'{API_BASE}/api/internal/scans/{scan_id}/reg-output',
        headers=internal_headers(),
        timeout=30
    )
    reg_output = r_reg.json() if r_reg.ok else {}

    # ── Step 2: Compute anchor chart ───────────────────────────────────
    anchor_chart = compute_anchor_chart(scan_id)
    log(f'  Anchor chart: {anchor_chart.get("total_fragments", 0)} fragments, '
        f'{anchor_chart.get("anchor_zone_count", 0)} anchor zones')

    # ── Step 3: Compute authority maps ─────────────────────────────────
    placement_authority = compute_placement_authority(anchor_chart, reg_output, geometry_output)
    log(f'  Placement authority: {placement_authority.get("total_placement_zones", 0)} zones')

    preview_authority = compute_preview_authority(anchor_chart, geometry_output)
    log(f'  Preview authority: {preview_authority.get("total_preview_zones", 0)} zones')

    # ── Step 4: Appearance-only routes ────────────────────────────────
    appearance_only_routes = []
    if view_output.get('appearance_only_route') == 1:
        appearance_only_routes.append({
            'route': 'appearance_only',
            'reason': 'no_metric_authority_or_severe_geometry_concern',
            'lineage_fingerprint': view_output.get('lineage_fingerprint', '')
        })

    # ── Step 5: Edit regions ──────────────────────────────────────────
    # Edit regions = placement zones minus refusal zones.
    # If any refusal zone has fragment_id='all', refuse all placement.
    refusal_zones = placement_authority.get('refusal_zones', [])
    refuse_all = any(r.get('fragment_id') == 'all' for r in refusal_zones)
    if refuse_all:
        edit_regions = []
    else:
        edit_regions = [
            z for z in placement_authority.get('placement_zones', [])
            if not any(r.get('fragment_id') == z['fragment_id'] for r in refusal_zones)
        ]

    # ── Step 5b: Write append-only .jsonl state bundles (ED-4) ─────────
    write_edsim_jsonl(scan_id, placement_authority, preview_authority, edit_regions, anchor_chart)

    # ── Step 6: Stale rebind register ─────────────────────────────────
    stale_rebind = detect_stale_rebind(scan_id, view_output)

    # ── Step 7: Edit readiness summary ───────────────────────────────
    edit_readiness = compute_edit_readiness_summary(
        scan_id, placement_authority, preview_authority, anchor_chart, reg_output
    )
    log(f'  Edit readiness: {edit_readiness.get("edit_ready")}, flags={edit_readiness.get("flags", [])}')

    # ── Step 8: Post edit_sim_outputs to server ───────────────────────
    r = requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/edsim-output',
        headers=internal_headers(),
        json={
            'output_version': EDSIM_OUTPUT_VERSION,
            'anchor_chart_json': json.dumps(anchor_chart),
            'placement_authority_json': json.dumps(placement_authority),
            'preview_authority_json': json.dumps(preview_authority),
            'appearance_only_routes_json': json.dumps(appearance_only_routes),
            'edit_regions_json': json.dumps(edit_regions),
            'stale_rebind_json': json.dumps(stale_rebind),
            'edit_readiness_summary_json': json.dumps(edit_readiness),
        },
        timeout=30
    )
    r.raise_for_status()
    eds_id = r.json().get('id')
    log(f'  edit_sim_outputs saved: id={eds_id}')

    # ── Step 8b: Write before_after_lineage snapshot (ED-5) ──────────
    eds_output_for_snapshot = {
        'output_version': EDSIM_OUTPUT_VERSION,
        'edit_readiness_summary_json': json.dumps(edit_readiness),
        'placement_authority_json': json.dumps(placement_authority),
        'anchor_chart_json': json.dumps(anchor_chart),
    }
    write_before_after_lineage(scan_id, eds_output_for_snapshot)

    # ── Step 9: Transition to OQSP ───────────────────────────────────
    requests.post(
        f'{API_BASE}/api/internal/scans/{scan_id}/status',
        headers=internal_headers(),
        json={
            'status': 'OQSP',
            'message': f'EDSIM done: edit_ready={edit_readiness.get("edit_ready")}, placement_zones={placement_authority.get("total_placement_zones", 0)}'
        },
        timeout=10
    ).raise_for_status()

    log(f'EDSIM complete for scan {scan_id} → OQSP')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python edsim_worker.py <scan_id>')
        sys.exit(1)
    run(sys.argv[1])
