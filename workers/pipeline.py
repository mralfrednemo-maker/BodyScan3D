"""
pipeline.py — Pipeline orchestrator: runs all workers for a given scan_id in sequence.

Watches the scan's pipelineStatus and dispatches the right worker.
Can be run as a one-shot processor (pass scan_id) or as a poller (no args).

Usage:
    python pipeline.py <scan_id>         # process one scan immediately
    python pipeline.py --poll            # poll server every 10s for pending scans
    SAM2_MOCK=1 python pipeline.py 42    # dev mode — no GPU needed
"""
import sys
import os
import time
import requests

sys.path.insert(0, os.path.dirname(__file__))
from config import API_BASE, internal_headers


def log(msg):
    print(f'[pipeline] {msg}', flush=True)


POLL_INTERVAL = 10   # seconds between polls in --poll mode

# States that require a worker to run
WORKER_FOR_STATE = {
    # UPLOADING is NOT here — frames are still arriving; /api/capture/finalize triggers FRAME_QA
    'VIDEO_UPLOADED':  'video_worker',
    'FRAME_QA':        'frame_qa',
    'FSCQI':           'fscqi_worker',   # FS-1: Full Signal Quality + Coverage Index
    'SIAT':            'siat_worker',     # SI-1: Subject Isolation and Targeting
    'RECONSTRUCTING':  'reconstruct_worker',  # pycolmap sparse + dense MVS
    'REG':             'reg_worker',     # RG-1: Honest scale posture metadata
    'POST_PROCESSING': 'mesh_worker',     # DG: Fragment-preserving geometry
    'PHOTOREAL':       'photoreal_worker',  # VW-1: View-capable realization
    'EDSIM':           'edsim_worker',       # ED-1: Edit simulation with placement authority
    'OQSP':            'oqsp_worker',        # OQ-1: Organize-validate-fuse-publish
};


def dispatch(scan_id, state):
    worker_name = WORKER_FOR_STATE.get(state)
    if not worker_name:
        log(f'Scan {scan_id}: no worker for state {state}')
        return

    log(f'Scan {scan_id}: state={state} -> running {worker_name}')

    module_path = os.path.join(os.path.dirname(__file__), f'{worker_name}.py')
    # Run via subprocess to isolate crashes
    import subprocess
    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, module_path, str(scan_id)],
        env=env, capture_output=False
    )
    if result.returncode != 0:
        log(f'Scan {scan_id}: {worker_name} exited {result.returncode}')
    else:
        log(f'Scan {scan_id}: {worker_name} finished OK')


def process_scan(scan_id):
    r = requests.get(
        f'{API_BASE}/api/internal/scans/{scan_id}/status',
        headers=internal_headers(),
        timeout=10
    )
    if not r.ok:
        log(f'Could not fetch scan {scan_id}: {r.status_code}')
        return
    data = r.json()
    state = data.get('pipelineStatus') or data.get('status')
    dispatch(scan_id, state)


def poll_loop():
    log('Polling for pending scans...')
    while True:
        try:
            r = requests.get(
                f'{API_BASE}/api/internal/scans/pending',
                headers=internal_headers(),
                timeout=10
            )
            if r.ok:
                scans = r.json()
                for scan in scans:
                    dispatch(scan['id'], scan.get('pipelineStatus') or scan.get('status'))
        except Exception as e:
            log(f'Poll error: {e}')
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] == '--poll':
        poll_loop()
    elif len(sys.argv) >= 2:
        process_scan(sys.argv[1])
    else:
        print('Usage: python pipeline.py <scan_id>  |  python pipeline.py --poll')
        sys.exit(1)
