# GAP ANALYSIS PASS 2 — Implementation Correctness
**Date:** 2026-04-22
**Trigger:** Horror-movie 3D rendering; DoD vs codebase gap analysis
**Type:** Implementation correctness (API usage, error handling, edge cases)

---

## CRITICAL (will crash or corrupt data)

### [API-1] `reconstruct_worker.py:118` — `os.getuid()`/`os.getgid()` don't exist on Windows
- **Severity:** CRITICAL — crashes immediately on Windows
- **Location:** `reconstruct_worker.py` line ~118
- **Current behavior:**
  ```python
  '--user', f'{os.getuid()}:{os.getgid()}',
  ```
  `os.getuid()` and `os.getgid()` are Unix-only. On Windows this raises `AttributeError`.
- **Fix:** Use `os.getpid()` and a hardcoded default or detect OS and skip the user flag on Windows.

### [API-2] `siat_worker.py:502–503` — File handles leaked
- **Severity:** CRITICAL — resource leak
- **Location:** `siat_worker.py` lines 502-503
- **Current behavior:**
  ```python
  'ambiguity_tags': json.loads(open(out_paths['_ambiguity_tags']).read()),
  'occlusion_labels': json.loads(open(out_paths['_occlusion_labels']).read()),
  ```
  Files opened but never closed. On Windows with strict file locking, this can cause issues on retry.
- **Fix:** Use `with open(...) as f: json.load(f)`.

### [API-3] `photoreal_worker.py:326,403` — GPU memory leak on exception
- **Severity:** CRITICAL — GPU memory leak in error path
- **Location:** `photoreal_worker.py` — `OffscreenRenderer` created at ~326, deleted in `finally` at ~403
- **Current behavior:** If an exception fires between renderer creation and the `finally` block (e.g., inside the per-pose render loop), `r.delete()` is never called and GPU texture memory leaks.
- **Fix:** Wrap the per-pose loop in its own try/finally with `r.delete()`, or use a context manager.

---

## HIGH (wrong results / degraded quality)

### [BUG-5] `siat_worker.py:54` — `AMBIGUITY_THRESHOLD` defined but never used
- **Location:** `siat_worker.py` line 54
- **Current behavior:** `AMBIGUITY_THRESHOLD = 0.65` is defined and logged but never used. Hardcoded threshold of `0.20` is used instead.
- **Fix:** Either use the variable or remove it.

### [BUG-6] `reg_worker.py:82–91` — BFS single-component bug, underestimates `connected_fraction`
- **Location:** `reg_worker.py` lines 82-91
- **Current behavior:** BFS for `connected_fraction` starts from only ONE node (`next(iter(connections.keys()))`). If the registration graph has multiple connected components, only one is visited. A graph with two equal-size components yields `0.5` instead of `1.0`.
- **Fix:** Iterate over ALL nodes not yet visited to find all connected components, or take the max component size.

### [ISSUE-10] `pipeline.py:59–62` — Worker failures swallowed silently
- **Location:** `pipeline.py` lines 59-62
- **Current behavior:**
  ```python
  if result.returncode != 0:
      log(f'Scan {scan_id}: {worker_name} exited {result.returncode}')
      # ← continues silently, scan stays in current state
  ```
  A scan whose worker crashes is re-polled every 10 seconds and re-runs the same failing worker indefinitely.
- **Fix:** After a worker failure, mark scan as FAILED and skip retry loop, or implement exponential backoff with max retries.

---

## MEDIUM

### [API-7] `siat_worker.py:96–98` — Silent overwrite on symlink fallback
### [BUG-8] `mesh_worker.py:70–77` — Swallows partial split state silently
### [BUG-9] `reg_worker.py:269–274` — Inconsistent return type (tuple vs string)
### [SMELL-13] `fscqi_worker.py:154–159` — `spread_y` ignored, asymmetric coverage

## LOW

### [SMELL-11] `siat_worker.py:168` — Empty slice hazard in mock_soft_masks
### [SMELL-12] `reconstruct_worker.py:227` — Ignored return value from `build_masked_frame`
### [SMELL-14] `photoreal_worker.py:264` — Zero-quaternion division by zero guard missing

---

## PRIORITY ORDER FOR FIXES

1. **API-1** — Windows crash (reconstruct_worker.py os.getuid/getgid)
2. **API-3** — GPU memory leak (photoreal_worker.py renderer cleanup)
3. **API-2** — File handle leak (siat_worker.py open/close)
4. **ISSUE-10** — Silent worker failures (pipeline.py)
5. **BUG-6** — BFS single-component (reg_worker.py)
6. **BUG-5** — Dead config variable (siat_worker.py AMBIGUITY_THRESHOLD)
7. BUG-8, BUG-9, SMELL-11, SMELL-12, SMELL-13, SMELL-14
