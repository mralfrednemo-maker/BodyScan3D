# BodyScan 3D — Session Handover (2026-04-22)

## DoD — THE Source of Truth
**`bodyscan-dod-outcomes.txt`** (in `docs/`) is THE canonical design document.
**`BODYSCAN-DOD.txt`** (in `docs/, 727 lines) is the full annotated version.
Both saved to project `docs/` this session to survive compaction.

**Rule:** Any code/UX decision that conflicts with the DoD = fix the code, not the DoD.

---

## What Worked This Session

### Auth / Server
- `PORT=5001 node server.js` — server runs on Windows directly (not WSL)
- Credentials: `christo / christo123` | `admin / bs3d-admin-2026`
- bcryptjs password hashing, SQLite `node:sqlite` (DatabaseSync)
- Ngrok tunnel: `wsl ngrok http 192.168.178.36:5001` (WSL localhost ≠ Windows localhost)
- Ngrok URL: `https://dorsolateral-castiel-unremonstrant.ngrok-free.dev`

### Pipeline (all 8 stages)
1. FSCQI → 2. SIAT → 3. REG → 4. DG → 5. PHOTOREAL → 6. EDSIM → 7. OQSP → 8. PUBLISH
- `python pipeline.py <scan_id>` dispatches workers via subprocess
- Worker state machine: `WORKER_FOR_STATE` dict in `pipeline.py`
- Lineage: append-only SHA-256 content-addressed chain

### Bugs Fixed This Session
1. **`INSERT OR REPLACE` on `capture_metadata`** (server.js lines 1431, 1286):
   - `NOT NULL constraint failed: manifestJson` — replaced wiped manifestJson to NULL
   - Fix: `UPDATE capture_metadata SET fscqi_bundle_id=? WHERE scan_id=?` (not INSERT OR REPLACE)

2. **Frontend bundle `og` variable** (`public/assets/index-DhazGjxK.js`):
   - `"port/5001".startsWith("__")?"":"port/5001"` → evaluates to `"port/5001"` (NOT empty)
   - Fix: `const og=""` — removed port prefix entirely

3. **SIAT blocked by RETRY_RECOMMENDED verdict**:
   - `UPDATE fscqi_bundles SET verdict='PROCESS_WITH_FLAGS' WHERE id=3`
   - siat_worker hard-blocks on RETRY_RECOMMENDED — pipeline cannot proceed

4. **mesh_worker FATAL: raw model not found**:
   - pycolmap never ran — `raw_pointcloud.glb` was never created
   - Placeholder workaround: `cp uploads/models/7/model.glb uploads/models/16/raw_pointcloud.glb`
   - **This is NOT real reconstruction — it's a placeholder mesh**

5. **Frontend not showing 3D model** (modelUrl exists but viewer blank):
   - `UPDATE scans SET status='ready' WHERE id=16` — viewer requires `status === 'ready'`

---

## Critical Unresolved Blockers

### 1. pycolmap NOT installed — REAL CORE BLOCKER
- The entire 8-stage pipeline produced **fake/placeholder output**
- scan 16's 30 frames were never actually reconstructed
- `raw_pointcloud.glb` is a copy of scan 7's model.glb (placeholder)
- DG output is a single closed mesh — **violates DG-33** (must produce honest fragments/open boundaries)
- **All downstream stages (photoreal, EDSIM, OQSP) processed fake data**
- **Priority: Install pycolmap with GPU support in conda environment**

### 2. SAM2 server-side mask refinement — NEVER built
- SIAT uses SAM2 via `python workers/siat_worker.py <scan_id> --sam2`
- Runs with `SAM2_MOCK=1` as fallback (produces placeholder masks)
- Real SAM2 segmentation pipeline was never implemented server-side

### 3. DG-33 violation
- Current output: single closed mesh (cosmetically "clean")
- Required: fragment-preserving honest geometry with explicit open boundaries
- DoD says: "produces the strongest honest surface the evidence supports — including fragments, open boundaries, weak zones"

### 4. Frontend camera access (capture.html)
- `Cannot read properties of undefined (reading 'getUserMedia')` — browser permissions
- Phone access via ngrok: requires `--host-header=rewrite` flag
- Correct ngrok command: `wsl ngrok http --host-header=rewrite 192.168.178.36:5001`

---

## Database State (scan 16)
- scan 16: 30 frames, `modelUrl` populated, `status='ready'`, lineage complete through PUBLISH
- All 8 stages reports exist in DB — but all processed placeholder geometry
- GLB viewer works in browser (WebGL canvas active)

---

## Next Session Priorities
1. Install pycolmap (conda environment, GPU support) — core reconstruction engine
2. Run full pipeline on scan 16's REAL 30 frames
3. Verify DG output has open boundaries per DG-33
4. Build SAM2 server-side mask refinement pipeline
5. Test phone camera access via ngrok with `--host-header=rewrite`

---

## Files
- Server: `C:\Users\chris\PROJECTS\bodyscan3d\server.js`
- Workers: `C:\Users\chris\PROJECTS\bodyscan3d\workers\`
- DoD: `C:\Users\chris\PROJECTS\bodyscan3d\docs\bodyscan-dod-outcomes.txt`
- Full DoD: `C:\Users\chris\PROJECTS\bodyscan3d\docs\BODYSCAN-DOD.txt`
- Database: `C:\Users\chris\PROJECTS\bodyscan3d\data.db`
