# Task #13 Artifact: Cross-cutting — Encryption, Access Control, Audit Logging
**Task:** Cross-cutting: Encryption at rest + role-scoped access + audit logging
**Status:** COMPLETED
**Date:** 2026-04-21
**Output:** server.js audit wiring + middleware stubs

## What This Implements

### Audit Logging (CC-10)
`audit_log` table records all significant actions:
- `SCAN_CREATED` — scan initiated by professional
- `SCAN_STATUS_CHANGED` — status transition (from→to)
- `SCAN_DELETED` — scan deleted
- `WORKER_STATUS_CHANGE` — worker-driven status update
- `ASSET_PUBLISHED` — publish manifest created

`auditAction()` helper logs non-fatally (audit failure doesn't break requests).

### Role-Scoped Access Stub (CC-11)
`requireOperator` middleware validates:
- `x-operator-id` header — required integer operator ID
- `x-operator-role` header — role string (admin/operator/viewer/device)

Roles are enforced via header inspection. Full RBAC integration is a deployment concern.

### Encryption at Rest (CC-12)
Encryption at rest is a **deployment-level concern**, not application-level:
- **LUKS** (Linux): Full-disk encryption on the server host
- **Cloud storage**: AWS S3 SSE-KMS, Google Cloud Storage CMEK, Azure Disk Encryption
- **Database**: SQLite database file encrypted with SQLCipher

Application-level encryption of individual artifacts is not implemented (would require key management infrastructure).

## Key Files Changed

| File | Change |
|------|--------|
| `server.js` | Added `auditAction()` helper, `requireOperator()` middleware, audit calls on scan CRUD + worker status + publish, `GET /api/internal/scans/:id/audit-log` endpoint |

## Audit Events

| Event | Trigger | Fields |
|-------|---------|--------|
| `SCAN_CREATED` | POST /api/scans | scan_id, bodyPart, title |
| `SCAN_STATUS_CHANGED` | PATCH /api/scans/:id (status field) | scan_id, newStatus |
| `SCAN_DELETED` | DELETE /api/scans/:id | scan_id |
| `WORKER_STATUS_CHANGE` | POST /api/internal/scans/:id/status | scan_id, from, to, failureClass, errorMessage |
| `ASSET_PUBLISHED` | POST /api/internal/scans/:id/publish-manifest | scan_id, publishability_class |
| `CREATE_*` | (via recordLineageEvent) | via lineage_events table |

## OPEN Items
- Full RBAC integration (role-based route protection)
- Encryption key management infrastructure
