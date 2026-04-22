# GAP ANALYSIS PASS 3 — Fresh Requirements Sweep
**Date:** 2026-04-22
**Trigger:** Third-pass fresh sweep after fixing passes 1+2

---

## NEW CRITICAL (not found in prior passes)

### [GAP-3A] `model_uv_url`/`model_deform_url` silently dropped from `geometry_outputs` INSERT
- **Severity:** CRITICAL
- **Location:** `server.js` lines ~1572-1583
- **Issue:** `geometry_outputs` table schema has no `model_uv_url` or `model_deform_url` columns. INSERT includes these columns but they're silently dropped. `mesh_worker.py` generates `model_uv.glb` and posts it, but server discards it. R-OUT-2 (UV parameterization for tattoo placement) is broken end-to-end.
- **Fix:** Add `model_uv_url TEXT` and `model_deform_url TEXT` columns to `geometry_outputs` table

### [GAP-3B] `PUBLISHED` not in `STATUS_TO_LEGACY` — SPA viewer fails on real pipeline output
- **Severity:** CRITICAL
- **Location:** `server.js` lines ~425-443
- **Issue:** `STATUS_TO_LEGACY` maps `COMPLETED -> 'ready'` but has no entry for `PUBLISHED`. The 8-stage pipeline transitions to `PUBLISHED` (oqsp_worker.py). The SPA viewer checks `status === "ready"`. Without PUBLISHED mapping, published scans don't mount the viewer.
- **Fix:** Add `'PUBLISHED': 'ready'` to `STATUS_TO_LEGACY`

### [GAP-3C] `pending` endpoint only polls 5 of 11 pipeline states
- **Severity:** CRITICAL
- **Location:** `server.js` lines ~1900-1901
- **Issue:** `GET /api/internal/scans/pending` only queries for 5 states: `'VIDEO_UPLOADED','FRAME_QA','MASKING','RECONSTRUCTING','POST_PROCESSING'`. But `WORKER_FOR_STATE` in pipeline.py also includes `'FSCQI','SIAT','REG','PHOTOREAL','EDSIM','OQSP'`. If `pipeline.py --poll` is used, scans stuck in intermediate stages are never picked up after a restart.
- **Fix:** Add missing states to the pending query

---

## NEW HIGH

### [GAP-3D] `MASKING` in pending query with no corresponding worker
- `MASKING` queried but no worker in `WORKER_FOR_STATE`. `FSCQI/SIAT/REG` in worker but missing from pending query.

### [GAP-3E] Stale rebind uses fragment count not geometry hash
- `detect_stale_rebind` in `edsim_worker.py` compares `fragment_count`, not actual geometry content hash. DoD ED-5 requires geometry hash comparison.

### [GAP-3F] Lineage fingerprint ignores actual artifact chain
- `compute_lineage_fingerprint` in `photoreal_worker.py` self-computes a subset instead of calling `recordLineageEvent` to get authoritative content-addressed hashes.

---

## MEDIUM

### [GAP-3H] No scan ID input validation on internal endpoints
### [GAP-3J] Purge leaves view/sparse directories on disk
### [GAP-3K] `artifact-versions/:type` accepts arbitrary type strings

---

## PRIORITY ORDER FOR FIXES

1. **GAP-3B** — add `'PUBLISHED': 'ready'` to STATUS_TO_LEGACY (quick fix)
2. **GAP-3C** — add missing states to pending query (quick fix)
3. **GAP-3A** — add columns to geometry_outputs table (schema migration)
