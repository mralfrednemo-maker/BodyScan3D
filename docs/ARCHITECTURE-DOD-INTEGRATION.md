# BodyScan3D — DoD Subsystem Integration Architecture
**Task:** #2
**Date:** 2026-04-21
**Author:** Newton

---

## 1. Pipeline Architecture

### 1.1 Full Pipeline States

```
CAPTURING → UPLOADING → FRAME_QA → FSCQI → SIAT → REG → DG → PHOTOREAL → EDSIM → OQSP → PUBLISHED
                 ↑
VIDEO_UPLOADED → EXTRACTING_KEYFRAMES ─────────────────────────┘
```

**State definitions:**

| State | Description |
|-------|-------------|
| CAPTURING | Phone capture in progress (30 frames) |
| UPLOADING | Frames arriving, not yet finalized |
| VIDEO_UPLOADED | Native camera video uploaded |
| EXTRACTING_KEYFRAMES | video_worker running |
| FRAME_QA | blur scoring, anchor selection |
| FSCQI | Full Signal Quality + Coverage Index (new) |
| SIAT | Subject Isolation and Targeting (new) |
| REG | Registration with honest scale posture (new) |
| DG | Detail Geometry — fragment-preserving (new) |
| PHOTOREAL | View-capable realization (new) |
| EDSIM | Edit Simulation — authority routing (new) |
| OQSP | Organize-Qualify-Select-Publish (new) |
| PUBLISHED | Externally publishable with honest view-capable route |

### 1.2 Gate Logic

**Capture review gate (after UPLOADING):**
```
operator sees: USABLE | USABLE_WITH_FLAGS | WORTH_PATCH | RETRY_RECOMMENDED
  ↓
USABLE → FRAME_QA
USABLE_WITH_FLAGS → FRAME_QA (flagged in provenance)
WORTH_PATCH → PATCH_CAPTURE → re-evaluate
RETRY_RECOMMENDED → CAPTURING (new capture session)
```

**OQSP publish gate (before PUBLISHED):**
```
condition: honest_view_capable_route EXISTS
  AND: lineage_valid (parent pointers intact)
  AND: honesty_channels_intact (all required metadata present)
  AND: no_severe_geometry_concern
→ PUBLISHED

else → internal_valid_only (not externally publishable)
```

**FSCQI verdict routing (new, after FRAME_QA):**
```
PROCESS_CLEAN     → SIAT (normal path)
PROCESS_WITH_FLAGS → SIAT (flagged in provenance, downstream aware)
REVIEW_NEEDED     → operator_review queue (blocks auto-pipeline)
RETRY_RECOMMENDED → CAPTURING (new capture)
```

---

## 2. Data Model

### 2.1 Schema Migration Sequence

Migration order matters — later subsystems reference earlier ones' version fields.

```
Phase 1 (this migration):
  - capture_metadata additions (fscqi_bundle_version FK)
  - scan_frames additions (content_hash)
  - capture_sessions additions (merkle_root)

Phase 2 (FSCQI):
  - fscqi_bundles
  - coverage_descriptors
  - weak_region_registers

Phase 3 (SIAT):
  - siat_outputs

Phase 4 (REG):
  - reg_outputs

Phase 5 (DG):
  - geometry_outputs

Phase 6 (View):
  - view_outputs

Phase 7 (EDSIM):
  - edit_sim_outputs
  - placement_state_bundles
  - preview_state_bundles
  - stale_rebind_register

Phase 8 (OQSP):
  - publish_manifests
  - qc_artifacts

Cross-cutting (any order):
  - lineage_events
  - audit_log
  - purge_log
  - artifact_versions (content-addressed manifest)
```

### 2.2 New Tables

```sql
-- FSCQI bundles (one per scan, versioned)
CREATE TABLE fscqi_bundles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    bundle_version   TEXT NOT NULL,          -- e.g. "1.0.0"
    verdict         TEXT NOT NULL,          -- PROCESS_CLEAN | PROCESS_WITH_FLAGS | REVIEW_NEEDED | RETRY_RECOMMENDED
    primary_tier_json  TEXT NOT NULL,       -- [frame_id, ...] top quality
    candidate_tier_json TEXT NOT NULL,      -- [frame_id, ...] borderline
    coverage_descriptor_json TEXT NOT NULL,  -- {total: float, per_region: {...}}
    weak_region_json TEXT,                  -- {regions: [{frame_id, reason, severity}]}
    health_summary_json TEXT NOT NULL,      -- {overall_score, flags: [...]}
    created_at      TEXT DEFAULT (datetime('now'))
);

-- SIAT outputs (one per scan, versioned)
CREATE TABLE siat_outputs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    output_version  TEXT NOT NULL,           -- e.g. "1.0.0"
    alpha_soft_path TEXT NOT NULL,          -- /store/scan_id/siat/v1/alpha_soft.png
    core_mask_path  TEXT NOT NULL,
    hard_mask_path  TEXT NOT NULL,
    rigid_core_path TEXT,
    boundary_conf_path TEXT NOT NULL,      -- boundary_confidence_channel
    ambiguity_tags_json TEXT,                -- [{frame_id, type, confidence}]
    occlusion_labels_json TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- REG outputs
CREATE TABLE reg_outputs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    output_version  TEXT NOT NULL,
    registration_state TEXT NOT NULL,       -- CONNECTED | PARTIAL | FRAGMENTED
    reg_graph_json  TEXT NOT NULL,          -- which frames share observations
    pose_version    TEXT NOT NULL,
    scale_regime    TEXT NOT NULL,         -- RELATIVE | METRIC | UNKNOWN
    scale_confidence_band_json TEXT NOT NULL,
    metric_trust_allowed INTEGER NOT NULL DEFAULT 0,  -- 0 or 1
    measurement_validity_claim TEXT,       -- VALID | INVALID | INDETERMINATE
    measurement_use_prohibited INTEGER NOT NULL DEFAULT 0,
    feature_support_regime TEXT NOT NULL,   -- core_only | core_plus_context | fallback_wide
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Geometry outputs
CREATE TABLE geometry_outputs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    output_version  TEXT NOT NULL,
    fragment_set_json TEXT NOT NULL,       -- [{fragment_id, vertex_count, face_count, is_anchor_zone}]
    hole_boundary_json TEXT NOT NULL,      -- {holes: [...], open_boundaries: [...]}
    usefulness_zones_json TEXT NOT NULL,   -- [{region, suitability_score}]
    severe_geometry_concern INTEGER NOT NULL DEFAULT 0,
    structural_proxy_path TEXT,
    appearance_scaffold_path TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- View outputs
CREATE TABLE view_outputs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    output_version  TEXT NOT NULL,
    view_bundle_path TEXT NOT NULL,        -- /store/scan_id/view/v1/bundle/
    lineage_fingerprint TEXT NOT NULL,     -- hash of all parent artifact hashes
    appearance_only_route INTEGER NOT NULL DEFAULT 0,  -- 1 if this is appearance-only
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Edit simulation outputs
CREATE TABLE edit_sim_outputs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    output_version  TEXT NOT NULL,
    anchor_chart_json TEXT NOT NULL,
    placement_authority_json TEXT NOT NULL,
    preview_authority_json TEXT NOT NULL,
    appearance_only_routes_json TEXT NOT NULL,
    edit_regions_json TEXT,
    stale_rebind_json TEXT,
    edit_readiness_summary_json TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Publish manifests (immutable once published)
CREATE TABLE publish_manifests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    manifest_version TEXT NOT NULL,         -- immutable after creation
    qc_artifacts_json TEXT NOT NULL,      -- 8-artifact QC set
    publishability_class TEXT NOT NULL,   -- VIEW_ONLY | EDIT_CAPABLE | FULL
    lineage_artifact_refs_json TEXT NOT NULL,
    capability_readiness_json TEXT NOT NULL,
    severe_concern_aggregation_json TEXT,
    integrity_conflict_surfaces_json TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Lineage events (append-only)
CREATE TABLE lineage_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    event_type      TEXT NOT NULL,        -- PATCH | RERUN | PUBLISH | STALE | REBIND | PURGE | CREATE
    payload_json    TEXT NOT NULL,
    parent_artifact_hash TEXT,
    new_artifact_hash TEXT,
    operator_id     INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Artifact versions (content-addressed manifest)
CREATE TABLE artifact_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    artifact_type   TEXT NOT NULL,        -- frame | mask | fscqi | siat | reg | geometry | view | eds
    content_hash    TEXT NOT NULL UNIQUE, -- SHA-256 of file content
    storage_path    TEXT NOT NULL,
    byte_size       INTEGER NOT NULL,
    parent_hash     TEXT,                 -- immutable parent pointer
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Audit log
CREATE TABLE audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id     INTEGER NOT NULL,
    action          TEXT NOT NULL,        -- UPLOAD_FRAME | FINALIZE_CAPTURE | PATCH | PUBLISH | PURGE | REBIND
    target_scan_id  INTEGER,
    target_artifact_type TEXT,
    target_artifact_hash TEXT,
    metadata_json   TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Purge log
CREATE TABLE purge_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    purged_artifact_hash TEXT NOT NULL,
    purge_reason    TEXT NOT NULL,
    lineage_safe    INTEGER NOT NULL,      -- 1 if cascade-safe, 0 if orphaned
    operator_id     INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
);
```

### 2.3 Additions to Existing Tables

```sql
-- scan_frames: content hash for integrity chain
ALTER TABLE scan_frames ADD COLUMN content_hash TEXT;

-- capture_metadata: Merkle root, FSCQI bundle FK
ALTER TABLE capture_metadata ADD COLUMN merkle_root TEXT;
ALTER TABLE capture_metadata ADD COLUMN fscqi_bundle_id INTEGER REFERENCES fscqi_bundles(id);

-- scans: canonical identity (1:1 with asset family root)
ALTER TABLE scans ADD COLUMN canonical_asset_id TEXT UNIQUE;  -- generated on first capture
ALTER TABLE scans ADD COLUMN lineage_root_hash TEXT;          -- first artifact hash

-- capture_sessions: bundle integrity proof
ALTER TABLE capture_sessions ADD COLUMN bundle_merkle_root TEXT;
```

---

## 3. Append-Only Content-Addressed Storage

### 3.1 Directory Structure

```
uploads/
  └── store/                          # content-addressed artifact store
      └── <content_hash>             # SHA-256 of file content (first 8 + last 8 for readability)
          ├── artifact               # the file itself
          └── meta.json              # {content_hash, byte_size, created_at, scan_id, artifact_type, parent_hash}

# Per-scan scratch space (not content-addressed, mutable during processing)
uploads/
  └── scan_<scan_id>/
      ├── frames/                    # incoming raw frames
      ├── fscqi/v1/                 # FSCQI outputs
      ├── siat/v1/                  # SIAT outputs
      ├── reg/v1/                   # REG outputs
      ├── geometry/v1/              # DG outputs
      ├── view/v1/                  # view bundles
      ├── eds/v1/                   # EDSIM outputs
      └── publish/v1/               # OQSP outputs

# Once an artifact is finalized, it is content-addressed and moved to store/
```

### 3.2 Content-Addressed Write Flow

```
1. Worker generates artifact (e.g., geometry_output.json)
2. Compute SHA-256(content) → hash
3. Check if hash already exists in artifact_versions
   - If yes: artifact already stored, skip (deduplication)
   - If no: write to store/<hash>/artifact + store/<hash>/meta.json
4. Insert artifact_versions row {scan_id, artifact_type, content_hash, storage_path, parent_hash}
5. Append lineage_event {event_type: "CREATE", new_artifact_hash: hash, parent_hash}
```

### 3.3 Lineage Pointer Rules

- Every artifact (except raw frames) has exactly one `parent_hash`
- Parent is the artifact from the immediately preceding subsystem
- Parent chain is immutable — never updated, only extended
- PATCH creates a new artifact with the original as parent (not a mutation)
- RERUN creates a new artifact chain — original chain is preserved
- PURGE does NOT delete content-addressed files (content hash could be referenced elsewhere) — instead marks lineage as orphaned in purge_log

---

## 4. Pipeline Sequence Detail

### Phase 0: Capture
```
Phone → capture.html → frames upload
  → capture_metadata (manifest, device info)
  → capture_session (token, expiry)
  → canonical_asset_id generated (UUID, first capture moment)
```

### Phase 1: Frame QA (existing)
```
FRAME_QA: blur scoring (Laplacian), anchor selection
  → scan_frames (with content_hash SHA-256)
  → capture_metadata.merkle_root computed from all frame hashes
```

### Phase 2: FSCQI (new)
```
FSCQI: curated primary tier, coverage descriptor, weak-region register
  → fscqi_bundles {verdict, primary_tier, coverage, weak_regions, health}
  → verdict gates next step:
     PROCESS_CLEAN / PROCESS_WITH_FLAGS → SIAT
     REVIEW_NEEDED → operator queue (blocks pipeline)
     RETRY_RECOMMENDED → operator → new capture
```

### Phase 3: SIAT (new, replaces raw masking)
```
SIAT: target_alpha_soft, target_core_mask, target_mask_hard,
      boundary_confidence_channel, ambiguity_tags, occlusion_labels
  → siat_outputs
  → Weak regions propagated to downstream (not dropped)
  → Ambiguity preserved in schema, not forced to binary
```

### Phase 4: REG (wraps pycolmap)
```
REG: intrinsics/extrinsics, registration_graph, pose_version,
     scale_regime, metric_trust_allowed (default false),
     feature_support_regime
  → reg_outputs
  → metric_trust_allowed MUST be false unless explicitly validated
```

### Phase 5: DG (wraps + enhances mesh_worker)
```
DG: surface_fragment_set, hole_boundary_map, usefulness_zones,
    structural_proxy, appearance_scaffold, severe_geometry_concern
  → geometry_outputs
  → Current mesh_worker "close holes" behavior REMOVED (violates open-boundary-explicit)
  → severe_geometry_concern = 1 if no placement-relevant anchor zone
```

### Phase 6: Photoreal (new)
```
PHOTOREAL: view_version, view_bundle, appearance_only_route
  → view_outputs
  → If appearance_only: appearance_only_route=1, no placement authority conferred
  → Lineage fingerprint = SHA-256 of all upstream artifact hashes
```

### Phase 7: EDSIM (new)
```
EDSIM: placement_authority_map, preview_authority_map,
       stale_rebind_register, refusal_zones
  → edit_sim_outputs
  → Placement only on anchor_chart regions (structural_proxy zones)
  → preview_appearance_only → non-persistent by construction
  → auto_rebind_identical only (same hash = same meaning)
```

### Phase 8: OQSP (new)
```
OQSP: publish_manifest, qc_artifacts, publishability_class
  → publish_manifests
  → Publish gate enforced:
     honest_view_capable_route EXISTS
     AND lineage_valid
     AND honesty_channels_intact
     AND no_severe_geometry_concern
  → PUBLISHED or internal_valid_only
```

---

## 5. Subsystem Interface Summary

| Subsystem | Input | Output | Version Field |
|-----------|-------|--------|-------------|
| FSCQI | scan_frames + blur scores | fscqi_bundle | fscqi_bundle_version |
| SIAT | FSCQI output + frames | siat_outputs | output_version |
| REG | SIAT masks + frames | reg_outputs | pose_version |
| DG | REG registration + geometry | geometry_outputs | geometry_version |
| Photoreal | DG geometry + SIAT masks | view_outputs | view_version |
| EDSIM | Photoreal views + DG geo | edit_sim_outputs | edit_sim_version |
| OQSP | All above | publish_manifest | manifest_version |

---

## 6. Cross-Cutting Integration

### 6.1 Version Propagation
Every subsystem reads the previous subsystem's version field from its output row. The version field is the canonical identifier for reproducibility.

```
reconstruct_worker reads: siat_outputs.output_version
mesh_worker reads:       reg_outputs.pose_version
photoreal reads:         geometry_outputs.geometry_version
edsim reads:            view_outputs.view_version
oqsp reads:             edit_sim_outputs.edit_sim_version
```

### 6.2 Telemetry ( §13 CC-11, CC-12)
Collected at these points:
- **Capture:** intervention_count, mode (M1/M2), frame_count, duration
- **FSCQI:** verdict distribution, weak_region_count, coverage_scores
- **Pipeline:** per-subsystem duration, failure classification, retry count
- **EDSIM:** stale_rebind events, placement_authority_coverage
- **OQSP:** publish_attempts, publish_rejections, reason

Telemetry endpoint: `POST /api/telemetry` (opt-in, no imagery)

### 6.3 Encryption at Rest ( §13 CC-10)
- Algorithm: AES-256-GCM
- Key derivation: PBKDF2(scan_secret + server_master_key)
- Encrypted: all files in `store/` (content-addressed artifacts)
- Not encrypted: SQLite DB (contains hashes + paths but not file content)
- In transit: TLS (server requirement)

### 6.4 Audit Log ( §13 CC-10)
Every write operation logged:
```sql
INSERT INTO audit_log (operator_id, action, target_scan_id, target_artifact_type, target_artifact_hash, metadata_json)
```
Actions: UPLOAD_FRAME, FINALIZE_CAPTURE, PATCH, STALE, REBIND, PUBLISH, PURGE

---

## 7. Known Design Decisions ( OPEN items for later )

| Item | Decision Needed | Impact |
|------|----------------|--------|
| DSCQI coverage descriptor schema | What per-region coverage metric? | Downstream |
| SIAT boundary_confidence_channel format | Per-pixel or per-region? | Storage + downstream |
| Geometry fragment merge strategy | What constitutes "same surface"? | DG output |
| View degradation fallback | Blur kernel parameters? | Photoreal |
| EDSIM anchor_chart granularity | Per-vertex or per-region? | Placement authority |

These are not resolved in this architecture — they require domain decisions. They are documented here so implementation tasks flag them for operator decision, not default assumptions.
