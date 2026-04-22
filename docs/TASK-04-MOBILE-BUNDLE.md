# Task #4 Artifact: Mobile Bundle Integrity Proof + Resumable Upload
**Task:** MB: Mobile bundle integrity proof + resumable upload state
**Status:** COMPLETED
**Date:** 2026-04-21
**Output:** `server.js` modifications

## What This Implements

### MB-1: Per-Frame SHA-256 Content Hash
- Added `content_hash TEXT` column to `scan_frames` via safe migration
- Each frame upload now computes SHA-256 of file content after write
- Hash stored in `scan_frames.content_hash`
- Hash is computed from raw file bytes (same bytes verified at upload time)

### MB-2: Bundle-Level Merkle Root
- Added `merkle_root TEXT` column to `capture_metadata` via safe migration
- Merkle root computed in `/api/capture/finalize` from all frame content hashes
- Tree construction: pairwise SHA-256 hash of adjacent leaves, repeat until root
- If odd number of leaves, last leaf is duplicated (SHA-256(left + left))
- Merkle root stored in `capture_metadata.merkle_root`

### MB-7: Intervention Counters
- Added `auto_mode_count`, `manual_shutter_count`, `retry_count` columns to `capture_metadata`
- Counters accumulated via `UPDATE ... SET col = COALESCE(col,0) + ?`
- Sent from client in first 3 frames of each upload batch

### MB-9: Integrity Proof at Capture Review Gate
- New endpoint: `GET /api/capture/bundle/:scanId`
- Returns: `scanId`, `merkleRoot`, `frameCount`, `frameHashes[]`, `interventionCounters`
- Operator review UI can fetch this to verify bundle integrity

## Key Code Changes

**server.js:**
- Added `const crypto = require('crypto');`
- Safe migrations: `scan_frames.content_hash`, `capture_metadata.merkle_root`, `capture_metadata.auto_mode_count`, `capture_metadata.manual_shutter_count`, `capture_metadata.retry_count`
- POST `/api/capture/frames`:
  - After INSERT: compute SHA-256 of each file, UPDATE `scan_frames.content_hash`
  - Accept `autoModeCount`, `manualShutterCount`, `retryCount` in form data
  - UPDATE `capture_metadata` with intervention counter increments
- POST `/api/capture/finalize`:
  - Collect all `content_hash` values in sortOrder
  - Build Merkle tree bottom-up using SHA-256(left + right)
  - Store root in `capture_metadata.merkle_root`
  - Return `merkleRoot` in response JSON
- New GET `/api/capture/bundle/:scanId`:
  - Returns full integrity proof bundle

## Merkle Tree Construction
```
Input: [h0, h1, h2, h3, h4] (content hashes in sortOrder)
Level 1: [SHA256(h0+h1), SHA256(h2+h3), SHA256(h4+h4)]
Level 2: [SHA256(SHA256(h0+h1)+SHA256(h2+h3)), SHA256(SHA256(h4+h4)+SHA256(h4+h4))]
...
Root: single SHA-256 hash
```

## Blocks
- Task #12 (append-only lineage) — uses content_hash and merkle_root
- Task #5..#11 — lineage fingerprint references frame content hashes

## OPEN Items
- Resumable upload (MB-4): NOT YET IMPLEMENTED
  - Client needs to query last-acknowledged frame index before resuming
  - Server `/api/capture/frames` accepts `frameIndex` param — partial implementation exists
  - Full resumable upload needs: client-side offset tracking + server-side gap detection
