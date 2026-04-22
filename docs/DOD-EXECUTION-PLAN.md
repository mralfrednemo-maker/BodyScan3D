# BodyScan3D — DoD Gap Closure: Execution Plan
**Session:** Newton @ 2026-04-21 18:50 Athens
**DoD source:** C:\Users\chris\Downloads\BODYSCAN-DOD.txt
**Codebase:** C:\Users\chris\PROJECTS\bodyscan3d\
**Cursor file:** docs/NEWTON-CURSOR.md (authoritative for iteration state)

---

## Status: PLANNING PHASE

Task #2 (Design) is the current blocker — all other tasks remain pending until it is complete.

---

## Task Map

| # | Task | Status | Blocked by |
|---|------|--------|-----------|
| 1 | Gap analysis vs DoD | ✅ DONE | — |
| 2 | Design: DoD subsystem integration architecture | 🔄 NEXT | — |
| 3 | Capture UX: 4-state review outcomes + intervention telemetry | ⏳ Available | — |
| 4 | MB: Mobile bundle integrity proof + resumable upload | ⏳ Available | — |
| 5 | FSCQI: six artifacts + four-state verdict | 🔒 | #2, #17 |
| 6 | SIAT: target isolation + ambiguity preservation | 🔒 | #2, #17 |
| 7 | REG: multi-view registration + honest scale posture | 🔒 | #2, #17 |
| 8 | DG: fragment-preserving geometry | 🔒 | #2, #17 |
| 9 | Photoreal: view-capable realization | 🔒 | #2, #17 |
| 10 | EDSIM: authority-map + edit-simulation | 🔒 | #2, #17 |
| 11 | OQSP: organize-validate-fuse-publish | 🔒 | #2, #17 |
| 12 | Cross-cutting: append-only lineage + content-addressed storage | 🔒 | #2, #17 |
| 13 | Cross-cutting: encryption + access control + audit | 🔒 | #2 |
| 14 | Cross-cutting: telemetry tripwires + deterministic replay | 🔒 | #2 |
| 15 | Cross-cutting: purge + regeneration truth + stale/rebind | 🔒 | #2, #12, #17 |
| 16 | Evidence: full DoD verification suite | 🔒 | #5–#15 |
| 17 | DB: schema migration for all DoD artifact tables | 🔒 | #2 |

---

## Phase 1: Parallel Tracks

### Task #2 — Design: DoD Subsystem Integration Architecture
- **Owner:** Newton
- **Output:** `docs/ARCHITECTURE-DOD-INTEGRATION.md`
- **Covers:**
  - Pipeline sequence for FSCQI → SIAT → REG → DG → Photoreal → EDSIM → OQSP
  - Data model: new SQLite tables for all artifact types
  - Append-only storage design (content-addressed, SHA-256 based)
  - Where each subsystem fits in the pipeline
  - Honest four-state processability verdict routing
  - Cross-cutting integration points (lineage, telemetry, purge)
- **Blocks:** Tasks #5–#12, #15, #17

### Task #3 — Capture UX: Four-State Review Outcomes + Intervention Telemetry
- **Independent:** Yes — no blockers
- **Files affected:** `public/capture.html`, `public/capture-sw.js`, `server.js`
- **DoD section:** §4 (CX-1..CX-11), §14 (PG-1..PG-8)
- **Deliverables:**
  1. Four-state review outcome UI: USABLE, USABLE_WITH_FLAGS, WORTH_PATCH, RETRY_RECOMMENDED
  2. Patch capture offer UI (bounded M2 fallback, photo count must not exceed approved ceiling)
  3. Intervention counter telemetry (autoMode toggles, manual shutter clicks, retry events)
  4. Server: persist review outcome and intervention data to `capture_metadata` table
  5. Downstream provenance context in mobile bundle

### Task #4 — Mobile Bundle Integrity Proof + Resumable Upload
- **Independent:** Yes — no blockers
- **Files affected:** `server.js`, `public/capture.html`, `public/capture-sw.js`
- **DoD section:** §5 (MB-1..MB-10)
- **Deliverables:**
  1. SHA-256 per uploaded frame, stored in `scan_frames`
  2. Bundle-level Merkle root computed after all frames confirmed, stored in `capture_metadata`
  3. Resumable upload: client queries server for last-acknowledged frame index on reconnection, resumes from there
  4. Backend durable-persistence acknowledgment on finalize
  5. Per-frame hash verification endpoint
  6. No destructive downsampling / hidden frame filtering before upload acknowledgment

---

## Phase 2: Core Subsystems (after Task #2 + #17)

### Task #5 — FSCQI
- **New file:** `workers/fscqi.py`
- **Pipeline position:** After frame_qa, before SIAT
- **DoD section:** §6 (FS-1..FS-8)
- **Required artifacts:**
  - curated primary tier — top-scoring frames passing blur threshold
  - extended candidate tier — borderline frames
  - raw reference map — all frames with scores
  - dual-level coverage descriptor — per-region and total coverage
  - weak-region register — frames/regions below quality threshold
  - capture-health summary
  - `fscqi_bundle_version`
  - Four-state processability verdict: PROCESS_CLEAN, PROCESS_WITH_FLAGS, REVIEW_NEEDED, RETRY_RECOMMENDED
- **Key requirement:** Unique moderate-quality views can outrank pristine duplicates; minor blur/exposure drift/gaps do not default to retry

### Task #6 — SIAT
- **New file:** `workers/siat.py`
- **Pipeline position:** After FSCQI, before REG
- **DoD section:** §7 (SI-1..SI-5)
- **Required artifacts:**
  - `target_alpha_soft` — soft boundary mask
  - `target_core_mask` — high-confidence core region
  - `target_mask_hard` — binary mask
  - `static_rigid_core` / `pose_safe_support_mask`
  - `boundary_confidence_channel` — per-pixel confidence
  - ambiguity tags and occlusion labels
- **Key requirements:**
  - No forced binary certainty under uncertainty — preserve ambiguity zones
  - Retained target-adjacent context margins for downstream placement/preview
  - Occlusion labels where evidence is weak
  - No zero-shot fragility

### Task #7 — REG
- **New file:** `workers/reg.py`
- **Pipeline position:** After SIAT, before DG
- **DoD section:** §8 (RG-1..RG-6)
- **Required artifacts:**
  - Per-observation intrinsics/extrinsics (from pycolmap camera params)
  - `registration_state` ∈ {connected, partial, fragmented}
  - Session registration graph — which frames share observations
  - `pose_version`
  - Scale regime and scale_confidence_band
  - `metric_trust_allowed` (default false)
  - `measurement_validity_claim` / `measurement_use_prohibited`
  - `feature_support_regime` ∈ {core_only, core_plus_context, fallback_wide}
- **Key requirements:**
  - Partial connectedness is allowed
  - "Close but slightly shifted" is failure, not tolerance
  - Scale posture must be honest and machine-readable

### Task #8 — DG
- **Files affected:** `workers/mesh_worker.py`, new `workers/dg.py`
- **Pipeline position:** After REG, before Photoreal
- **DoD section:** §9 (DG-1..DG-6)
- **Required artifacts:**
  - `geometry_version`
  - `surface_fragment_set` — list of disconnected surface components with per-fragment metrics
  - `hole_and_open_boundary_map` — which boundaries are open vs filled
  - `surface_usefulness_zones` — regions suitable for placement anchoring
  - structural_proxy and appearance_scaffold as distinct outputs
- **Key requirements:**
  - Fragment-preserving output — don't silently merge disconnected surfaces
  - Zero placement-relevant anchor zones → `severe_geometry_concern` flag
  - Unsupported gaps NOT bridged for cosmetic neatness
  - Open boundaries must be explicit in schema

### Task #9 — Photoreal View-Capable Realization
- **New file:** `workers/photoreal.py`
- **Pipeline position:** After DG
- **DoD section:** §10 (VW-1..VW-7)
- **Required artifacts:**
  - Lineage-addressable `view_version`
  - View bundle with pipeline/version fingerprint
  - Explicit link to asset family and lineage tuple
  - Explicit appearance-only route when photoreal route unavailable
- **Key requirements:**
  - Photo-like output on near-capture viewpoints (believable skin tone, microtexture, lighting)
  - Outside supported envelope: degrade with blur/ghosting, NOT tears/floaters/inversions/collapse
  - Appearance does not confer edit authority
  - Non-go: beautiful orbit but workflow-dead asset

### Task #10 — EDSIM
- **New file:** `workers/edsim.py`
- **Pipeline position:** After Photoreal
- **DoD section:** §11 (ED-1..ED-10)
- **Required artifacts:**
  - `anchor_chart_manifest.json`
  - `placement_authority_map.json`
  - `preview_authority_map.json`
  - `appearance_only_routes.json`
  - `edit_region_definitions.json`
  - `placement_state_bundles.jsonl`
  - `preview_edit_state_bundles.jsonl`
  - `placement_on_preview_bindings.jsonl`
  - `stale_rebind_register.json`
  - `before_after_lineage.json`
  - `edit_readiness_summary.json`
  - refusal-zone files
  - decision/admission logs
- **Key requirements:**
  - Placement is persistent only on real anchor domains
  - `preview_appearance_only` is non-persistent by construction
  - `auto_rebind_identical` is the ONLY automatic rebind path
  - Non-identical cases → `stale_requires_review` or `retired`

### Task #11 — OQSP
- **New file:** `workers/oqsp.py`
- **Pipeline position:** After EDSIM
- **DoD section:** §12 (OQ-1..OQ-10)
- **Required artifacts:**
  - Immutable `publish_manifest.json`
  - 8-artifact QC set (asset/QC manifest, capability readiness, severe concern aggregation, integrity/conflict surfaces)
  - Lineage-aware artifact refs
  - `publishability_class`
  - Append-only state transition records
  - Immutable parent pointers and pipeline fingerprints
- **Key requirements:**
  - Durable resumable staged workflow (partial publish can resume)
  - Dependency-scoped invalidation after patch (NOT blind full rerun)
  - Preserve upstream weakness/refusal/stale signals — don't flatten
  - Layered publishing, not binary
  - External publish forbidden unless honest view-capable route exists
- **Blocks publishing when:** lineage corruption, integrity failures, no honest view-capable route, false metric trust

---

## Phase 3: Cross-Cutting Infrastructure

### Task #12 — Append-Only Lineage + Content-Addressed Storage
- **New files:** `workers/lineage_store.py`, `workers/event_log.py`
- **DoD section:** §13 (CC-1, CC-2, CC-8)
- **Required behaviors:**
  - Migrate artifact storage from flat `uploads/` to content-addressed store (SHA-256 content hash → path)
  - All stage versions stored with immutable parent pointers
  - Patch, rerun, publish, stale/rebind events → append-only JSONL per scan_id
  - No version/QC output/publish manifest mutated in place
  - Deterministic regeneration: same canonical asset + same derivation context + same parameters = same practical output

### Task #13 — Encryption + Access Control + Audit Logging
- **DoD section:** §13 (CC-10), §3 (WS-10)
- **Required behaviors:**
  - AES-256 encryption at rest for all raw and derived artifacts
  - Role-scoped API (professional vs client — clients see only their own scans)
  - Audit log: every write operation (upload, status change, publish, purge) with operator ID, timestamp, action, target scan
  - Opt-in operational telemetry — no imagery or identifying subject data

### Task #14 — Telemetry Tripwires + Deterministic Replay
- **New files:** `workers/telemetry.py`, `tests/deterministic_replay_test.py`
- **DoD section:** §13 (CC-6, CC-7, CC-11, CC-12)
- **Required behaviors:**
  - Telemetry tripwires: monitor intervention_counter, retry_inflation, patch_inflation, M1_demotion
  - Alert when thresholds breached; detect backend strictness leaking into operator burden
  - Telemetry state must NOT change capture/upload behavior
  - Deterministic replay test: same canonical asset + same derivation context + same parameters = same practical output
  - "Close but slightly shifted" flagged as failure

### Task #15 — Purge + Regeneration Truth + Stale/Rebind
- **New files:** `workers/purge_manager.py`, `workers/stale_rebind.py`
- **New endpoints:** `PATCH /api/scans/:id/purge`, `POST /api/scans/:id/rebind`
- **DoD section:** §13 (CC-9, CC-10)
- **Required behaviors:**
  - Purge is explicit, auditable, lineage-safe, cascade-safe
  - After purge removes required provenance, asset cannot claim regenerability/replayability
  - Regeneration_claim_verification checks parent pointers before allowing regeneration
  - Patch is repair, never silent rewrite: new version with explicit parent pointer, never mutate existing artifact
  - `auto_rebind_identical` only; non-identical → `stale_requires_review` or `retired`

---

## Phase 4: Evidence

### Task #16 — Full DoD Verification Suite
- **New file:** `tests/dod_verification_suite.py`
- **DoD section:** §15 (EV-1..EV-11), §14 (PG-1..PG-8)
- **Test categories:**
  1. Integrity tests — all stage artifacts and handoffs
  2. Replay/regeneration tests for placement and preview
  3. Lineage integrity checks across all version fields
  4. Schema validation for all machine-readable honesty channels
  5. Cross-output consistency checks on claimed regions
  6. Publish-manifest presence/integrity
  7. Region-level honesty (summaries never override regional truth)
  8. Patch regression (identical vs non-identical rebind behavior)
  9. Refusal/stale/guarded/appearance_only state machine tests
  10. UX + telemetry threshold tests (calm capture, no intervention inflation)
  11. Purge/regeneration truth tests
- **Publish gate tests:** system proves internal validity requires identity/lineage + honesty channels, NOT all R-OUT artifacts non-empty
- **This is the DoD acceptance gate — all tests must pass before "done" is claimed**

---

## Task #17 — DB Schema Migration
- **Blocks:** Tasks #5–#12, #15
- **New tables:**
  - `fscqi_bundles`
  - `siat_outputs`
  - `reg_outputs`
  - `geometry_outputs`
  - `view_outputs`
  - `edit_sim_outputs`
  - `publish_manifests`
  - `lineage_events` (append-only)
  - `audit_log`
- **New columns:** version fields and content-addressed hashes on existing tables
- **Pattern:** ALTER TABLE IF NOT EXISTS (current server.js migration pattern)

---

## Dependency Graph

```
Task #2 (Design)
    │
    ├──► Task #17 (Schema) ─────────────────────────┐
    │                                              │
Task #3 (Capture UX) ──────────────────────────────┤
    │                                              │
Task #4 (MB integrity) ────────────────────────────┤
    │                                              ▼
    │                                     Tasks #5–#11 (Subsystems)
    │                                              │
    ├──► Task #13 (Encryption/access) ◄──────────────┤
    │                                              │
    ├──► Task #14 (Telemetry/replay) ◄──────────────┤
    │                                              │
    │                                     Task #12 (Append-only lineage)
    │                                              │
    │                                     Task #15 (Purge/rebind) ◄────┤
    │                                                               │
    │                          Task #16 (Verification suite) ◄────────┘
    │
    └──► (nothing — #16 is the final gate)
```

---

## DoD Summary

BodyScan3D is **done** only when calm, phone-first capture produces a canonical, append-only, self-hosted asset family that is:
- view-capable (photoreal)
- placement-capable where authority exists (EDSIM)
- preview-capable where authority exists (EDSIM)
- replayable/regenerable while provenance exists
- privacy-safe
- publishable only within explicit authority/refusal/guarded/stale bounds

**Honest partial success is valid. Silent mutation, silent overclaim, fake continuity, fake metric trust, hidden second gating, and operator-burden backflow are not.**

---

## Key DoD Acceptance Criteria (selected)

| ID | Check | Evidence |
|----|-------|----------|
| WS-6 | Asset family is workflow-alive | Replay suite; cross-output checks |
| WS-8 | One canonical capture → one canonical asset family root, append-only | Lineage graph; root/descendant audit |
| WS-9 | No external runtime dependencies | Deployment diagram; runtime audit |
| CX-4 | Exactly four-state review outcome frame | UI inspection; event payloads |
| CX-6 | Median zero interventions, p95 ≤1, ceiling 3 | Intervention telemetry |
| MB-1 | One canonical_session_id across segments/patches/resumes | Identity audit |
| MB-2 | Raw media unmutated with encoder metadata | Bundle inspection |
| FS-3 | Four-state processability verdict emitted | Verdict schema check |
| SI-2 | Ambiguity preserved, no forced binary certainty | Mask review set |
| RG-5 | "Close but slightly shifted" treated as failure | Replay/regeneration suite |
| DG-5 | `severe_geometry_concern` when no anchor zone exists | Concern aggregation test |
| ED-7 | `auto_rebind_identical` only automatic rebind path | Patch semantics audit |
| OQ-7 | Dependency-scoped invalidation after patch, not blind rerun | Patch orchestration test |
| CC-6 | Same asset + same context + same params = same output | Deterministic replay suite |
| PG-1 | Internal validity does NOT require all R-OUT non-empty | Gate logic audit |
| PG-7 | Missing honesty channels / lineage corruption blocks publish | Release-block suite |
