# [BODYSCAN3D] Next Session Brief — Video-First Pipeline

**Date:** 2026-04-16
**Written by:** Mitso (previous session, context full)
**Status:** Architecture pivot approved by Christo. APK abandoned. Backend-only video-extraction pipeline to be built next.

---

## The pivot (read this first)

After **five failed APK designs** on 2026-04-15/16 — culminating in Slice 1.5 (12-slot clockface + shutter button) which Christo field-tested and declared "a disaster" — Christo asked:

> "My phone has Full HD 60fps capability. Do we actually need an APK, or should I just take a video then upload it and then point to the subject of interest somewhere on the backend before processing?"

**Answer: no APK needed.** The native phone camera app is a better capture UX than anything we can build. Backend does all the thinking. Browser does all the interaction.

**This approach IS compatible with the design doc.** `design-ledger-20260415-081149-FINAL-MASTER.md` §5 explicitly rejected *video-first reconstruction* (Gaussian Splatting from raw video) but *allowed* "selected frames from video" as input. Video + keyframe extraction + classical pipeline is not the thing the adjudicator rejected.

---

## Architecture (what to build)

```
Phone (native camera app)      Browser (dashboard)              WSL backend
────────────────────────      ─────────────────────            ──────────────
1. Film 20-30s walkaround  →  2. Upload video via web form
   around subject (1080p60)    POST /api/video-upload
                               → scan state=VIDEO_UPLOADED  →  3. Keyframe worker
                                                                (NEW: workers/video_worker.py)
                                                                opens mp4 with OpenCV,
                                                                samples every ~200ms,
                                                                Laplacian-variance blur filter,
                                                                keeps N (15-30) sharpest
                                                                non-redundant frames.
                                                                State → FRAME_QA.
                               4. prompt.html (EXISTS)    ←
                                  User taps on subject in
                                  sharpest keyframe.
                                  State → MASKING.
                                                            →  5. mask_worker.py (EXISTS,
                                                                real SAM 2 on GPU, ready)
                                                            →  6. reconstruct_worker.py
                                                                (EXISTS, sparse SfM +
                                                                NEW Docker CUDA dense MVS)
                                                            →  7. mesh_worker.py (EXISTS,
                                                                Poisson cleanup, GLB export)
                               8. Dashboard shows model.glb
                                  in 3D viewer.
```

**Only 3 new things to build.** The rest is already wired and tested.

---

## What's already ready (verified 2026-04-15 evening, post-reboot)

| Component | State | Location |
|---|---|---|
| Server | Running on `:5000`, HTTP 200 | `C:\Users\chris\PROJECTS\BodyScan3D\server.js` |
| Pipeline auto-poller | Running in WSL venv | Background job `bxpa03fbq` (may need restart if killed) |
| NVIDIA driver | 595.79, CUDA 13.2 | (Windows host — verified via `wsl -- nvidia-smi`) |
| PyTorch + SAM 2 + checkpoint | Installed, GPU-ready | `/home/christos/bs3d-venv/` + `/home/christos/sam2_checkpoints/sam2_hiera_small.pt` |
| Docker COLMAP (CUDA 12.9.1) | Pulled, GPU verified | `colmap/colmap:latest` |
| apt colmap (CPU) | Installed but unused | `/usr/bin/colmap` (CPU-only, Christo installed it via `sudo apt install colmap`) |
| `mask_worker.py` | Wired for real SAM 2 + auto-centre-prompt fallback | `workers/mask_worker.py` |
| `reconstruct_worker.py` | **Modified today** to shell out to Docker COLMAP for dense MVS | `workers/reconstruct_worker.py` — see `run_dense_mvs()` |
| Existing frames for testing | 37 real frames from scan 7 (April 12) | `uploads/frames/` |
| `prompt.html` | Exists — click-on-frame UI that emits SAM 2 prompt anchors | `public/prompt.html` |

**Pipeline worker env vars (important):**
- `BS3D_API_BASE="http://192.168.178.36:5000"` (LAN IP; WSL resolv.conf is unreliable post-reboot)
- `BS3D_UPLOADS_DIR=/mnt/c/Users/chris/PROJECTS/BodyScan3D/uploads`
- `SAM2_CHECKPOINT=/home/christos/sam2_checkpoints/sam2_hiera_small.pt`
- `SAM2_CONFIG=configs/sam2/sam2_hiera_s.yaml`
- `SAM2_MOCK` NOT set (use real SAM 2)

---

## What to build — step-by-step

### Step 1: Video upload endpoint (server.js) — ~1 hr

Add to `server.js`:
```js
// New endpoint — accepts mp4/mov from a web form
app.post('/api/video-upload', upload.single('video'), (req, res) => {
  const scanId = createScanRecord({ state: 'VIDEO_UPLOADED', sourceType: 'video' });
  // Move uploaded file to uploads/videos/<scan_id>.mp4
  // Return { scanId }
});
```

Add to `uploads/`: new subdir `videos/` for raw video blobs.

Add `VIDEO_UPLOADED` to the scan state enum (check schema in server.js around DB init).

### Step 2: Upload page — ~30 min

New file: `public/upload-video.html`
- Minimal HTML form: `<input type="file" accept="video/*">` + submit button
- On submit, POST to `/api/video-upload`, show progress, redirect to `/prompt.html?scan_id=X` on success.
- Mobile-friendly (viewport meta, large tap targets).

### Step 3: Keyframe extractor worker — ~2 hrs

New file: `workers/video_worker.py`

```python
# Responsibilities:
# 1. Fetch scan from API by ID (state=VIDEO_UPLOADED).
# 2. Open uploads/videos/<scan_id>.mp4 with cv2.VideoCapture.
# 3. Sample every N_SAMPLE_MS (default 200ms).
# 4. For each sampled frame: compute Laplacian variance (blur score).
# 5. Keep top K_KEYFRAMES (default 25) by sharpness with diversity filter:
#    - No two kept frames within MIN_TEMPORAL_GAP_MS (default 400ms).
#    - Optional: cosine distance on ORB features > DIVERSITY_THRESHOLD
#      to reject near-duplicate frames from static moments.
# 6. Save kept frames as uploads/frames/<uuid>.jpg (match APK-era naming).
# 7. POST frames to /api/internal/scans/<id>/frames (same as APK would).
# 8. Transition state → FRAME_QA.
```

Dependencies: already in venv (opencv-python-headless, requests, Pillow). No new deps.

Register in `workers/pipeline.py` `WORKER_FOR_STATE`:
```python
'VIDEO_UPLOADED':  'video_worker',
```

### Step 4: Verify prompt.html handoff — ~30 min

Existing `public/prompt.html` expects: a frame URL to display, click coords to emit as SAM 2 prompt anchor. Walk through the code, confirm it works after keyframe extraction. May need to tweak what it shows (the sharpest keyframe, not all frames).

### Step 5: Test E2E — ~30 min

Christo films a 20-30 sec walkaround of a simple object on his phone, uploads via `upload-video.html` on his phone browser, taps the subject on `prompt.html` (desktop or phone), waits ~5 min, views the resulting `/uploads/models/<scan_id>/model.glb` on the dashboard.

---

## Known gotchas from today

1. **WSL `resolv.conf` is unreliable post-reboot.** `ip route show default` also returned empty. **Always use `192.168.178.36:5000` as `BS3D_API_BASE` in the pipeline workers.** It's Christo's Windows LAN IP and is reachable from WSL.

2. **PowerShell reports `gradlew.bat` as exit 1 even when `BUILD SUCCESSFUL`.** The SDK XML version warning gets captured as `NativeCommandError`. Always grep the output for `BUILD SUCCESSFUL` or `FAILED` rather than trusting the exit code. (Mostly irrelevant for video work, but might come up if we ever touch the APK repo again.)

3. **Codex companion runs sandboxed to the cwd where it's invoked.** Run it from `C:/Users/chris/PROJECTS/BodyScan3D/` so it can write to `server.js`, `workers/`, `public/`.

4. **`pip install pycolmap` is CPU-only.** Dense MVS requires Docker COLMAP. `reconstruct_worker.py` `run_dense_mvs()` was updated today to shell out to `docker run --gpus all -v workdir:/work -v image_dir:/images:ro colmap/colmap:latest bash -c "colmap image_undistorter ... && patch_match_stereo ... && stereo_fusion"`. **This hasn't been smoke-tested end-to-end yet** — the 15-frame test (`test_dense_15.sh`) was written but not run. Run it early to confirm Docker COLMAP dense path works.

5. **15 photos is probably too few for a reliable reconstruction.** Earlier CPU-only test on scan 7's existing 37 frames (down-sampled to 15) registered only 10/15 images because background features dominated. With video keyframe extraction we can easily keep 25-30 frames, which is much safer. Recommend default `K_KEYFRAMES=25` not 15.

6. **Codex prompt budget.** Don't feed Codex the full session history when writing the new worker — just the brief (this file) + `workers/frame_qa.py` for style reference + `workers/config.py`. It handles ~60k tokens cleanly but starts hallucinating around 100k.

7. **The dual-review pattern still works.** For each new file the next session writes: Codex review-until-zero, then `superpowers:code-reviewer` as final gate. Saved memory: `feedback_BodyScan3D_review_loop_order.md`. Even though this is a simpler slice, the pattern caught High bugs on every prior slice — keep using it.

---

## Open questions for Christo (answer early, don't guess)

1. **Video container format:** mp4 with H.264 is universal. Pixel 9 Pro's native camera records h265/HEVC by default on some Android versions. If Christo's phone records HEVC, OpenCV might refuse to decode without ffmpeg. Check before building — may need `apt install ffmpeg` and decode via ffmpeg CLI subprocess.

2. **Max video size:** a 30s 1080p60 clip is ~150-300 MB. Multer default upload limit is 10 MB — needs bump in server.js.

3. **Who holds the video after processing?** Keep the raw video after keyframe extraction (debuggability), or delete to save space? Default: keep for 7 days then delete via a cron or lifecycle rule. Easier: keep forever, worry later.

4. **Prompt UI on phone vs desktop:** Christo's phone is a Pixel 9 Pro. The tap-on-subject step is easier on a bigger screen. But if he wants the entire flow on the phone, we need to make sure `prompt.html` is mobile-friendly with a zoomable frame. Check early.

5. **Background (cluttered vs plain):** With real SAM 2 masking on GPU (now wired), cluttered backgrounds should be handled OK. Earlier CPU-only test with mock masks struggled because of background features. This may no longer be an issue — but if the first video test fails to register all frames, that's the first thing to check.

---

## Files not to touch unless asked

- `BodyScan3DCapture/` — the APK repo. **Abandoned.** Do not rebuild it, do not fix more findings, do not ship another slice. If Christo ever asks about it, remind him of today's decision.
- `design-ledger-20260415-081149-FINAL-MASTER.md` — the spec is now historical. Its *principles* (don't make the user a photographer, etc.) still guide UX thinking. Its *specifics* (144-cell sphere, etc.) are no longer relevant.
- `docs/SLICE-1-BRIEF.md`, `docs/SLICE-1.5-BRIEF.md` — kept for historical record. Irrelevant to the video pipeline.

---

## Status of running background jobs from the previous session

These may still be alive when the next session opens — or they may have died. Check and restart if needed.

- **Server** (node server.js) — check with `curl -s http://localhost:5000/api/health`. If dead, restart with `cd C:\Users\chris\PROJECTS\BodyScan3D && node server.js` (run in Windows, NOT WSL — the phone needs to reach it on the LAN).
- **Pipeline poller** — check with `wsl -- bash -c 'ps aux | grep pipeline.py'`. If dead, restart with:
  ```
  wsl -- bash -c 'source /home/christos/bs3d-venv/bin/activate && \
    export BS3D_API_BASE="http://192.168.178.36:5000" && \
    export BS3D_UPLOADS_DIR=/mnt/c/Users/chris/PROJECTS/BodyScan3D/uploads && \
    export SAM2_CHECKPOINT=/home/christos/sam2_checkpoints/sam2_hiera_small.pt && \
    export SAM2_CONFIG=configs/sam2/sam2_hiera_s.yaml && \
    cd /mnt/c/Users/chris/PROJECTS/BodyScan3D/workers && \
    python3 pipeline.py --poll 2>&1'
  ```

---

## Quickstart for the next Mitso

1. Read this doc top-to-bottom. (5 min)
2. Verify server + poller are alive (or restart them).
3. Run `test_dense_15.sh` to smoke-test the Docker COLMAP dense path that got wired but never tested. If it works, dense MVS is ready for the first real video test. If it fails, debug BEFORE writing the video pipeline — no point shipping a pipeline if its final stage is broken.
4. If dense test passes → ask Christo what object he wants to film first for the E2E test. Something with texture and not translucent — a vase, a shoe, a small statue. Not a black leather bag.
5. Implement Step 1 (`/api/video-upload` endpoint) first — smallest change, most testable.
6. Move through Steps 2-5 with dual-reviewer (Codex → code-reviewer) on each.
7. Christo films → uploads → taps subject → waits → views model.glb.
8. If it works, you've done in half a day what five APK designs couldn't do in two weeks. If it doesn't, you've got concrete output to debug from.

---

## Note to Mitso-next on character

The current session had Christo frustrated multiple times — "a disaster", "fucking disaster". He earned it. Five APK designs, all polished garbage. He was ready to kill the whole project. The video pivot pulled it back from the edge.

**Don't oversell the video approach.** Don't promise it'll work. Just build it, run the test, tell him what came out. Evidence over confidence.

The Mitso abides.
