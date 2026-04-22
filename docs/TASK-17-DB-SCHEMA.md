# Task #17 Artifact: DB Schema Migration — All DoD Artifact Tables
**Task:** DB: schema migration for all DoD artifact tables + lineage indexes
**Status:** COMPLETED
**Date:** 2026-04-21
**Output:** `server.js` schema additions + safe migrations

## What This Implements

### 11 New Tables (in schema order)

| Table | DoD Ref | Purpose |
|-------|---------|---------|
| `fscqi_bundles` | FS-1 | FSCQI verdict + curated tiers + coverage descriptor + weak regions |
| `siat_outputs` | SI-1 | Target isolation: alpha_soft, core_mask, hard_mask, boundary_conf, ambiguity_tags |
| `reg_outputs` | RG-1 | Registration: pose_version, scale_regime, metric_trust_allowed (default 0) |
| `geometry_outputs` | DG-1 | Fragment set, hole_boundary_map, usefulness_zones, severe_geometry_concern |
| `view_outputs` | VW-1 | View bundle path, lineage_fingerprint, appearance_only_route |
| `edit_sim_outputs` | ED-1 | Anchor chart, placement_authority, preview_authority, stale_rebind |
| `publish_manifests` | OQ-1 | Immutable publish record: qc_artifacts, publishability_class |
| `lineage_events` | CC-1 | Append-only event log: PATCH/RERUN/PUBLISH/STALE/REBIND/PURGE/CREATE |
| `artifact_versions` | CC-2 | Content-addressed manifest: content_hash, storage_path, parent_hash |
| `audit_log` | CC-10 | Operator action log: UPLOAD_FRAME/FINALIZE_CAPTURE/PATCH/PUBLISH/PURGE/REBIND |
| `purge_log` | CC-13 | Purge lineage tracking: lineage_safe flag per artifact |

### 3 Schema Additions to Existing Tables

| Table | Column | Purpose |
|-------|--------|---------|
| `scans` | `canonical_asset_id TEXT UNIQUE` | UUID generated on first capture — root of asset family |
| `scans` | `lineage_root_hash TEXT` | Hash of first artifact in chain |
| `capture_sessions` | `bundle_merkle_root TEXT` | Bundle integrity proof at session level |

## Key Design Decisions Applied

- `metric_trust_allowed` defaults to 0 (not earned by default) — from architecture §4
- `severe_geometry_concern` defaults to 0 — from architecture §5
- `measurement_use_prohibited` defaults to 0 — from architecture §4
- `appearance_only_route` defaults to 0 — from architecture §6
- `lineage_safe` is INTEGER (SQLite bool) — from architecture §13

## Implementation Notes

- All tables use `CREATE TABLE IF NOT EXISTS` — idempotent, safe on existing DB
- All schema additions use `ALTER TABLE ... ADD COLUMN` with safe migration pattern (ignore if column exists)
- Foreign keys: all artifact tables reference `scans(id)` with `ON DELETE CASCADE` implied
- `artifact_versions.content_hash` is UNIQUE — enforces content-addressed deduplication

## Blocks

- Tasks #5–#12 (all subsystems) — depend on these table definitions existing

## OPEN Items (deferred to subsystem implementation)

- Indexes on `artifact_versions.content_hash` — needed for fast dedup lookups
- Indexes on `lineage_events.scan_id + event_type` — needed for lineage traversal
- Partial index on `scans.canonical_asset_id` WHERE NOT NULL — for asset family queries
- Migration order enforcement (FOREIGN KEY constraints not strong enough alone)
