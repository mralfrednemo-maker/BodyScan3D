#!/usr/bin/env bash
# Verify dense MVS via Docker COLMAP works on 15 frames from scan 7.
set -u
SRC=/mnt/c/Users/chris/PROJECTS/BodyScan3D/uploads/frames
TEST=/tmp/bs3d_d15
WORK=/tmp/bs3d_d15_work
rm -rf "$TEST" "$WORK"
mkdir -p "$TEST" "$WORK"

mapfile -t FILES < <(ls "$SRC"/*.jpg | sort)
TOTAL=${#FILES[@]}
for i in $(seq 0 14); do
    IDX=$(( i * TOTAL / 15 ))
    cp "${FILES[$IDX]}" "$TEST/"
done
echo "Staged $(ls "$TEST" | wc -l) frames at $TEST"

source /home/christos/bs3d-venv/bin/activate
cd "$WORK"

python3 <<'PY'
import os, sys, time
sys.path.insert(0, '/mnt/c/Users/chris/PROJECTS/BodyScan3D/workers')
from reconstruct_worker import run_pycolmap, run_dense_mvs, export_dense_glb, export_sparse_glb

TEST = "/tmp/bs3d_d15"
WORK = "/tmp/bs3d_d15_work"

t0 = time.time()
print('=' * 60)
print('STAGE 1: sparse SfM (pycolmap CPU)')
print('=' * 60)
recon_dir = run_pycolmap(TEST, WORK)
if not recon_dir:
    print('FAIL: sparse reconstruction did not produce anything')
    sys.exit(1)
print(f'Sparse OK in {time.time()-t0:.0f}s: {recon_dir}')

t1 = time.time()
print()
print('=' * 60)
print('STAGE 2: dense MVS (Docker CUDA COLMAP)')
print('=' * 60)
fused = run_dense_mvs(recon_dir, TEST, WORK)
print(f'Dense attempt took {time.time()-t1:.0f}s')

print()
print('=' * 60)
print('VERDICT')
print('=' * 60)
if fused and os.path.exists(fused):
    size = os.path.getsize(fused)
    print(f'SUCCESS: dense fused.ply = {size/1024/1024:.1f} MB at {fused}')
    out_glb = os.path.join(WORK, 'dense.glb')
    if export_dense_glb(fused, out_glb):
        gsize = os.path.getsize(out_glb)
        print(f'DENSE GLB: {gsize/1024:.0f} KB at {out_glb}')
    sys.exit(0)
else:
    print('FALLBACK: dense MVS failed; sparse-only still works.')
    out_glb = os.path.join(WORK, 'sparse.glb')
    export_sparse_glb(recon_dir, out_glb)
    gsize = os.path.getsize(out_glb)
    print(f'SPARSE GLB: {gsize/1024:.0f} KB at {out_glb}')
    sys.exit(2)
PY
RC=$?
echo ""
echo "[DONE] exit=$RC  total=$SECONDS s"
exit $RC
