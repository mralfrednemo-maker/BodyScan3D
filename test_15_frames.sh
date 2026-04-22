#!/usr/bin/env bash
# Backend validation: run pycolmap SfM + dense MVS on 15 evenly-spaced frames
# from scan 7, report whether a usable reconstruction comes out.
set -u
SRC=/mnt/c/Users/chris/PROJECTS/BodyScan3D/uploads/frames
TEST=/tmp/bs3d_test15
WORK=/tmp/bs3d_test15_work

rm -rf "$TEST" "$WORK"
mkdir -p "$TEST" "$WORK"

# Pick 15 evenly-spaced frames by sorted filename
mapfile -t FILES < <(ls "$SRC"/*.jpg | sort)
TOTAL=${#FILES[@]}
echo "Total frames available: $TOTAL"

for i in $(seq 0 14); do
    IDX=$(( i * TOTAL / 15 ))
    cp "${FILES[$IDX]}" "$TEST/"
done

PICKED=$(ls "$TEST" | wc -l)
echo "Picked: $PICKED frames, $(du -sh "$TEST" | cut -f1)"

cd "$WORK"

python3 <<'PY'
import os, time, sys, traceback
import pycolmap

TEST = "/tmp/bs3d_test15"
WORK = "/tmp/bs3d_test15_work"
os.makedirs(WORK, exist_ok=True)
DB = os.path.join(WORK, "database.db")
SPARSE = os.path.join(WORK, "sparse")
os.makedirs(SPARSE, exist_ok=True)

t0 = time.time()
print(f"[1/4] extract_features from {TEST}...")
pycolmap.extract_features(
    database_path=DB,
    image_path=TEST,
    camera_mode=pycolmap.CameraMode.SINGLE,
)
print(f"     done in {time.time()-t0:.1f}s")

t1 = time.time()
print(f"[2/4] match_exhaustive...")
pycolmap.match_exhaustive(database_path=DB)
print(f"     done in {time.time()-t1:.1f}s")

t2 = time.time()
print(f"[3/4] incremental_mapping...")
maps = pycolmap.incremental_mapping(
    database_path=DB,
    image_path=TEST,
    output_path=SPARSE,
)
print(f"     done in {time.time()-t2:.1f}s")

if not maps:
    print("FAIL: incremental_mapping returned empty — pycolmap could not build a reconstruction from 15 frames")
    sys.exit(1)

# Report sparse reconstruction quality
recon_dir = os.path.join(SPARSE, "0")
if not os.path.isdir(recon_dir):
    subdirs = sorted(os.listdir(SPARSE))
    if subdirs:
        recon_dir = os.path.join(SPARSE, subdirs[0])

recon = pycolmap.Reconstruction(recon_dir)
n_images_registered = len(recon.images)
n_points = len(recon.points3D)
n_cameras = len(recon.cameras)

print()
print("=" * 60)
print("SPARSE RECONSTRUCTION RESULT")
print("=" * 60)
print(f"Images registered: {n_images_registered} / 15")
print(f"3D points:         {n_points}")
print(f"Cameras:           {n_cameras}")

# Attempt dense MVS (requires CUDA pycolmap)
t3 = time.time()
print(f"\n[4/4] dense MVS (CUDA patch-match stereo)...")
MVS = os.path.join(WORK, "mvs")
os.makedirs(MVS, exist_ok=True)
dense_ok = False
try:
    pycolmap.undistort_images(output_path=MVS, input_path=recon_dir, image_path=TEST)
    print("     undistort OK")
    pycolmap.patch_match_stereo(workspace_path=MVS)
    print("     patch-match stereo OK")
    fused = os.path.join(MVS, "fused.ply")
    pycolmap.stereo_fusion(output_path=fused, workspace_path=MVS)
    if os.path.exists(fused):
        size = os.path.getsize(fused)
        print(f"     stereo fusion OK: {fused} ({size} bytes)")
        dense_ok = True
except Exception as e:
    print(f"     FAIL (dense MVS unavailable): {type(e).__name__}: {e}")
    traceback.print_exc()

print()
print("=" * 60)
print("VERDICT")
print("=" * 60)
print(f"Sparse reconstruction: {'OK' if n_points > 0 else 'FAIL'} ({n_points} points)")
print(f"Dense MVS (CUDA):      {'OK' if dense_ok else 'NO / FALLBACK TO SPARSE'}")
print(f"Images registered:     {n_images_registered}/15 ({n_images_registered*100//15}%)")
print(f"Total time:            {time.time()-t0:.1f}s")

if n_images_registered >= 12 and n_points > 500:
    print("\n=> Backend CAN produce a reconstruction from 15 photos.")
    if dense_ok:
        print("   Dense MVS is wired correctly. Expect a real Poisson mesh at the end.")
    else:
        print("   BUT dense MVS is NOT available — final output will be a sparse")
        print("   point cloud only. Poisson may struggle to mesh it.")
elif n_images_registered < 12:
    print("\n=> WARNING: only {} images registered. pycolmap could not connect".format(n_images_registered))
    print("   15 photos into a single consistent reconstruction. Possible causes:")
    print("   - Background features dominate (need SAM 2 masking)")
    print("   - Insufficient overlap between adjacent frames")
    print("   - Featureless object")
else:
    print("\n=> FAIL: reconstruction produced too few points to be usable.")
PY
RC=$?
echo ""
echo "[DONE] exit code: $RC"
exit $RC
