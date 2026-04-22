'use strict';

// Suppress experimental warnings for node:sqlite
process.removeAllListeners('warning');
process.on('warning', (w) => {
  if (w.name === 'ExperimentalWarning') return;
  console.warn(w.message);
});

const express  = require('express');
const path     = require('path');
const fs       = require('fs');
const cors     = require('cors');
const multer   = require('multer');
const bcrypt   = require('bcryptjs');
const crypto   = require('crypto');
const { v4: uuidv4 }       = require('uuid');
const { DatabaseSync }     = require('node:sqlite');
const os                   = require('os');

const app  = express();
const PORT = process.env.PORT || 5000;

// ---------------------------------------------------------------------------
// Paths & constants
// ---------------------------------------------------------------------------
const DB_PATH         = path.join(__dirname, 'data.db');
const UPLOADS_DIR     = path.join(__dirname, 'uploads');
const FRAMES_DIR      = path.join(UPLOADS_DIR, 'frames');
const MASKS_DIR       = path.join(UPLOADS_DIR, 'masks');
const VIDEOS_DIR      = path.join(UPLOADS_DIR, 'videos');
const INTERNAL_SECRET = process.env.INTERNAL_SECRET || 'bs3d-internal-2026';
if (!process.env.INTERNAL_SECRET) {
  console.warn('[WARN] INTERNAL_SECRET env var not set — using default. Set it in production.');
  if (process.env.NODE_ENV === 'production') {
    console.error('[FATAL] INTERNAL_SECRET must be set in production. Exiting.');
    process.exit(1);
  }
}

[UPLOADS_DIR, FRAMES_DIR, MASKS_DIR, VIDEOS_DIR].forEach(d => {
  if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true });
});

// ---------------------------------------------------------------------------
// Database setup (node:sqlite — built into Node 22+, no compilation)
// ---------------------------------------------------------------------------
const db = new DatabaseSync(DB_PATH);

db.exec('PRAGMA journal_mode = WAL');
db.exec('PRAGMA foreign_keys = ON');

// Core tables (unchanged schema — safe to run on existing DB)
db.exec(`
  CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password      TEXT NOT NULL,
    displayName   TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'professional',
    specialization TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    visitor_id TEXT UNIQUE NOT NULL,
    user_id    INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS clients (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    professional_id INTEGER NOT NULL,
    firstName       TEXT NOT NULL,
    lastName        TEXT NOT NULL,
    email           TEXT,
    phone           TEXT,
    notes           TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS scans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL,
    professional_id INTEGER NOT NULL,
    title           TEXT NOT NULL,
    bodyPart        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'CREATED',
    modelUrl        TEXT,
    notes           TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS scan_photos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id    INTEGER NOT NULL,
    photoUrl   TEXT NOT NULL,
    sortOrder  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS tattoo_designs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    professional_id INTEGER NOT NULL,
    name            TEXT NOT NULL,
    imageUrl        TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS scan_annotations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id    INTEGER NOT NULL,
    posX       REAL NOT NULL,
    posY       REAL NOT NULL,
    posZ       REAL NOT NULL,
    normalX    REAL NOT NULL DEFAULT 0,
    normalY    REAL NOT NULL DEFAULT 1,
    normalZ    REAL NOT NULL DEFAULT 0,
    note       TEXT NOT NULL,
    color      TEXT NOT NULL DEFAULT '#14b8a6',
    created_at TEXT DEFAULT (datetime('now'))
  );
`);

// Pipeline tables (new)
db.exec(`
  CREATE TABLE IF NOT EXISTS capture_sessions (
    id         TEXT PRIMARY KEY,
    scan_id    INTEGER NOT NULL,
    token      TEXT UNIQUE NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
  );

  CREATE TABLE IF NOT EXISTS scan_frames (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id    INTEGER NOT NULL,
    frameUrl   TEXT NOT NULL,
    sortOrder  INTEGER NOT NULL DEFAULT 0,
    blurScore  REAL,
    accepted   INTEGER NOT NULL DEFAULT 1,
    isAnchor   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS scan_prompts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id    INTEGER NOT NULL UNIQUE,
    promptJson TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS capture_metadata (
    scan_id       INTEGER PRIMARY KEY,
    deviceModel   TEXT,
    frameCount    INTEGER,
    bodyPart      TEXT,
    manifestJson  TEXT NOT NULL,
    created_at    TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS scan_masks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id    INTEGER NOT NULL,
    frameId    INTEGER NOT NULL,
    maskUrl    TEXT NOT NULL,
    confidence REAL,
    created_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS scan_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id    INTEGER NOT NULL,
    stage      TEXT NOT NULL,
    level      TEXT NOT NULL DEFAULT 'info',
    message    TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
  );

  -- ---- DoD artifact tables (Phase 2 schema) ----
  -- FSCQI bundles — one per scan, versioned (MB-5 / FS-1)
  CREATE TABLE IF NOT EXISTS fscqi_bundles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    bundle_version  TEXT NOT NULL,
    verdict         TEXT NOT NULL,
    primary_tier_json  TEXT NOT NULL,
    candidate_tier_json TEXT NOT NULL,
    raw_reference_map_json TEXT,
    coverage_descriptor_json TEXT NOT NULL,
    weak_region_json   TEXT,
    health_summary_json TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now'))
  );

  -- SIAT outputs — target isolation with ambiguity preservation (SI-1)
  CREATE TABLE IF NOT EXISTS siat_outputs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    output_version  TEXT NOT NULL,
    alpha_soft_path TEXT NOT NULL,
    core_mask_path  TEXT NOT NULL,
    hard_mask_path  TEXT NOT NULL,
    static_rigid_core_path TEXT,
    boundary_conf_path TEXT NOT NULL,
    ambiguity_tags_json TEXT,
    occlusion_labels_json TEXT,
    pose_safe_support_mask_path TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
  );

  -- REG outputs — multi-view registration with honest scale posture (RG-1)
  CREATE TABLE IF NOT EXISTS reg_outputs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    output_version  TEXT NOT NULL,
    registration_state TEXT NOT NULL,
    reg_graph_json  TEXT NOT NULL,
    pose_version    TEXT NOT NULL,
    scale_regime    TEXT NOT NULL,
    scale_confidence_band_json TEXT NOT NULL,
    metric_trust_allowed INTEGER NOT NULL DEFAULT 0,
    measurement_validity_claim TEXT,
    measurement_use_prohibited INTEGER NOT NULL DEFAULT 0,
    feature_support_regime TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now'))
  );

  -- Geometry outputs — fragment-preserving DG output (DG-1)
  CREATE TABLE IF NOT EXISTS geometry_outputs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    output_version  TEXT NOT NULL,
    fragment_set_json TEXT NOT NULL,
    hole_boundary_json TEXT NOT NULL,
    usefulness_zones_json TEXT NOT NULL,
    severe_geometry_concern INTEGER NOT NULL DEFAULT 0,
    structural_proxy_path TEXT,
    appearance_scaffold_path TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
  );

  -- View outputs — lineage-addressable photoreal bundle (VW-1)
  CREATE TABLE IF NOT EXISTS view_outputs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    output_version  TEXT NOT NULL,
    view_bundle_path TEXT NOT NULL,
    lineage_fingerprint TEXT NOT NULL,
    appearance_only_route INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
  );

  -- Edit simulation outputs — authority-map + edit-simulation (ED-1)
  CREATE TABLE IF NOT EXISTS edit_sim_outputs (
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

  -- Publish manifests — OQSP immutable publish record (OQ-1)
  CREATE TABLE IF NOT EXISTS publish_manifests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    manifest_version TEXT NOT NULL,
    qc_artifacts_json TEXT NOT NULL,
    publishability_class TEXT NOT NULL,
    lineage_artifact_refs_json TEXT NOT NULL,
    capability_readiness_json TEXT NOT NULL,
    severe_concern_aggregation_json TEXT,
    integrity_conflict_surfaces_json TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
  );

  -- Lineage events — append-only event log (CC-1)
  CREATE TABLE IF NOT EXISTS lineage_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    event_type      TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    parent_artifact_hash TEXT,
    new_artifact_hash TEXT,
    operator_id     INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
  );

  -- Artifact versions — content-addressed manifest (CC-2)
  CREATE TABLE IF NOT EXISTS artifact_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    artifact_type   TEXT NOT NULL,
    content_hash    TEXT NOT NULL UNIQUE,
    storage_path    TEXT NOT NULL,
    byte_size       INTEGER NOT NULL,
    parent_hash     TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
  );

  -- Audit log — operator action log (CC-10)
  CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_id     INTEGER NOT NULL,
    action          TEXT NOT NULL,
    target_scan_id  INTEGER,
    target_artifact_type TEXT,
    target_artifact_hash TEXT,
    metadata_json   TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
  );

  -- Purge log — purge lineage tracking (CC-13)
  CREATE TABLE IF NOT EXISTS purge_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id         INTEGER NOT NULL REFERENCES scans(id),
    purged_artifact_hash TEXT NOT NULL,
    purge_reason    TEXT NOT NULL,
    lineage_safe    INTEGER NOT NULL,
    operator_id     INTEGER,
    created_at      TEXT DEFAULT (datetime('now'))
  );
`);

// Safe column migrations — SQLite throws if column exists; we ignore that
[
  'ALTER TABLE scans ADD COLUMN pipelineStatus TEXT',
  'ALTER TABLE scans ADD COLUMN errorMessage TEXT',
  'ALTER TABLE scans ADD COLUMN stats TEXT',
  // capture_sessions: expires_at added in schema v2 (nullable for ALTER TABLE compat)
  "ALTER TABLE capture_sessions ADD COLUMN expires_at TEXT",
  // MB-1: per-frame SHA-256 content hash for mobile bundle integrity chain
  "ALTER TABLE scan_frames ADD COLUMN content_hash TEXT",
  // MB-2: bundle-level Merkle root computed from all frame content hashes
  "ALTER TABLE capture_metadata ADD COLUMN merkle_root TEXT",
  // MB-7: intervention counters for capture UX telemetry
  "ALTER TABLE capture_metadata ADD COLUMN auto_mode_count INTEGER DEFAULT 0",
  "ALTER TABLE capture_metadata ADD COLUMN manual_shutter_count INTEGER DEFAULT 0",
  "ALTER TABLE capture_metadata ADD COLUMN retry_count INTEGER DEFAULT 0",
  "ALTER TABLE capture_metadata ADD COLUMN patch_count INTEGER DEFAULT 0",
  // §2.3: canonical asset family identity + lineage root
  "ALTER TABLE scans ADD COLUMN canonical_asset_id TEXT UNIQUE",
  "ALTER TABLE scans ADD COLUMN lineage_root_hash TEXT",
  // MB-2: bundle integrity proof at session level
  "ALTER TABLE capture_sessions ADD COLUMN bundle_merkle_root TEXT",
  // FS-1: FSCQI bundle FK in capture_metadata
  "ALTER TABLE capture_metadata ADD COLUMN fscqi_bundle_id INTEGER REFERENCES fscqi_bundles(id)",
  // FS-1: raw_reference_map artifact
  "ALTER TABLE fscqi_bundles ADD COLUMN raw_reference_map_json TEXT",
  // SI-1: rename rigid_core_path → static_rigid_core_path (DoD spec compliance)
  "ALTER TABLE siat_outputs ADD COLUMN static_rigid_core_path TEXT",
].forEach(sql => { try { db.exec(sql); } catch (_) {} });

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Convert null-prototype rows to plain objects
function row(r)   { if (!r) return r; return Object.assign({}, r); }
function rows(arr){ return arr.map(row); }

const q = {
  get: (sql, ...p) => row(db.prepare(sql).get(...p)),
  all: (sql, ...p) => rows(db.prepare(sql).all(...p)),
  run: (sql, ...p) => db.prepare(sql).run(...p)
};

// Content-addressed lineage recording (CC-1, CC-2)
// Append-only: each artifact is hashed and stored with its parent hash for chain integrity.
function recordLineageEvent(scanId, artifactType, contentJson, parentHash, storagePath) {
  const contentStr = typeof contentJson === 'string' ? contentJson : JSON.stringify(contentJson);
  const contentHash = crypto.createHash('sha256').update(contentStr).digest('hex');
  const byteSize = Buffer.byteLength(contentStr, 'utf8');

  // artifact_versions — content-addressed manifest (CC-2)
  try {
    db.prepare(`
      INSERT INTO artifact_versions
        (scan_id, artifact_type, content_hash, storage_path, byte_size, parent_hash)
      VALUES (?,?,?,?,?,?)`).run(
      scanId, artifactType, contentHash, storagePath || '', byteSize, parentHash || null
    );
  } catch (err) {
    // Duplicate hash = same artifact (idempotent, fine to ignore)
    if (!err.message.includes('UNIQUE')) throw err;
  }

  // lineage_events — append-only event log (CC-1)
  db.prepare(`
    INSERT INTO lineage_events
      (scan_id, event_type, payload_json, parent_artifact_hash, new_artifact_hash)
    VALUES (?,?,?,?,?)`).run(
    scanId, `CREATE_${artifactType.toUpperCase()}`, contentStr, parentHash || null, contentHash
  );

  return contentHash;
}

// Get the most recent artifact hash for a given scan and type (for parent chain)
function getLastArtifactHash(scanId, artifactType) {
  const row = q.get(
    'SELECT content_hash FROM artifact_versions WHERE scan_id=? AND artifact_type=? ORDER BY id DESC LIMIT 1',
    scanId, artifactType
  );
  return row ? row.content_hash : null;
}

// Map canonical pipeline status → legacy SPA-compat status
// The React SPA checks `status === "ready"` / `"processing"` (lowercase) when deciding
// to mount the 3D viewer. Capitalized legacy values silently fail that check, so every
// pipeline state is normalized to its lowercase SPA equivalent here.
const STATUS_TO_LEGACY = {
  'CREATED':         'processing',
  'CAPTURING':       'processing',
  'UPLOADING':       'processing',
  'VIDEO_UPLOADED':  'processing',
  'EXTRACTING_KEYFRAMES': 'processing',
  'FRAME_QA':        'processing',
  'AWAITING_TARGET': 'processing',
  'MASKING':         'processing',
  'RECONSTRUCTING':  'processing',
  'POST_PROCESSING': 'processing',
  'COMPLETED':       'ready',
  'FAILED':          'failed',
  // pass-through for legacy capitalized values already stored in DB
  'Processing': 'processing',
  'Ready':      'ready',
  'Archived':   'archived',
  'Failed':     'failed'
};

// Failure classification — workers set failureClass via status update
const FAILURE_CLASSES = {
  'BLUR_TOO_HIGH':       { retake: true,  guidance: 'Too many blurry frames. Hold the phone steadier and ensure good lighting.' },
  'TOO_FEW_FRAMES':      { retake: true,  guidance: 'Not enough usable frames. Complete the full capture orbit around the target.' },
  'RECONSTRUCTION_FAIL': { retake: true,  guidance: 'Reconstruction failed. Try capturing from more angles with more overlap.' },
  'SAM_DRIFT':           { retake: true,  guidance: 'Mask tracking lost the target. Recapture with slower, steadier movement.' },
  'MASK_LOW_CONFIDENCE':  { retake: true,  guidance: 'Segmentation confidence too low. Try adding more positive/negative points in the prompt UI.' },
  'INTERNAL_ERROR':       { retake: false, guidance: 'An internal processing error occurred. Contact support.' },
};

function normalizeScanForSpa(scan) {
  if (!scan) return scan;
  const pipelineStatus = scan.status;
  const legacyStatus   = STATUS_TO_LEGACY[scan.status] || scan.status;
  const out = { ...scan, pipelineStatus, status: legacyStatus };
  // Parse failure classification from stats for retake guidance
  if (pipelineStatus === 'FAILED' && scan.stats) {
    try {
      const s = JSON.parse(scan.stats);
      if (s.failureClass) {
        out.failureClass = s.failureClass;
        out.retakeAllowed = s.retake !== false;
        out.retakeGuidance = s.guidance || null;
      }
    } catch (_) {}
  }
  return out;
}
function normalizeScansArray(arr) { return arr.map(normalizeScanForSpa); }

// Write a log entry for a scan (capped at 500 rows per scan)
function scanLog(scan_id, stage, message, level = 'info') {
  try {
    q.run('INSERT INTO scan_logs (scan_id, stage, level, message) VALUES (?, ?, ?, ?)',
      scan_id, stage, level, message);
    const cnt = q.get('SELECT COUNT(*) AS c FROM scan_logs WHERE scan_id = ?', scan_id).c;
    if (cnt > 500) {
      q.run('DELETE FROM scan_logs WHERE scan_id = ? AND id IN (SELECT id FROM scan_logs WHERE scan_id = ? ORDER BY id ASC LIMIT ?)',
        scan_id, scan_id, cnt - 500);
    }
  } catch (_) {}
}

// ---------------------------------------------------------------------------
// Multer — general uploads (model files, photos, designs)
// ---------------------------------------------------------------------------
const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, UPLOADS_DIR),
  filename:    (req, file, cb) => {
    const ext = path.extname(file.originalname).toLowerCase();
    cb(null, `${uuidv4()}${ext}`);
  }
});
const upload = multer({ storage, limits: { fileSize: 100 * 1024 * 1024 } });

// Multer — frame uploads (token-authenticated, no session header)
const framesStorage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, FRAMES_DIR),
  filename:    (req, file, cb) => cb(null, `${uuidv4()}.jpg`)
});
// masksStorage/masksUpload removed — mask_worker.py writes files locally, routes accept JSON body

// Multer — video uploads (auth-gated, single mp4/mov, large file size)
const VIDEO_MAX_BYTES = parseInt(process.env.VIDEO_MAX_BYTES) || 500 * 1024 * 1024; // 500MB default
const ALLOWED_VIDEO_EXTS = new Set(['.mp4', '.mov', '.m4v']);
const videoStorage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, VIDEOS_DIR),
  // Filename is finalised in the route handler once we know scanId; use a temp uuid here
  filename:    (req, file, cb) => {
    const ext = path.extname(file.originalname).toLowerCase() || '.mp4';
    cb(null, `pending-${uuidv4()}${ext}`);
  }
});
const videoUpload = multer({
  storage: videoStorage,
  limits: { fileSize: VIDEO_MAX_BYTES },
  fileFilter: (req, file, cb) => {
    const ext = path.extname(file.originalname).toLowerCase();
    if (!ALLOWED_VIDEO_EXTS.has(ext)) return cb(new Error(`Unsupported video extension: ${ext}`));
    cb(null, true);
  }
});

// ---------------------------------------------------------------------------
// Middleware
// ---------------------------------------------------------------------------

// Strip Perplexity /port/NNNN path prefix
app.use((req, res, next) => {
  if (req.path.startsWith('/port/')) req.url = req.url.replace(/^\/port\/\d+/, '');
  next();
});

app.use(cors({
  origin: true,
  methods: ['GET','POST','PATCH','PUT','DELETE','OPTIONS'],
  allowedHeaders: ['Content-Type','X-Visitor-Id','X-Session-Id','X-Internal-Secret'],
  credentials: false
}));
app.options('*', (req, res) => res.sendStatus(204));
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use('/uploads', express.static(UPLOADS_DIR));

// ---------------------------------------------------------------------------
// Auth helpers
// ---------------------------------------------------------------------------
function getVisitorId(req) {
  return req.headers['x-visitor-id'] || req.headers['x-session-id'] || null;
}
function getSessionUser(req) {
  const visitorId = getVisitorId(req);
  if (!visitorId) return null;
  const session = q.get('SELECT * FROM sessions WHERE visitor_id = ?', visitorId);
  if (!session)  return null;
  return q.get('SELECT id,username,displayName,role,specialization FROM users WHERE id = ?', session.user_id);
}
function requireAuth(req, res, next) {
  const user = getSessionUser(req);
  if (!user) return res.status(401).json({ message: 'Unauthorized' });
  req.user = user;
  next();
}
function requireInternal(req, res, next) {
  if (req.headers['x-internal-secret'] !== INTERNAL_SECRET)
    return res.status(401).json({ message: 'Unauthorized' });
  next();
}

// Audit logging (CC-10) — all significant actions logged to audit_log
function auditAction(operatorId, action, targetScanId, targetArtifactType, targetArtifactHash, metadata) {
  try {
    db.prepare(`
      INSERT INTO audit_log
        (operator_id, action, target_scan_id, target_artifact_type, target_artifact_hash, metadata_json)
      VALUES (?,?,?,?,?,?)`).run(
      operatorId || 0,
      action,
      targetScanId || null,
      targetArtifactType || null,
      targetArtifactHash || null,
      JSON.stringify(metadata || {})
    );
  } catch (err) {
    // Non-fatal: don't fail the request if audit fails
    console.error('[audit] failed to log:', err.message);
  }
}

// Role-scoped access middleware stub (CC-11)
// Operator roles: 'admin', 'operator', 'viewer', 'device'
// x-operator-id and x-operator-role headers must be present for scoped routes
function requireOperator(req, res, next) {
  const operatorId = parseInt(req.headers['x-operator-id'] || '0');
  const role = req.headers['x-operator-role'] || 'viewer';
  if (!operatorId)
    return res.status(401).json({ message: 'x-operator-id header required' });
  req.operatorId = operatorId;
  req.operatorRole = role;
  next();
}

// ---------------------------------------------------------------------------
// Routes — Health
// ---------------------------------------------------------------------------
app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

// ---------------------------------------------------------------------------
// Routes — Auth
// ---------------------------------------------------------------------------
const VALID_ROLES           = ['professional', 'client'];
const VALID_SPECIALIZATIONS = ['Plastic Surgeon', 'Tattoo Artist', 'Other'];

app.post('/api/register', async (req, res) => {
  try {
    const { username, password, displayName, role = 'professional', specialization } = req.body;
    if (!username || !password || !displayName)
      return res.status(400).json({ message: 'username, password and displayName are required' });
    if (!VALID_ROLES.includes(role))
      return res.status(400).json({ message: `role must be one of: ${VALID_ROLES.join(', ')}` });
    if (q.get('SELECT id FROM users WHERE username = ?', username))
      return res.status(400).json({ message: 'Username already taken' });
    const hashed = await bcrypt.hash(password, 10);
    const result = q.run(
      'INSERT INTO users (username,password,displayName,role,specialization) VALUES (?,?,?,?,?)',
      username, hashed, displayName, role, specialization || null
    );
    const user      = { id: Number(result.lastInsertRowid), username, displayName, role, specialization };
    const visitorId = getVisitorId(req) || uuidv4();
    q.run('INSERT OR REPLACE INTO sessions (id,visitor_id,user_id) VALUES (?,?,?)', uuidv4(), visitorId, user.id);
    res.status(201).json(user);
  } catch (err) { res.status(500).json({ message: err.message }); }
});

app.post('/api/login', async (req, res) => {
  try {
    const { username, password } = req.body;
    if (!username || !password) return res.status(400).json({ message: 'username and password required' });
    const userRow = q.get('SELECT * FROM users WHERE username = ?', username);
    if (!userRow) return res.status(401).json({ message: 'Invalid credentials' });
    const valid = await bcrypt.compare(password, userRow.password);
    if (!valid) return res.status(401).json({ message: 'Invalid credentials' });
    const user      = { id: userRow.id, username: userRow.username, displayName: userRow.displayName, role: userRow.role, specialization: userRow.specialization };
    const visitorId = getVisitorId(req) || uuidv4();
    q.run('INSERT OR REPLACE INTO sessions (id,visitor_id,user_id) VALUES (?,?,?)', uuidv4(), visitorId, user.id);
    res.json(user);
  } catch (err) { res.status(500).json({ message: err.message }); }
});

app.post('/api/logout', requireAuth, (req, res) => {
  const visitorId = getVisitorId(req);
  if (visitorId) q.run('DELETE FROM sessions WHERE visitor_id = ?', visitorId);
  res.json({ message: 'Logged out' });
});

app.get('/api/user', requireAuth, (req, res) => res.json(req.user));

// ---------------------------------------------------------------------------
// Routes — Clients
// ---------------------------------------------------------------------------
app.get('/api/clients', requireAuth, (req, res) => {
  res.json(q.all('SELECT * FROM clients WHERE professional_id = ? ORDER BY created_at DESC', req.user.id));
});

app.post('/api/clients', requireAuth, (req, res) => {
  const { firstName, lastName, email, phone, notes } = req.body;
  if (!firstName || !lastName)
    return res.status(400).json({ message: 'firstName and lastName are required' });
  const result = q.run(
    'INSERT INTO clients (professional_id,firstName,lastName,email,phone,notes) VALUES (?,?,?,?,?,?)',
    req.user.id, firstName, lastName, email||null, phone||null, notes||null
  );
  res.status(201).json(q.get('SELECT * FROM clients WHERE id = ?', Number(result.lastInsertRowid)));
});

app.get('/api/clients/:id', requireAuth, (req, res) => {
  const client = q.get('SELECT * FROM clients WHERE id = ? AND professional_id = ?', req.params.id, req.user.id);
  if (!client) return res.status(404).json({ message: 'Client not found' });
  res.json(client);
});

app.patch('/api/clients/:id', requireAuth, (req, res) => {
  const client = q.get('SELECT * FROM clients WHERE id = ? AND professional_id = ?', req.params.id, req.user.id);
  if (!client) return res.status(404).json({ message: 'Client not found' });
  const { firstName, lastName, email, phone, notes } = req.body;
  q.run(
    'UPDATE clients SET firstName=COALESCE(?,firstName),lastName=COALESCE(?,lastName),email=COALESCE(?,email),phone=COALESCE(?,phone),notes=COALESCE(?,notes) WHERE id=?',
    firstName||null, lastName||null, email||null, phone||null, notes||null, req.params.id
  );
  res.json(q.get('SELECT * FROM clients WHERE id = ?', req.params.id));
});

app.delete('/api/clients/:id', requireAuth, (req, res) => {
  const result = q.run('DELETE FROM clients WHERE id = ? AND professional_id = ?', req.params.id, req.user.id);
  if (result.changes === 0) return res.status(404).json({ message: 'Client not found' });
  res.json({ message: 'Deleted' });
});

app.get('/api/clients/:id/scans', requireAuth, (req, res) => {
  if (!q.get('SELECT id FROM clients WHERE id = ? AND professional_id = ?', req.params.id, req.user.id))
    return res.status(404).json({ message: 'Client not found' });
  res.json(normalizeScansArray(
    q.all('SELECT * FROM scans WHERE client_id = ? ORDER BY created_at DESC', req.params.id)
  ));
});

// ---------------------------------------------------------------------------
// Routes — Scans
// ---------------------------------------------------------------------------
const VALID_SCAN_STATUSES = [
  'CREATED','CAPTURING','UPLOADING','VIDEO_UPLOADED','EXTRACTING_KEYFRAMES','FRAME_QA',
  'FSCQI','SIAT','REG',                                    // DoD §6-§8 pipeline states
  'POST_PROCESSING',                                        // DG (Detail Geometry) — alias
  'PHOTOREAL','EDSIM','OQSP','PUBLISHED',                // DoD §9-§12 pipeline states
  'OPERATOR_REVIEW','AWAITING_TARGET','MASKING','RECONSTRUCTING',
  'COMPLETED','FAILED','PURGED',                           // End states + purge
  'Processing','Ready','Archived','Failed'   // legacy — kept for SPA PATCH compat
];

app.get('/api/scans', requireAuth, (req, res) => {
  res.json(normalizeScansArray(
    q.all('SELECT * FROM scans WHERE professional_id = ? ORDER BY created_at DESC', req.user.id)
  ));
});

// IMPORTANT: /recent must come before /:id
app.get('/api/scans/recent', requireAuth, (req, res) => {
  const limit = Math.min(Math.max(1, parseInt(req.query.limit) || 5), 100);
  res.json(normalizeScansArray(
    q.all(`SELECT s.*,c.firstName,c.lastName
           FROM scans s JOIN clients c ON s.client_id=c.id
           WHERE s.professional_id=? ORDER BY s.created_at DESC LIMIT ?`,
      req.user.id, limit)
  ));
});

app.post('/api/scans', requireAuth, (req, res) => {
  const { clientId, title, bodyPart, notes } = req.body;
  if (!clientId || !title || !bodyPart)
    return res.status(400).json({ message: 'clientId, title and bodyPart are required' });
  if (!q.get('SELECT id FROM clients WHERE id = ? AND professional_id = ?', clientId, req.user.id))
    return res.status(404).json({ message: 'Client not found' });
  const result = q.run(
    'INSERT INTO scans (client_id,professional_id,title,bodyPart,notes,status) VALUES (?,?,?,?,?,?)',
    clientId, req.user.id, title, bodyPart, notes||null, 'CREATED'
  );
  const scanId = Number(result.lastInsertRowid);
  auditAction(req.user.id, 'SCAN_CREATED', scanId, null, null, { bodyPart, title });
  res.status(201).json(normalizeScanForSpa(
    q.get('SELECT * FROM scans WHERE id = ?', scanId)
  ));
});

app.get('/api/scans/:id', requireAuth, (req, res) => {
  const scan = q.get('SELECT * FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id);
  if (!scan) return res.status(404).json({ message: 'Scan not found' });
  res.json(normalizeScanForSpa(scan));
});

app.patch('/api/scans/:id', requireAuth, (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id))
    return res.status(404).json({ message: 'Scan not found' });
  const { title, bodyPart, status, notes } = req.body;
  if (status && !VALID_SCAN_STATUSES.includes(status))
    return res.status(400).json({ message: `status must be one of: ${VALID_SCAN_STATUSES.join(', ')}` });
  q.run(
    'UPDATE scans SET title=COALESCE(?,title),bodyPart=COALESCE(?,bodyPart),status=COALESCE(?,status),notes=COALESCE(?,notes) WHERE id=?',
    title||null, bodyPart||null, status||null, notes||null, req.params.id
  );
  if (status) auditAction(req.user.id, 'SCAN_STATUS_CHANGED', parseInt(req.params.id), null, null, { newStatus: status });
  res.json(normalizeScanForSpa(q.get('SELECT * FROM scans WHERE id = ?', req.params.id)));
});

app.delete('/api/scans/:id', requireAuth, (req, res) => {
  const result = q.run('DELETE FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id);
  if (result.changes === 0) return res.status(404).json({ message: 'Scan not found' });
  auditAction(req.user.id, 'SCAN_DELETED', parseInt(req.params.id), null, null, {});
  res.json({ message: 'Deleted' });
});

app.post('/api/scans/:id/upload', requireAuth, upload.single('model'), (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id))
    return res.status(404).json({ message: 'Scan not found' });
  if (!req.file) return res.status(400).json({ message: 'No file uploaded' });
  const modelUrl = `/uploads/${req.file.filename}`;
  q.run("UPDATE scans SET modelUrl=?,status='COMPLETED' WHERE id=?", modelUrl, req.params.id);
  res.json(normalizeScanForSpa(q.get('SELECT * FROM scans WHERE id = ?', req.params.id)));
});

// GET pipeline status — lightweight polling endpoint
app.get('/api/scans/:id/status', requireAuth, (req, res) => {
  const scan = q.get('SELECT id,status,errorMessage FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id);
  if (!scan) return res.status(404).json({ message: 'Scan not found' });
  res.json({ id: scan.id, pipelineStatus: scan.status, errorMessage: scan.errorMessage || null });
});

// GET processing log
app.get('/api/scans/:id/processing-log', requireAuth, (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id))
    return res.status(404).json({ message: 'Scan not found' });
  res.json(q.all('SELECT * FROM scan_logs WHERE scan_id = ? ORDER BY created_at DESC LIMIT 50', req.params.id));
});

// ---------------------------------------------------------------------------
// Routes — Video upload (alternate capture path: native phone camera → backend)
// ---------------------------------------------------------------------------

// POST /api/video-upload
// Body (multipart):
//   video      — single mp4/mov/m4v file (required)
//   clientId   — existing client id (required)
//   title      — scan title (required)
//   bodyPart   — scan body part (required)
//   notes      — optional
// Creates a scan in VIDEO_UPLOADED state and renames the uploaded file to
// uploads/videos/<scanId>.<ext> so the worker can find it deterministically.
app.post('/api/video-upload', requireAuth, (req, res, next) => {
  videoUpload.single('video')(req, res, (err) => {
    if (err) {
      // multer errors (file too large, bad extension)
      const code = err.code === 'LIMIT_FILE_SIZE' ? 413 : 400;
      return res.status(code).json({ message: err.message });
    }
    next();
  });
}, (req, res) => {
  const cleanup = () => { if (req.file && fs.existsSync(req.file.path)) { try { fs.unlinkSync(req.file.path); } catch (_) {} } };

  if (!req.file)               { return res.status(400).json({ message: 'video file required' }); }
  const { clientId, title, bodyPart, notes } = req.body;
  if (!clientId || !title || !bodyPart) {
    cleanup();
    return res.status(400).json({ message: 'clientId, title and bodyPart are required' });
  }
  if (!q.get('SELECT id FROM clients WHERE id = ? AND professional_id = ?', clientId, req.user.id)) {
    cleanup();
    return res.status(404).json({ message: 'Client not found' });
  }

  const result = q.run(
    'INSERT INTO scans (client_id,professional_id,title,bodyPart,notes,status) VALUES (?,?,?,?,?,?)',
    clientId, req.user.id, title, bodyPart, notes || null, 'VIDEO_UPLOADED'
  );
  const scanId = Number(result.lastInsertRowid);

  // Rename pending-<uuid>.ext to <scanId>.ext so the worker can glob it.
  const ext       = path.extname(req.file.filename).toLowerCase() || '.mp4';
  const finalName = `${scanId}${ext}`;
  const finalPath = path.join(VIDEOS_DIR, finalName);
  try {
    fs.renameSync(req.file.path, finalPath);
  } catch (e) {
    // Roll back the scan row — disk move failed
    q.run('DELETE FROM scans WHERE id = ?', scanId);
    cleanup();
    return res.status(500).json({ message: `Failed to store video: ${e.message}` });
  }

  scanLog(scanId, 'video_upload', `video stored: ${finalName} (${req.file.size} bytes)`);
  res.status(201).json(normalizeScanForSpa(q.get('SELECT * FROM scans WHERE id = ?', scanId)));
});

// ---------------------------------------------------------------------------
// Routes — Capture session (creates token for phone-side upload)
// ---------------------------------------------------------------------------
app.post('/api/scans/:id/capture-session', requireAuth, (req, res) => {
  const scan = q.get('SELECT * FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id);
  if (!scan) return res.status(404).json({ message: 'Scan not found' });

  // If a session already exists for this scan, reuse it if not expired
  // expires_at may be NULL on rows migrated from schema v1 — treat NULL as expired
  const existing = q.get(
    "SELECT * FROM capture_sessions WHERE scan_id = ? AND expires_at IS NOT NULL AND expires_at > datetime('now')",
    req.params.id
  );
  if (existing) {
    const captureUrl = buildCaptureUrl(req, existing.token, req.params.id);
    return res.json({ sessionId: existing.id, token: existing.token, captureUrl, expiresAt: existing.expires_at });
  }

  const token     = uuidv4();
  const sessionId = uuidv4();
  // 4-hour expiry
  q.run(
    "INSERT INTO capture_sessions (id,scan_id,token,expires_at) VALUES (?,?,?,datetime('now','+4 hours'))",
    sessionId, req.params.id, token
  );
  // Move scan to CAPTURING → UPLOADING once frames start arriving
  q.run("UPDATE scans SET status='CAPTURING' WHERE id=?", req.params.id);

  const captureUrl = buildCaptureUrl(req, token, req.params.id);
  const session = q.get('SELECT * FROM capture_sessions WHERE id = ?', sessionId);
  res.json({ sessionId, token, captureUrl, expiresAt: session.expires_at });
});

function getNetworkIp() {
  // First try native interfaces (works on bare metal / macOS / Linux)
  const nets = os.networkInterfaces();
  for (const iface of Object.values(nets)) {
    for (const cfg of iface) {
      if (cfg.family === 'IPv4' && !cfg.internal &&
          !cfg.address.startsWith('172.') &&
          !cfg.address.startsWith('100.') &&
          !cfg.address.startsWith('192.168.56.')) {
        return cfg.address;
      }
    }
  }
  // WSL2 fallback: get Windows host IP via ipconfig.exe
  try {
    const { execSync } = require('child_process');
    const out = execSync('ipconfig.exe', { encoding: 'utf8', timeout: 3000 });
    const ips = [...out.matchAll(/IPv4[^:]*:\s*([\d.]+)/g)].map(m => m[1]);
    // Filter out VirtualBox (192.168.56.x), Docker/WSL (172.x), Tailscale (100.x)
    return ips.find(ip => !ip.startsWith('172.') && !ip.startsWith('192.168.56.') && !ip.startsWith('100.')) || ips[0] || null;
  } catch { return null; }
}

function buildCaptureUrl(req, token, scanId) {
  // Use CAPTURE_BASE_URL env var in production to avoid host-header poisoning.
  if (process.env.CAPTURE_BASE_URL) {
    return `${process.env.CAPTURE_BASE_URL}/capture.html?token=${token}&scan=${scanId}`;
  }
  // Dev: use LAN IP so phone can reach the server (localhost won't work from phone)
  let host = req.headers.host || `localhost:${PORT}`;
  if (host.startsWith('localhost') || host.startsWith('127.0.0.1')) {
    const lanIp = getNetworkIp();
    if (lanIp) host = `${lanIp}:${PORT}`;
  }
  const proto = req.secure ? 'https' : 'http';
  return `${proto}://${host}/capture.html?token=${token}&scan=${scanId}`;
}

// ---------------------------------------------------------------------------
// Routes — Token-authenticated capture endpoints (no session header)
// ---------------------------------------------------------------------------

// Validate a capture token (called by the PWA/app on launch)
app.get('/api/capture/validate', (req, res) => {
  const { token } = req.query;
  if (!token) return res.status(400).json({ message: 'token required' });
  const session = q.get(
    "SELECT cs.*,s.bodyPart,s.title AS scanTitle FROM capture_sessions cs JOIN scans s ON cs.scan_id=s.id WHERE cs.token=? AND cs.expires_at IS NOT NULL AND cs.expires_at > datetime('now')",
    token
  );
  if (!session) return res.status(401).json({ valid: false, message: 'Invalid or expired token' });
  res.json({ valid: true, scanId: session.scan_id, scanTitle: session.scanTitle || null, bodyPart: session.bodyPart, expiresAt: session.expires_at });
});

// Receive frame upload from PWA (one at a time, field 'frame') or Android bulk (field 'frames')
// Does NOT transition to FRAME_QA — caller must POST /api/capture/finalize when all frames sent.
const framesUploadAny = multer({ storage: framesStorage, limits: { fileSize: 15 * 1024 * 1024 } }).any();

app.post('/api/capture/frames', framesUploadAny, (req, res) => {
  const { token, manifest } = req.body;

  if (!token) {
    if (req.files) req.files.forEach(f => { try { fs.unlinkSync(f.path); } catch(_){} });
    return res.status(400).json({ message: 'token required' });
  }

  const session = q.get(
    "SELECT * FROM capture_sessions WHERE token=? AND expires_at IS NOT NULL AND expires_at > datetime('now')",
    token
  );
  if (!session) {
    if (req.files) req.files.forEach(f => { try { fs.unlinkSync(f.path); } catch(_){} });
    return res.status(401).json({ message: 'Invalid or expired capture token' });
  }

  if (!req.files || req.files.length === 0) {
    return res.status(400).json({ message: 'No frames uploaded' });
  }

  const scanId = session.scan_id;

  // Use frameIndex from body for sort order; fall back to current frame count
  const baseIdx = (() => {
    const n = parseInt(req.body.frameIndex);
    if (!isNaN(n)) return n;
    return q.get('SELECT COUNT(*) as c FROM scan_frames WHERE scan_id=?', scanId).c;
  })();

  const insertFrame = db.prepare(
    'INSERT INTO scan_frames (scan_id,frameUrl,sortOrder) VALUES (?,?,?)'
  );
  req.files.forEach((file, i) => {
    insertFrame.run(scanId, `/uploads/frames/${file.filename}`, baseIdx + i);
  });

  // MB-1: compute SHA-256 content hash for each uploaded frame and store in scan_frames
  const updateHash = db.prepare('UPDATE scan_frames SET content_hash=? WHERE frameUrl=? AND scan_id=?');
  req.files.forEach(file => {
    const fileBuf = fs.readFileSync(file.path);
    const hash = crypto.createHash('sha256').update(fileBuf).digest('hex');
    updateHash.run(hash, `/uploads/frames/${file.filename}`, scanId);
  });

  // MB-7 / CX-7 / CX-6: track intervention counters from client telemetry
  // auto_mode_count = auto-capture triggers, manual_shutter_count = manual shutter clicks,
  // retry_count = retry events (e.g. WORTH_PATCH -> new capture)
  const autoModeCount = parseInt(req.body.autoModeCount) || 0;
  const manualShutterCount = parseInt(req.body.manualShutterCount) || 0;
  const retryCount = parseInt(req.body.retryCount) || 0;
  if (autoModeCount > 0 || manualShutterCount > 0 || retryCount > 0) {
    q.run(
      `UPDATE capture_metadata SET
        auto_mode_count = COALESCE(auto_mode_count, 0) + ?,
        manual_shutter_count = COALESCE(manual_shutter_count, 0) + ?,
        retry_count = COALESCE(retry_count, 0) + ?
      WHERE scan_id = ?`,
      autoModeCount, manualShutterCount, retryCount, scanId
    );
  }

  // Store manifest if provided (on first frame only — identified by frameIndex === 0 or first upload)
  if (manifest) {
    try {
      const m = typeof manifest === 'string' ? JSON.parse(manifest) : manifest;
      q.run(
        'INSERT OR REPLACE INTO capture_metadata (scan_id,deviceModel,frameCount,bodyPart,manifestJson) VALUES (?,?,?,?,?)',
        scanId, m.deviceModel||null, m.frameCount||req.files.length, m.bodyPart||null, JSON.stringify(m)
      );
    } catch (_) {}
  }

  // Transition CAPTURING → UPLOADING on first frame; stay UPLOADING until /finalize
  const currentScan = q.get('SELECT status FROM scans WHERE id=?', scanId);
  if (currentScan && currentScan.status === 'CAPTURING') {
    q.run("UPDATE scans SET status='UPLOADING' WHERE id=?", scanId);
  }
  const totalSoFar = q.get('SELECT COUNT(*) as c FROM scan_frames WHERE scan_id=?', scanId).c;
  scanLog(scanId, 'frame_upload', `+${req.files.length} frames (total: ${totalSoFar})`);
  res.json({ framesAccepted: req.files.length, totalFrames: totalSoFar, scanId, status: 'UPLOADING' });
});

// Finalize capture: transitions UPLOADING → FRAME_QA (triggers pipeline worker)
// Accepts token from JSON body, URL-encoded body, query param, or multipart
app.post('/api/capture/finalize', multer().none(), (req, res) => {
  const token = req.body?.token || req.query?.token;
  if (!token) return res.status(400).json({ message: 'token required' });
  const session = q.get(
    "SELECT * FROM capture_sessions WHERE token=? AND expires_at IS NOT NULL AND expires_at > datetime('now')",
    token
  );
  if (!session) return res.status(401).json({ message: 'Invalid or expired capture token' });
  const scanId = session.scan_id;
  const scan = q.get('SELECT * FROM scans WHERE id=?', scanId);
  if (!scan) return res.status(404).json({ message: 'Scan not found' });
  if (!['UPLOADING','CREATED'].includes(scan.status))
    return res.status(409).json({ message: `Scan is already in ${scan.status} state` });
  const frameCount = q.get('SELECT COUNT(*) as c FROM scan_frames WHERE scan_id=?', scanId).c;
  if (frameCount === 0) return res.status(400).json({ message: 'No frames uploaded yet' });

  // MB-2 / MB-3: compute Merkle root from all frame content hashes (in sortOrder)
  const allFrames = q.all(
    'SELECT content_hash FROM scan_frames WHERE scan_id=? ORDER BY sortOrder ASC',
    scanId
  );
  const leafHashes = allFrames.map(r => r.content_hash).filter(h => h);
  let merkleRoot = null;
  if (leafHashes.length > 0) {
    // Build Merkle tree bottom-up: pairwise hash, repeat until one root remains
    let level = leafHashes.slice();
    while (level.length > 1) {
      const nextLevel = [];
      for (let i = 0; i < level.length; i += 2) {
        const left = level[i];
        const right = level[i + 1] || left; // duplicate last if odd
        nextLevel.push(crypto.createHash('sha256').update(left + right).digest('hex'));
      }
      level = nextLevel;
    }
    merkleRoot = level[0];
    // Store in capture_metadata for integrity proof
    q.run('UPDATE capture_metadata SET merkle_root=? WHERE scan_id=?', merkleRoot, scanId);
  }

  q.run("UPDATE scans SET status='FRAME_QA' WHERE id=?", scanId);
  scanLog(scanId, 'capture_finalize', `capture finalized, ${frameCount} frames → FRAME_QA`);
  res.json({ scanId, frameCount, status: 'FRAME_QA', merkleRoot });
});

// MB-9: Get mobile bundle integrity proof for capture review gate
// Returns merkle_root + frame list with content hashes for operator verification
app.get('/api/capture/bundle/:scanId', (req, res) => {
  const scanId = parseInt(req.params.scanId);
  if (!scanId) return res.status(400).json({ message: 'scanId required' });

  const meta = q.get('SELECT merkle_root, auto_mode_count, manual_shutter_count, retry_count FROM capture_metadata WHERE scan_id=?', scanId);
  if (!meta) return res.status(404).json({ message: 'Capture metadata not found' });

  const frames = q.all(
    'SELECT id, sortOrder, content_hash, blurScore FROM scan_frames WHERE scan_id=? ORDER BY sortOrder ASC',
    scanId
  );

  res.json({
    scanId,
    merkleRoot: meta.merkle_root,
    frameCount: frames.length,
    frameHashes: frames.map(f => ({ id: f.id, sortOrder: f.sortOrder, contentHash: f.content_hash })),
    interventionCounters: {
      autoModeCount: meta.auto_mode_count || 0,
      manualShutterCount: meta.manual_shutter_count || 0,
      retryCount: meta.retry_count || 0
    }
  });
});

// ---------------------------------------------------------------------------
// Routes — Anchor frame nomination & prompting
// ---------------------------------------------------------------------------

// Manually nominate anchor frames (also called by frame_qa.py worker)
app.post('/api/scans/:id/anchor-frames', requireAuth, (req, res) => {
  const scan = q.get('SELECT * FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id);
  if (!scan) return res.status(404).json({ message: 'Scan not found' });

  const { frameIds } = req.body;
  if (!Array.isArray(frameIds) || frameIds.length === 0 || frameIds.length > 3)
    return res.status(400).json({ message: 'frameIds must be array of 1–3 IDs' });

  // Verify frames belong to this scan
  const valid = frameIds.every(fid =>
    q.get('SELECT id FROM scan_frames WHERE id = ? AND scan_id = ?', fid, req.params.id)
  );
  if (!valid) return res.status(400).json({ message: 'One or more frameIds not found for this scan' });

  // Reset all anchors, then set selected ones
  q.run('UPDATE scan_frames SET isAnchor=0 WHERE scan_id=?', req.params.id);
  const setAnchor = db.prepare('UPDATE scan_frames SET isAnchor=1 WHERE id=?');
  frameIds.forEach(fid => setAnchor.run(fid));

  q.run("UPDATE scans SET status='AWAITING_TARGET' WHERE id=?", req.params.id);
  scanLog(req.params.id, 'anchor_select', `anchors set: ${frameIds.join(',')}`);

  const anchors = q.all('SELECT * FROM scan_frames WHERE scan_id=? AND isAnchor=1', req.params.id);
  res.json({ scan: normalizeScanForSpa(q.get('SELECT * FROM scans WHERE id=?', req.params.id)), anchors });
});

// Return anchor frames for the prompting UI
app.get('/api/scans/:id/anchor-frames', requireAuth, (req, res) => {
  const scan = q.get('SELECT * FROM scans WHERE id=? AND professional_id=?', req.params.id, req.user.id);
  if (!scan) return res.status(404).json({ message: 'Scan not found' });
  const anchors = q.all('SELECT * FROM scan_frames WHERE scan_id=? AND isAnchor=1 ORDER BY sortOrder ASC', req.params.id);
  res.json({ scan: normalizeScanForSpa(scan), anchors });
});

// Save multi-anchor prompt and transition to MASKING
app.post('/api/scans/:id/prompt', requireAuth, (req, res) => {
  const scan = q.get('SELECT * FROM scans WHERE id=? AND professional_id=?', req.params.id, req.user.id);
  if (!scan) return res.status(404).json({ message: 'Scan not found' });
  if (!['AWAITING_TARGET', 'MASKING'].includes(scan.status))
    return res.status(409).json({ message: 'Scan must be in AWAITING_TARGET state' });

  const { anchors } = req.body;
  // Allow empty anchors array — workers fall back to automatic segmentation
  if (!Array.isArray(anchors))
    return res.status(400).json({ message: 'anchors must be an array' });

  q.run(
    'INSERT OR REPLACE INTO scan_prompts (scan_id,promptJson) VALUES (?,?)',
    req.params.id, JSON.stringify({ anchors })
  );
  q.run("UPDATE scans SET status='MASKING' WHERE id=?", req.params.id);
  scanLog(req.params.id, 'prompt', `prompt saved, ${anchors.length} anchor(s)`);

  const prompt = q.get('SELECT * FROM scan_prompts WHERE scan_id=?', req.params.id);
  res.json({ promptId: prompt.id, status: 'MASKING' });
});

// ---------------------------------------------------------------------------
// Routes — Scan Photos (unchanged)
// ---------------------------------------------------------------------------
app.get('/api/scans/:id/photos', requireAuth, (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id=? AND professional_id=?', req.params.id, req.user.id))
    return res.status(404).json({ message: 'Scan not found' });
  res.json(q.all('SELECT * FROM scan_photos WHERE scan_id=? ORDER BY sortOrder ASC', req.params.id));
});

app.post('/api/scans/:id/photos', requireAuth, upload.array('photos', 50), (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id=? AND professional_id=?', req.params.id, req.user.id))
    return res.status(404).json({ message: 'Scan not found' });
  if (!req.files || req.files.length === 0)
    return res.status(400).json({ message: 'No files uploaded' });
  const existing = q.get('SELECT COUNT(*) as cnt FROM scan_photos WHERE scan_id=?', req.params.id);
  const insertPhoto = db.prepare('INSERT INTO scan_photos (scan_id,photoUrl,sortOrder) VALUES (?,?,?)');
  req.files.forEach((file, i) => {
    insertPhoto.run(req.params.id, `/uploads/${file.filename}`, existing.cnt + i);
  });
  res.status(201).json(q.all('SELECT * FROM scan_photos WHERE scan_id=? ORDER BY sortOrder ASC', req.params.id));
});

app.delete('/api/scans/:id/photos/:photoId', requireAuth, (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id=? AND professional_id=?', req.params.id, req.user.id))
    return res.status(404).json({ message: 'Scan not found' });
  const result = q.run('DELETE FROM scan_photos WHERE id=? AND scan_id=?', req.params.photoId, req.params.id);
  if (result.changes === 0) return res.status(404).json({ message: 'Photo not found' });
  res.json({ message: 'Deleted' });
});

// ---------------------------------------------------------------------------
// Routes — Tattoo Designs (unchanged)
// ---------------------------------------------------------------------------
app.get('/api/tattoo-designs', requireAuth, (req, res) => {
  res.json(q.all('SELECT * FROM tattoo_designs WHERE professional_id=? ORDER BY created_at DESC', req.user.id));
});

app.post('/api/tattoo-designs', requireAuth, upload.single('design'), (req, res) => {
  if (!req.file) return res.status(400).json({ message: 'No file uploaded' });
  const name     = req.body.name || path.parse(req.file.originalname).name;
  const imageUrl = `/uploads/${req.file.filename}`;
  const result = q.run('INSERT INTO tattoo_designs (professional_id,name,imageUrl) VALUES (?,?,?)', req.user.id, name, imageUrl);
  res.status(201).json(q.get('SELECT * FROM tattoo_designs WHERE id=?', Number(result.lastInsertRowid)));
});

app.delete('/api/tattoo-designs/:id', requireAuth, (req, res) => {
  const result = q.run('DELETE FROM tattoo_designs WHERE id=? AND professional_id=?', req.params.id, req.user.id);
  if (result.changes === 0) return res.status(404).json({ message: 'Design not found' });
  res.json({ message: 'Deleted' });
});

// ---------------------------------------------------------------------------
// Routes — Annotations (unchanged)
// ---------------------------------------------------------------------------
app.get('/api/scans/:id/annotations', requireAuth, (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id=? AND professional_id=?', req.params.id, req.user.id))
    return res.status(404).json({ message: 'Scan not found' });
  res.json(q.all('SELECT * FROM scan_annotations WHERE scan_id=? ORDER BY created_at ASC', req.params.id));
});

app.post('/api/scans/:id/annotations', requireAuth, (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id=? AND professional_id=?', req.params.id, req.user.id))
    return res.status(404).json({ message: 'Scan not found' });
  const { posX, posY, posZ, normalX=0, normalY=1, normalZ=0, note, color='#14b8a6' } = req.body;
  if (posX == null || posY == null || posZ == null || !note)
    return res.status(400).json({ message: 'posX, posY, posZ and note are required' });
  const result = q.run(
    'INSERT INTO scan_annotations (scan_id,posX,posY,posZ,normalX,normalY,normalZ,note,color) VALUES (?,?,?,?,?,?,?,?,?)',
    req.params.id, posX, posY, posZ, normalX, normalY, normalZ, note, color
  );
  res.status(201).json(q.get('SELECT * FROM scan_annotations WHERE id=?', Number(result.lastInsertRowid)));
});

app.delete('/api/scans/:id/annotations/:annotationId', requireAuth, (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id=? AND professional_id=?', req.params.id, req.user.id))
    return res.status(404).json({ message: 'Scan not found' });
  const result = q.run('DELETE FROM scan_annotations WHERE id=? AND scan_id=?', req.params.annotationId, req.params.id);
  if (result.changes === 0) return res.status(404).json({ message: 'Annotation not found' });
  res.json({ message: 'Deleted' });
});

// ---------------------------------------------------------------------------
// Routes — Stats
// ---------------------------------------------------------------------------
app.get('/api/stats', requireAuth, (req, res) => {
  const uid = req.user.id;
  res.json({
    totalClients: q.get('SELECT COUNT(*) as cnt FROM clients WHERE professional_id=?', uid).cnt,
    totalScans:   q.get('SELECT COUNT(*) as cnt FROM scans WHERE professional_id=?', uid).cnt,
    totalPhotos:  q.get('SELECT COUNT(*) as cnt FROM scan_photos sp JOIN scans s ON sp.scan_id=s.id WHERE s.professional_id=?', uid).cnt,
    totalModels:  q.get("SELECT COUNT(*) as cnt FROM scans WHERE professional_id=? AND modelUrl IS NOT NULL AND status IN ('COMPLETED','Ready')", uid).cnt
  });
});

// ---------------------------------------------------------------------------
// Routes — Internal worker callbacks (X-Internal-Secret auth)
// ---------------------------------------------------------------------------

// Worker updates scan status / sets modelUrl / sets errorMessage / sets fscqi_bundle_id
app.post('/api/internal/scans/:id/status', requireInternal, (req, res) => {
  const scan = q.get('SELECT * FROM scans WHERE id=?', req.params.id);
  if (!scan) return res.status(404).json({ message: 'Scan not found' });
  const { status, modelUrl, failureClass, fscqi_bundle_id } = req.body;
  // Accept both 'errorMessage' and 'message' from workers
  const errorMessage = req.body.errorMessage || (status === 'FAILED' ? req.body.message : null) || null;
  if (status && !VALID_SCAN_STATUSES.includes(status))
    return res.status(400).json({ message: `Invalid status: ${status}` });
  if (status)       q.run('UPDATE scans SET status=? WHERE id=?', status, req.params.id);
  if (modelUrl)     q.run('UPDATE scans SET modelUrl=? WHERE id=?', modelUrl, req.params.id);
  if (errorMessage) q.run('UPDATE scans SET errorMessage=? WHERE id=?', errorMessage, req.params.id);
  // Store FSCQI bundle FK in capture_metadata (FS-1)
  if (fscqi_bundle_id != null) {
    q.run('UPDATE capture_metadata SET fscqi_bundle_id=? WHERE scan_id=?',
      fscqi_bundle_id, req.params.id);
  }
  // Store failure classification for retake guidance
  if (failureClass && FAILURE_CLASSES[failureClass]) {
    const fc = FAILURE_CLASSES[failureClass];
    q.run('UPDATE scans SET stats=? WHERE id=?',
      JSON.stringify({ failureClass, retake: fc.retake, guidance: fc.guidance }), req.params.id);
  }
  scanLog(req.params.id, 'worker', `status→${status||'(no change)'}${failureClass ? ' class:'+failureClass : ''}${errorMessage ? ' err:'+errorMessage : ''}`);
  if (status) auditAction(0, 'WORKER_STATUS_CHANGE', parseInt(req.params.id), null, null, { from: scan.status, to: status, failureClass, errorMessage });
  res.json(normalizeScanForSpa(q.get('SELECT * FROM scans WHERE id=?', req.params.id)));
});

// Compare-and-swap status transition. Only updates the scan if its current
// status matches `from`. Used by video_worker to claim a VIDEO_UPLOADED scan
// exclusively before doing work — prevents a duplicate-extract race if two
// workers ever fire on the same scan.
//   200 { ok: true, status }    — claimed
//   409 { ok: false, current }  — somebody else got it (or wrong starting state)
app.post('/api/internal/scans/:id/claim-state', requireInternal, (req, res) => {
  const { from, to } = req.body || {};
  if (!from || !to) return res.status(400).json({ message: '`from` and `to` required' });
  if (!VALID_SCAN_STATUSES.includes(from))
    return res.status(400).json({ message: `Invalid source status: ${from}` });
  if (!VALID_SCAN_STATUSES.includes(to))
    return res.status(400).json({ message: `Invalid target status: ${to}` });
  const scan = q.get('SELECT id,status FROM scans WHERE id = ?', req.params.id);
  if (!scan) return res.status(404).json({ message: 'Scan not found' });
  const result = q.run(
    'UPDATE scans SET status = ? WHERE id = ? AND status = ?',
    to, req.params.id, from
  );
  if (result.changes !== 1) {
    const fresh = q.get('SELECT status FROM scans WHERE id = ?', req.params.id);
    return res.status(409).json({ ok: false, current: fresh ? fresh.status : null });
  }
  scanLog(req.params.id, 'claim', `${from} -> ${to}`);
  res.json({ ok: true, status: to });
});

// Worker fetches the source video filename for a scan in VIDEO_UPLOADED state.
// Returns { videoUrl, absPath } so the worker can open the file directly on disk.
app.get('/api/internal/scans/:id/video', requireInternal, (req, res) => {
  const scanId = req.params.id;
  if (!q.get('SELECT id FROM scans WHERE id = ?', scanId))
    return res.status(404).json({ message: 'Scan not found' });
  let match = null;
  try {
    match = fs.readdirSync(VIDEOS_DIR).find(f => {
      const stem = f.replace(/\.[^.]+$/, '');
      return stem === String(scanId);
    });
  } catch (_) {}
  if (!match) return res.status(404).json({ message: 'No video file on disk for this scan' });
  res.json({
    videoUrl: `/uploads/videos/${match}`,
    absPath:  path.join(VIDEOS_DIR, match),
    filename: match
  });
});

// Worker registers extracted keyframes (files already written to FRAMES_DIR locally).
// Body: { frames: [{ frameUrl, blurScore?, sortOrder? }] }
// Returns { framesAdded, totalFrames }
app.post('/api/internal/scans/:id/frames-register', requireInternal, (req, res) => {
  const scanId = req.params.id;
  if (!q.get('SELECT id FROM scans WHERE id = ?', scanId))
    return res.status(404).json({ message: 'Scan not found' });
  const { frames } = req.body;
  if (!Array.isArray(frames) || frames.length === 0)
    return res.status(400).json({ message: 'frames array required' });
  const baseIdx = q.get('SELECT COUNT(*) as c FROM scan_frames WHERE scan_id = ?', scanId).c;
  const insert  = db.prepare(
    'INSERT INTO scan_frames (scan_id,frameUrl,sortOrder,blurScore) VALUES (?,?,?,?)'
  );
  frames.forEach((f, i) => {
    const order = (f.sortOrder != null) ? f.sortOrder : (baseIdx + i);
    insert.run(scanId, f.frameUrl, order, f.blurScore != null ? f.blurScore : null);
  });
  const total = q.get('SELECT COUNT(*) as c FROM scan_frames WHERE scan_id = ?', scanId).c;
  scanLog(scanId, 'video_keyframes', `+${frames.length} keyframes registered (total: ${total})`);
  res.json({ framesAdded: frames.length, totalFrames: total });
});

// Worker fetches frame list
app.get('/api/internal/scans/:id/frames', requireInternal, (req, res) => {
  const anchorsOnly = req.query.anchorsOnly === '1' || req.query.anchorsOnly === 'true';
  const sql = anchorsOnly
    ? 'SELECT * FROM scan_frames WHERE scan_id=? AND isAnchor=1 ORDER BY sortOrder ASC'
    : 'SELECT * FROM scan_frames WHERE scan_id=? ORDER BY sortOrder ASC';
  res.json(q.all(sql, req.params.id));
});

// Worker updates blur scores per frame
// Accepts { frameScores: [{frameId, blurScore, accepted}] }
// Also accepts { scores: [{frameId, score}] } (frame_qa.py compact format)
app.post('/api/internal/scans/:id/frame-scores', requireInternal, (req, res) => {
  const raw = req.body.frameScores || req.body.scores;
  if (!Array.isArray(raw))
    return res.status(400).json({ message: 'frameScores (or scores) array required' });
  const update = db.prepare('UPDATE scan_frames SET blurScore=?,accepted=? WHERE id=? AND scan_id=?');
  raw.forEach(entry => {
    const frameId   = entry.frameId;
    const blurScore = entry.blurScore != null ? entry.blurScore : (entry.score != null ? entry.score : null);
    const accepted  = entry.accepted != null ? (entry.accepted ? 1 : 0) : 1;
    update.run(blurScore, accepted, frameId, req.params.id);
  });
  res.json({ updated: raw.length });
});

// Worker creates FSCQI bundle — FS-1 six artifacts + four-state verdict
app.post('/api/internal/scans/:id/fscqi-bundle', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const {
    bundle_version, verdict, primary_tier, candidate_tier,
    raw_reference_map, coverage_descriptor, weak_regions, health_summary
  } = req.body;

  if (!verdict || !primary_tier || !coverage_descriptor || !health_summary)
    return res.status(400).json({ message: 'verdict, primary_tier, coverage_descriptor, health_summary required' });

  const valid_verbs = ['PROCESS_CLEAN', 'PROCESS_WITH_FLAGS', 'REVIEW_NEEDED', 'RETRY_RECOMMENDED'];
  if (!valid_verbs.includes(verdict))
    return res.status(400).json({ message: `verdict must be one of: ${valid_verbs.join(', ')}` });

  const insert = db.prepare(`
    INSERT INTO fscqi_bundles
      (scan_id, bundle_version, verdict, primary_tier_json, candidate_tier_json,
       raw_reference_map_json, coverage_descriptor_json, weak_region_json, health_summary_json)
    VALUES (?,?,?,?,?,?,?,?,?)`);

  const result = insert.run(
    scanId,
    bundle_version || '1.0.0',
    verdict,
    JSON.stringify(primary_tier),
    JSON.stringify(candidate_tier || []),
    JSON.stringify(raw_reference_map || {}),
    JSON.stringify(coverage_descriptor),
    JSON.stringify(weak_regions || []),
    JSON.stringify(health_summary)
  );

  // Also store fscqi_bundle_id in capture_metadata
  q.run('UPDATE capture_metadata SET fscqi_bundle_id=? WHERE scan_id=?',
    result.lastInsertRowid, scanId);

  // Record lineage event (FSCQI is the first artifact — no parent)
  const fscqiContent = { verdict, health_summary, coverage_descriptor, primary_tier, raw_reference_map };
  recordLineageEvent(scanId, 'fscqi_bundle', fscqiContent, null);

  res.json({ id: result.lastInsertRowid, verdict });
});

// Worker fetches FSCQI bundle (for SIAT, REG workers)
app.get('/api/internal/scans/:id/fscqi-bundle', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const bundle = q.get(
    'SELECT * FROM fscqi_bundles WHERE scan_id=? ORDER BY id DESC LIMIT 1',
    scanId
  );
  if (!bundle) return res.status(404).json({ message: 'FSCQI bundle not found' });
  res.json({
    id: bundle.id,
    bundle_version: bundle.bundle_version,
    verdict: bundle.verdict,
    primary_tier: JSON.parse(bundle.primary_tier_json || '[]'),
    candidate_tier: JSON.parse(bundle.candidate_tier_json || '[]'),
    raw_reference_map: JSON.parse(bundle.raw_reference_map_json || '{}'),
    coverage_descriptor: JSON.parse(bundle.coverage_descriptor_json || '{}'),
    weak_regions: JSON.parse(bundle.weak_region_json || '[]'),
    health_summary: JSON.parse(bundle.health_summary_json || '{}'),
  });
});

// Worker creates SIAT output record — SI-1
app.post('/api/internal/scans/:id/siat-output', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const {
    output_version, alpha_soft_path, core_mask_path, hard_mask_path,
    boundary_conf_path, ambiguity_tags, occlusion_labels,
    static_rigid_core_path, pose_safe_support_mask_path,
    rigid_core_path  // backward compat alias
  } = req.body;

  if (!alpha_soft_path || !core_mask_path || !hard_mask_path || !boundary_conf_path)
    return res.status(400).json({ message: 'alpha_soft_path, core_mask_path, hard_mask_path, boundary_conf_path required' });

  const insert = db.prepare(`
    INSERT INTO siat_outputs
      (scan_id, output_version, alpha_soft_path, core_mask_path, hard_mask_path,
       static_rigid_core_path, boundary_conf_path, ambiguity_tags_json, occlusion_labels_json,
       pose_safe_support_mask_path)
    VALUES (?,?,?,?,?,?,?,?,?,?)`);

  const result = insert.run(
    scanId,
    output_version || '1.0.0',
    alpha_soft_path, core_mask_path, hard_mask_path, boundary_conf_path,
    JSON.stringify(ambiguity_tags || []),
    JSON.stringify(occlusion_labels || []),
    (static_rigid_core_path || rigid_core_path) || null,  // accept either name
    pose_safe_support_mask_path || null
  );

  // Record lineage: parent = FSCQI bundle
  const parentHash = getLastArtifactHash(scanId, 'fscqi_bundle');
  const siatContent = {
    alpha_soft_path, core_mask_path, hard_mask_path, boundary_conf_path,
    static_rigid_core_path: (static_rigid_core_path || rigid_core_path),
    pose_safe_support_mask_path, ambiguity_tags, occlusion_labels
  };
  recordLineageEvent(scanId, 'siat_output', siatContent, parentHash);

  res.json({ id: result.lastInsertRowid });
});

// Worker creates REG output — RG-1 honest scale posture
app.post('/api/internal/scans/:id/reg-output', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const {
    output_version, registration_state, reg_graph_json,
    pose_version, scale_regime, scale_confidence_band_json,
    metric_trust_allowed, measurement_validity_claim,
    measurement_use_prohibited, feature_support_regime
  } = req.body;

  if (!registration_state || !pose_version || !scale_regime)
    return res.status(400).json({ message: 'registration_state, pose_version, scale_regime required' });

  const insert = db.prepare(`
    INSERT INTO reg_outputs
      (scan_id, output_version, registration_state, reg_graph_json, pose_version,
       scale_regime, scale_confidence_band_json, metric_trust_allowed,
       measurement_validity_claim, measurement_use_prohibited, feature_support_regime)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)`);

  const result = insert.run(
    scanId,
    output_version || '1.0.0',
    registration_state,
    JSON.stringify(reg_graph_json || {}),
    pose_version,
    scale_regime,
    JSON.stringify(scale_confidence_band_json || {}),
    metric_trust_allowed != null ? (metric_trust_allowed ? 1 : 0) : 0,
    measurement_validity_claim || 'INDETERMINATE',
    measurement_use_prohibited != null ? (measurement_use_prohibited ? 1 : 0) : 0,
    feature_support_regime || 'fallback_wide'
  );

  // Record lineage: parent = SIAT output
  const parentHash = getLastArtifactHash(scanId, 'siat_output');
  const regContent = { registration_state, scale_regime, metric_trust_allowed, pose_version };
  recordLineageEvent(scanId, 'reg_output', regContent, parentHash);

  res.json({ id: result.lastInsertRowid });
});

// Worker creates DG geometry output — DG-1 fragment-preserving geometry
app.post('/api/internal/scans/:id/geometry-output', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const {
    output_version, fragment_set_json, hole_boundary_json,
    usefulness_zones_json, severe_geometry_concern,
    structural_proxy_path, appearance_scaffold_path
  } = req.body;

  if (!fragment_set_json || !hole_boundary_json || !usefulness_zones_json)
    return res.status(400).json({ message: 'fragment_set_json, hole_boundary_json, usefulness_zones_json required' });

  const insert = db.prepare(`
    INSERT INTO geometry_outputs
      (scan_id, output_version, fragment_set_json, hole_boundary_json,
       usefulness_zones_json, severe_geometry_concern,
       structural_proxy_path, appearance_scaffold_path)
    VALUES (?,?,?,?,?,?,?,?)`);

  const result = insert.run(
    scanId,
    output_version || '1.0.0',
    fragment_set_json,
    hole_boundary_json,
    usefulness_zones_json,
    severe_geometry_concern != null ? (severe_geometry_concern ? 1 : 0) : 0,
    structural_proxy_path || null,
    appearance_scaffold_path || null
  );

  // Record lineage: parent = REG output
  const parentHash = getLastArtifactHash(scanId, 'reg_output');
  const geoContent = { fragment_set_json, severe_geometry_concern };
  recordLineageEvent(scanId, 'geometry_output', geoContent, parentHash);

  res.json({ id: result.lastInsertRowid });
});

// Worker creates photoreal view output — VW-1
app.post('/api/internal/scans/:id/view-output', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const {
    output_version, view_bundle_path, lineage_fingerprint, appearance_only_route
  } = req.body;

  if (!view_bundle_path || !lineage_fingerprint)
    return res.status(400).json({ message: 'view_bundle_path and lineage_fingerprint required' });

  const insert = db.prepare(`
    INSERT INTO view_outputs
      (scan_id, output_version, view_bundle_path, lineage_fingerprint, appearance_only_route)
    VALUES (?,?,?,?,?)`);

  const result = insert.run(
    scanId,
    output_version || '1.0.0',
    view_bundle_path,
    lineage_fingerprint,
    appearance_only_route != null ? (appearance_only_route ? 1 : 0) : 0
  );

  // Record lineage: parent = geometry output
  const parentHash = getLastArtifactHash(scanId, 'geometry_output');
  const viewContent = { lineage_fingerprint, appearance_only_route };
  recordLineageEvent(scanId, 'view_output', viewContent, parentHash);

  res.json({ id: result.lastInsertRowid });
});

// Worker GET: fetch latest SIAT output for scan
app.get('/api/internal/scans/:id/siat-output', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const row = q.get(
    'SELECT * FROM siat_outputs WHERE scan_id=? ORDER BY id DESC LIMIT 1',
    scanId
  );
  if (!row) return res.status(404).json({ message: 'SIAT output not found' });
  res.json(row);
});

// Worker GET: fetch latest REG output for scan
app.get('/api/internal/scans/:id/reg-output', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const row = q.get(
    'SELECT * FROM reg_outputs WHERE scan_id=? ORDER BY id DESC LIMIT 1',
    scanId
  );
  if (!row) return res.status(404).json({ message: 'REG output not found' });
  res.json(row);
});

// Worker GET: fetch latest geometry output for scan
app.get('/api/internal/scans/:id/geometry-output', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const row = q.get(
    'SELECT * FROM geometry_outputs WHERE scan_id=? ORDER BY id DESC LIMIT 1',
    scanId
  );
  if (!row) return res.status(404).json({ message: 'Geometry output not found' });
  res.json(row);
});

// Worker GET: fetch all prior geometry output versions (for ED-5 stale rebind)
app.get('/api/internal/scans/:id/geometry-output-history', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const rows = q.all(
    'SELECT id, output_version, fragment_set_json, severe_geometry_concern, created_at FROM geometry_outputs WHERE scan_id=? ORDER BY id ASC',
    scanId
  );
  res.json(rows);
});

// Worker GET: fetch lineage events for scan (CC-1)
app.get('/api/internal/scans/:id/lineage-events', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const rows = q.all(
    'SELECT * FROM lineage_events WHERE scan_id=? ORDER BY id ASC',
    scanId
  );
  res.json(rows);
});

// Worker GET: fetch artifact versions for scan (CC-2 content-addressed)
app.get('/api/internal/scans/:id/artifact-versions', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const rows = q.all(
    'SELECT * FROM artifact_versions WHERE scan_id=? ORDER BY id ASC',
    scanId
  );
  res.json(rows);
});

// Worker GET: fetch artifact versions by type (CC-2)
app.get('/api/internal/scans/:id/artifact-versions/:type', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const artifactType = req.params.type;
  const rows = q.all(
    'SELECT * FROM artifact_versions WHERE scan_id=? AND artifact_type=? ORDER BY id ASC',
    scanId, artifactType
  );
  res.json(rows);
});

// Worker GET: fetch audit log for scan (CC-10)
app.get('/api/internal/scans/:id/audit-log', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const rows = q.all(
    'SELECT * FROM audit_log WHERE target_scan_id=? ORDER BY id ASC',
    scanId
  );
  res.json(rows);
});

// Worker GET: fetch latest view output for scan (EDSIM reads this)
app.get('/api/internal/scans/:id/view-output', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const row = q.get(
    'SELECT * FROM view_outputs WHERE scan_id=? ORDER BY id DESC LIMIT 1',
    scanId
  );
  if (!row) return res.status(404).json({ message: 'View output not found' });
  res.json(row);
});

// Worker POST: create EDSIM output — ED-1 (authority map + edit simulation)
app.post('/api/internal/scans/:id/edsim-output', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const {
    output_version,
    anchor_chart_json,
    placement_authority_json,
    preview_authority_json,
    appearance_only_routes_json,
    edit_regions_json,
    stale_rebind_json,
    edit_readiness_summary_json
  } = req.body;

  if (!anchor_chart_json || !placement_authority_json || !preview_authority_json ||
      !appearance_only_routes_json || !edit_readiness_summary_json)
    return res.status(400).json({ message: 'anchor_chart_json, placement_authority_json, preview_authority_json, appearance_only_routes_json, edit_readiness_summary_json required' });

  const insert = db.prepare(`
    INSERT INTO edit_sim_outputs
      (scan_id, output_version, anchor_chart_json, placement_authority_json,
       preview_authority_json, appearance_only_routes_json, edit_regions_json,
       stale_rebind_json, edit_readiness_summary_json)
    VALUES (?,?,?,?,?,?,?,?,?)`);

  const result = insert.run(
    scanId,
    output_version || '1.0.0',
    anchor_chart_json,
    placement_authority_json,
    preview_authority_json,
    appearance_only_routes_json,
    edit_regions_json || '[]',
    stale_rebind_json || '{}',
    edit_readiness_summary_json
  );

  // Record lineage: parent = view output
  const parentHash = getLastArtifactHash(scanId, 'view_output');
  const edsimContent = { anchor_chart_json, placement_authority_json, preview_authority_json, edit_readiness_summary_json };
  recordLineageEvent(scanId, 'edit_sim_output', edsimContent, parentHash);

  res.json({ id: result.lastInsertRowid });
});

// Worker GET: fetch latest EDSIM output for scan (OQSP reads this)
app.get('/api/internal/scans/:id/edsim-output', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const row = q.get(
    'SELECT * FROM edit_sim_outputs WHERE scan_id=? ORDER BY id DESC LIMIT 1',
    scanId
  );
  if (!row) return res.status(404).json({ message: 'EDSIM output not found' });
  res.json(row);
});

// OQSP creates immutable publish manifest — OQ-1
app.post('/api/internal/scans/:id/publish-manifest', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const {
    manifest_version,
    qc_artifacts_json,
    publishability_class,
    lineage_artifact_refs_json,
    capability_readiness_json,
    severe_concern_aggregation_json,
    integrity_conflict_surfaces_json
  } = req.body;

  if (!qc_artifacts_json || !publishability_class || !lineage_artifact_refs_json ||
      !capability_readiness_json || !severe_concern_aggregation_json)
    return res.status(400).json({ message: 'qc_artifacts_json, publishability_class, lineage_artifact_refs_json, capability_readiness_json, severe_concern_aggregation_json required' });

  const insert = db.prepare(`
    INSERT INTO publish_manifests
      (scan_id, manifest_version, qc_artifacts_json, publishability_class,
       lineage_artifact_refs_json, capability_readiness_json,
       severe_concern_aggregation_json, integrity_conflict_surfaces_json)
    VALUES (?,?,?,?,?,?,?,?)`);

  const result = insert.run(
    scanId,
    manifest_version || '1.0.0',
    qc_artifacts_json,
    publishability_class,
    lineage_artifact_refs_json,
    capability_readiness_json,
    severe_concern_aggregation_json,
    integrity_conflict_surfaces_json || '{}'
  );

  // Record lineage: parent = edit_sim_output (final artifact in pipeline)
  const parentHash = getLastArtifactHash(scanId, 'edit_sim_output');
  const publishContent = { publishability_class, lineage_artifact_refs_json };
  const manifestHash = recordLineageEvent(scanId, 'publish_manifest', publishContent, parentHash);

  // N1 fix: Populate canonical_asset_id on scan row
  const refs = JSON.parse(lineage_artifact_refs_json || '{}');
  const fp = refs.view_fingerprint || '';
  const canonicalId = `body3d-${scanId}-${fp.slice(0, 16)}`;
  q.run('UPDATE scans SET canonical_asset_id=? WHERE id=? AND canonical_asset_id IS NULL', canonicalId, scanId);

  // Audit: publish action
  auditAction(0, 'ASSET_PUBLISHED', scanId, 'publish_manifest', manifestHash, { publishability_class });

  res.json({ id: result.lastInsertRowid });
});

// Worker sets anchor frames (bypasses status check)
app.post('/api/internal/scans/:id/anchor-frames-internal', requireInternal, (req, res) => {
  const { frameIds } = req.body;
  if (!Array.isArray(frameIds) || frameIds.length === 0)
    return res.status(400).json({ message: 'frameIds array required' });
  q.run('UPDATE scan_frames SET isAnchor=0 WHERE scan_id=?', req.params.id);
  const setAnchor = db.prepare('UPDATE scan_frames SET isAnchor=1 WHERE id=?');
  frameIds.forEach(fid => setAnchor.run(fid));
  res.json({ anchorsSet: frameIds.length });
});

// Worker fetches prompt JSON
app.get('/api/internal/scans/:id/prompt', requireInternal, (req, res) => {
  const prompt = q.get('SELECT * FROM scan_prompts WHERE scan_id=?', req.params.id);
  if (!prompt) return res.status(404).json({ message: 'No prompt found' });
  try { res.json({ id: prompt.id, anchors: JSON.parse(prompt.promptJson).anchors }); }
  catch (_) { res.json({ id: prompt.id, raw: prompt.promptJson }); }
});

// Worker registers mask paths (files already written locally by mask_worker.py)
// Body: { masks: [{ frameId, maskUrl, confidence? }] }
app.post('/api/internal/scans/:id/masks', requireInternal, (req, res) => {
  const { masks } = req.body;
  if (!Array.isArray(masks) || masks.length === 0)
    return res.status(400).json({ message: 'masks array required' });
  const insert = db.prepare('INSERT OR REPLACE INTO scan_masks (scan_id,frameId,maskUrl,confidence) VALUES (?,?,?,?)');
  masks.forEach(({ frameId, maskUrl, confidence }) => {
    insert.run(req.params.id, frameId || 0, maskUrl, confidence || null);
  });
  res.json({ masksReceived: masks.length });
});

// Worker delivers raw reconstruction output — JSON body { modelUrl, stage }
// Files are already on the local filesystem; no multipart needed.
app.post('/api/internal/scans/:id/model', requireInternal, (req, res) => {
  const { modelUrl, stage } = req.body;
  if (!modelUrl) return res.status(400).json({ message: 'modelUrl required' });
  q.run('UPDATE scans SET modelUrl=? WHERE id=?', modelUrl, req.params.id);
  scanLog(req.params.id, 'reconstruction', `model registered: ${modelUrl} stage=${stage||'raw'}`);
  res.json(normalizeScanForSpa(q.get('SELECT * FROM scans WHERE id=?', req.params.id)));
});

// Worker delivers final cleaned model — JSON body { modelUrl }
app.post('/api/internal/scans/:id/final-model', requireInternal, (req, res) => {
  const { modelUrl } = req.body;
  if (!modelUrl) return res.status(400).json({ message: 'modelUrl required' });
  q.run(
    "UPDATE scans SET modelUrl=?,status='COMPLETED' WHERE id=?",
    modelUrl, req.params.id
  );
  scanLog(req.params.id, 'cleanup', `final model delivered: ${modelUrl}`);
  res.json(normalizeScanForSpa(q.get('SELECT * FROM scans WHERE id=?', req.params.id)));
});

// Internal log write (workers can post log lines)
app.post('/api/internal/scans/:id/log', requireInternal, (req, res) => {
  const { stage, message, level } = req.body;
  if (!stage || !message) return res.status(400).json({ message: 'stage and message required' });
  scanLog(req.params.id, stage, message, level || 'info');
  res.json({ ok: true });
});

// GET pipeline status for a single scan (used by pipeline.py process_scan)
app.get('/api/internal/scans/:id/status', requireInternal, (req, res) => {
  const scan = q.get('SELECT id,status,errorMessage FROM scans WHERE id=?', req.params.id);
  if (!scan) return res.status(404).json({ message: 'Scan not found' });
  res.json({ id: scan.id, pipelineStatus: scan.status, errorMessage: scan.errorMessage || null });
});

// List scans that need a worker to run (used by pipeline.py --poll)
app.get('/api/internal/scans/pending', requireInternal, (req, res) => {
  const pending = q.all(
    "SELECT id,status FROM scans WHERE status IN ('VIDEO_UPLOADED','FRAME_QA','MASKING','RECONSTRUCTING','POST_PROCESSING') ORDER BY created_at ASC LIMIT 20"
  );
  res.json(pending.map(s => ({ id: s.id, pipelineStatus: s.status })));
});

// GET per-scan telemetry with tripwire flags (CC-14)
app.get('/api/internal/scans/:id/telemetry', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const scan = q.get('SELECT id,status FROM scans WHERE id=?', scanId);
  if (!scan) return res.status(404).json({ message: 'Scan not found' });

  const meta = q.get(`
    SELECT merkle_root, auto_mode_count, manual_shutter_count, retry_count,
           patch_count, fscqi_bundle_id
    FROM capture_metadata WHERE scan_id=?`, scanId);

  if (!meta) return res.json({ error: 'no capture metadata' });

  const auto = meta.auto_mode_count || 0;
  const manual = meta.manual_shutter_count || 0;
  const total = auto + manual;
  const retry = meta.retry_count || 0;
  const patch = meta.patch_count || 0;

  // M1/M2 ratio: manual / auto (M1=manual, M2=auto)
  // High manual ratio may indicate backend strictness leaking to operator burden
  const m1m2Ratio = auto > 0 ? (manual / auto) : (manual > 0 ? Infinity : 0);

  // Tripwire flags
  const flags = [];
  if (retry > 3) flags.push('HIGH_RETRY_INFLATION');
  if (patch > 2) flags.push('HIGH_PATCH_INFLATION');
  if (m1m2Ratio > 5) flags.push('EXCESSIVE_MANUAL_EFFORT');
  if (total === 0) flags.push('NO_CAPTURE_EVENTS');

  res.json({
    scan_id: scanId,
    status: scan.status,
    intervention_counters: {
      auto_mode_count: auto,
      manual_shutter_count: manual,
      retry_count: retry,
      patch_count: patch,
      total_capture_events: total
    },
    ratios: {
      m1m2_ratio: m1m2Ratio === Infinity ? 'all_manual' : parseFloat(m1m2Ratio.toFixed(3)),
      retry_ratio: total > 0 ? parseFloat((retry / total).toFixed(3)) : 0,
      patch_ratio: total > 0 ? parseFloat((patch / total).toFixed(3)) : 0
    },
    tripwire_flags: flags,
    tripwire_thresholds: {
      retry_inflation: '>3 retries',
      patch_inflation: '>2 patches',
      manual_effort: 'M1/M2 > 5'
    }
  });
});

// GET system-wide telemetry aggregate (CC-14)
app.get('/api/internal/telemetry', requireInternal, (req, res) => {
  const days = parseInt(req.query.days || '7');

  // Aggregate across recent scans
  const recentScans = q.all(`
    SELECT COUNT(*) as total_scans,
           SUM(CASE WHEN status='PUBLISHED' THEN 1 ELSE 0 END) as published,
           SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) as failed
    FROM scans
    WHERE created_at >= datetime('now', '-${days} days')`);

  const aggregates = q.get(`
    SELECT
      SUM(COALESCE(cm.auto_mode_count,0)) as total_auto,
      SUM(COALESCE(cm.manual_shutter_count,0)) as total_manual,
      SUM(COALESCE(cm.retry_count,0)) as total_retry,
      SUM(COALESCE(cm.patch_count,0)) as total_patch,
      COUNT(cm.scan_id) as scans_with_meta
    FROM capture_metadata cm
    JOIN scans s ON s.id = cm.scan_id
    WHERE s.created_at >= datetime('now', '-${days} days')`);

  const totalAuto = aggregates.total_auto || 0;
  const totalManual = aggregates.total_manual || 0;
  const totalCapture = totalAuto + totalManual;
  const systemM1M2 = totalAuto > 0 ? (totalManual / totalAuto) : (totalManual > 0 ? Infinity : 0);

  // Tripwire: system-level flags
  const flags = [];
  if (aggregates.scans_with_meta > 0) {
    const avgRetryPerScan = (aggregates.total_retry || 0) / aggregates.scans_with_meta;
    const avgPatchPerScan = (aggregates.total_patch || 0) / aggregates.scans_with_meta;
    if (avgRetryPerScan > 2) flags.push('SYSTEM_HIGH_RETRY_RATE');
    if (avgPatchPerScan > 1.5) flags.push('SYSTEM_HIGH_PATCH_RATE');
  }
  if (systemM1M2 > 3) flags.push('SYSTEM_EXCESSIVE_MANUAL');

  res.json({
    period_days: days,
    scan_summary: recentScans[0],
    intervention_aggregates: {
      total_auto_captures: totalAuto,
      total_manual_captures: totalManual,
      total_retries: aggregates.total_retry || 0,
      total_patches: aggregates.total_patch || 0,
      scans_with_metadata: aggregates.scans_with_meta || 0,
      avg_retries_per_scan: aggregates.scans_with_meta > 0
        ? parseFloat((aggregates.total_retry / aggregates.scans_with_meta).toFixed(2)) : 0,
      avg_patches_per_scan: aggregates.scans_with_meta > 0
        ? parseFloat((aggregates.total_retry / aggregates.scans_with_meta).toFixed(2)) : 0
    },
    system_m1m2_ratio: systemM1M2 === Infinity ? 'all_manual' : parseFloat(systemM1M2.toFixed(3)),
    system_tripwire_flags: flags
  });
});

// Verify deterministic replay: re-hash current artifact chain and compare to stored lineage
// GET /api/internal/scans/:id/replay-verify (CC-14 deterministic replay)
app.get('/api/internal/scans/:id/replay-verify', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);

  // Get current lineage fingerprint from view_output
  const view = q.get('SELECT lineage_fingerprint FROM view_outputs WHERE scan_id=? ORDER BY id DESC LIMIT 1', scanId);
  if (!view) return res.status(404).json({ message: 'view_output not found' });

  // Get artifact chain
  const chain = q.all(
    'SELECT artifact_type, content_hash, parent_hash, byte_size FROM artifact_versions WHERE scan_id=? ORDER BY id ASC',
    scanId
  );

  // Verify: each artifact's parent_hash should match the previous artifact's content_hash
  const checks = [];
  let allPassed = true;
  for (let i = 1; i < chain.length; i++) {
    const prev = chain[i - 1];
    const curr = chain[i];
    const parentMatches = prev.content_hash === curr.parent_hash;
    checks.push({
      artifact: curr.artifact_type,
      expected_parent: prev.content_hash,
      actual_parent: curr.parent_hash || '(null)',
      parent_hash_valid: parentMatches
    });
    if (!parentMatches) allPassed = false;
  }

  // Check: lineage fingerprint should equal final artifact hash
  const finalHash = chain.length > 0 ? chain[chain.length - 1].content_hash : null;
  const lineageMatches = view.lineage_fingerprint === finalHash;

  res.json({
    scan_id: scanId,
    lineage_fingerprint: view.lineage_fingerprint,
    final_artifact_hash: finalHash,
    lineage_fingerprint_valid: lineageMatches,
    chain_integrity: {
      all_parent_links_valid: allPassed,
      chain_length: chain.length,
      checks
    },
    deterministic_replay_ok: allPassed && lineageMatches
  });
});

// POST /api/internal/scans/:id/purge — cascade purge (CC-13)
// Soft-cascade: marks artifacts as non-regenerable, clears lineage root, logs to purge_log.
// Lineage_events are PRESERVED (append-only audit trail).
// After purge: asset must NOT present itself as regenerable.
app.post('/api/internal/scans/:id/purge', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const scan = q.get('SELECT * FROM scans WHERE id=?', scanId);
  if (!scan) return res.status(404).json({ message: 'Scan not found' });

  const { purgeReason, operatorId } = req.body;
  if (!purgeReason) return res.status(400).json({ message: 'purgeReason required' });

  // Gather artifact hashes before purging (for purge_log)
  const artifacts = q.all(
    'SELECT artifact_type, content_hash FROM artifact_versions WHERE scan_id=?',
    scanId
  );

  // Mark scan as PURGED
  q.run('UPDATE scans SET status=? WHERE id=?', 'PURGED', scanId);

  // GAP-6: Clear modelUrl — lineage is terminated, model must not be retrievable as valid
  q.run('UPDATE scans SET modelUrl=NULL WHERE id=?', scanId);

  // GAP-7: Delete model files from disk — orphaned artifacts must not persist after purge
  const scanModelDir = path.join(UPLOADS_DIR, 'models', String(scanId));
  const modelFiles = ['model.glb', 'appearance_scaffold.glb', 'raw_pointcloud.glb'];
  for (const f of modelFiles) {
    try {
      const fp = path.join(scanModelDir, f);
      if (fs.existsSync(fp)) { fs.unlinkSync(fp); }
    } catch (_) {}
  }
  // Also delete EDSIM jsonl files
  const edsimDir = path.join(scanModelDir, 'edsim');
  try {
    if (fs.existsSync(edsimDir)) {
      for (const f of fs.readdirSync(edsimDir)) {
        try { fs.unlinkSync(path.join(edsimDir, f)); } catch (_) {}
      }
    }
  } catch (_) {}

  // CC-1: Chain terminates honestly at PURGE.
  // lineage_root_hash points to the hash of 'PURGED' — the chain ends there, not at a deleted artifact.
  // This is CC-1 compliant: no versioned artifact is mutated; the root hash now
  // references the terminal PURGE state, making the "no longer regenerable" claim honest.
  const terminalHash = crypto.createHash('sha256').update('PURGED').digest('hex');
  q.run('UPDATE scans SET lineage_root_hash=? WHERE id=?', terminalHash, scanId);

  // Append PURGE_ROOT event to lineage_events (append-only — CC-1)
  const lastHash = artifacts.length > 0 ? artifacts[artifacts.length - 1].content_hash : scan.lineage_root_hash;
  db.prepare(`
    INSERT INTO lineage_events (scan_id, event_type, payload_json, parent_artifact_hash, new_artifact_hash)
    VALUES (?,?,?,?,?)`).run(
    scanId, 'PURGE_ROOT',
    JSON.stringify({ purgeReason, purged_by: operatorId || 0, artifacts_count: artifacts.length }),
    lastHash, terminalHash
  );

  // Delete artifact_versions (cascade — content-addressed storage wiped)
  q.run('DELETE FROM artifact_versions WHERE scan_id=?', scanId);

  // Log to purge_log (one entry per artifact)
  const insertPurge = db.prepare(`
    INSERT INTO purge_log (scan_id, purged_artifact_hash, purge_reason, lineage_safe, operator_id)
    VALUES (?,?,?,?,?)`);
  for (const art of artifacts) {
    insertPurge.run(scanId, art.content_hash, purgeReason, 1, operatorId || 0);
  }

  // Audit the purge action itself
  auditAction(operatorId || 0, 'ASSET_PURGED', scanId, null, terminalHash, {
    purgeReason,
    artifactsPurged: artifacts.length,
    lineage_events_preserved: true,
    lineage_chain_terminated_at: terminalHash
  });

  res.json({
    ok: true,
    scan_id: scanId,
    status: 'PURGED',
    lineage_chain_terminated: true,
    lineage_root_hash: terminalHash,
    artifacts_purged: artifacts.length,
    lineage_events_preserved: true,
    purge_reason: purgeReason
  });
});

// GET /api/internal/scans/:id/purge-log — fetch purge history (CC-13)
app.get('/api/internal/scans/:id/purge-log', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const rows = q.all(
    'SELECT * FROM purge_log WHERE scan_id=? ORDER BY id ASC',
    scanId
  );
  res.json(rows);
});

// GET /api/internal/scans/:id/stale-rebind — fetch stale rebind register (CC-13)
// Returns stale rebind data from the most recent EDSIM output
app.get('/api/internal/scans/:id/stale-rebind', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const row = q.get(
    'SELECT stale_rebind_json FROM edit_sim_outputs WHERE scan_id=? ORDER BY id DESC LIMIT 1',
    scanId
  );
  if (!row) return res.status(404).json({ message: 'EDSIM output not found' });
  try {
    const stale = JSON.parse(row.stale_rebind_json || '{}');
    res.json(stale);
  } catch (_) {
    res.json({});
  }
});

// POST /api/internal/scans/:id/retire — mark a specific artifact version as retired/stale (CC-13)
// Does NOT delete — just marks the artifact as having non-identical downstream meaning
app.post('/api/internal/scans/:id/retire', requireInternal, (req, res) => {
  const scanId = parseInt(req.params.id);
  const { artifactHash, reason } = req.body;
  if (!artifactHash || !reason) return res.status(400).json({ message: 'artifactHash and reason required' });

  // Log as a lineage event of type RETIRE
  db.prepare(`
    INSERT INTO lineage_events (scan_id, event_type, payload_json, parent_artifact_hash, new_artifact_hash)
    VALUES (?,?,?,?,?)`).run(
    scanId, 'RETIRE_ARTIFACT', JSON.stringify({ reason, artifactHash }), null, artifactHash
  );

  auditAction(0, 'ARTIFACT_RETIRED', scanId, null, artifactHash, { reason });

  res.json({ ok: true, artifact_hash: artifactHash, reason });
});

// ---------------------------------------------------------------------------
// QR code generation — local, no external API (token stays private)
// ---------------------------------------------------------------------------
app.get('/api/qr', (req, res) => {
  const { data, size: sizeStr } = req.query;
  if (!data) return res.status(400).json({ message: 'data query param required' });
  try {
    const QRCode = require('qrcode');
    const size = Math.min(Math.max(parseInt(sizeStr) || 160, 64), 512);
    QRCode.toBuffer(data, { width: size, margin: 1 }, (err, buf) => {
      if (err) return res.status(500).json({ message: 'QR generation failed' });
      res.type('png').send(buf);
    });
  } catch (e) {
    // qrcode package not installed — return 501
    res.status(501).json({ message: 'QR code generation requires the qrcode npm package' });
  }
});

// ---------------------------------------------------------------------------
// Routes — Testing helpers (internal secret, GET for easy curl)
// ---------------------------------------------------------------------------

// Instantly complete a scan with mock model (Phase 2 UI testing)
app.get('/api/scans/:id/mock-complete', requireInternal, (req, res) => {
  const scan = q.get('SELECT * FROM scans WHERE id=?', req.params.id);
  if (!scan) return res.status(404).json({ message: 'Scan not found' });
  // Find any existing GLB in uploads/, fall back to placeholder
  let modelUrl = '/uploads/mock.glb';
  try {
    const glb = fs.readdirSync(UPLOADS_DIR).find(f => f.endsWith('.glb'));
    if (glb) modelUrl = `/uploads/${glb}`;
  } catch (_) {}
  q.run("UPDATE scans SET status='COMPLETED',modelUrl=? WHERE id=?", modelUrl, req.params.id);
  scanLog(req.params.id, 'mock', 'mock-complete triggered');
  res.json(normalizeScanForSpa(q.get('SELECT * FROM scans WHERE id=?', req.params.id)));
});

// Quick AWAITING_TARGET shortcut — sets 1st frame as anchor (for testing prompt UI without full QA)
app.get('/api/scans/:id/mock-awaiting', requireInternal, (req, res) => {
  const scan = q.get('SELECT * FROM scans WHERE id=?', req.params.id);
  if (!scan) return res.status(404).json({ message: 'Scan not found' });
  const firstFrame = q.get('SELECT * FROM scan_frames WHERE scan_id=? ORDER BY sortOrder ASC LIMIT 1', req.params.id);
  if (!firstFrame) return res.status(400).json({ message: 'No frames uploaded yet' });
  q.run('UPDATE scan_frames SET isAnchor=0 WHERE scan_id=?', req.params.id);
  q.run('UPDATE scan_frames SET isAnchor=1 WHERE id=?', firstFrame.id);
  q.run("UPDATE scans SET status='AWAITING_TARGET' WHERE id=?", req.params.id);
  res.json({ scan: normalizeScanForSpa(q.get('SELECT * FROM scans WHERE id=?', req.params.id)), anchor: firstFrame });
});

// ---------------------------------------------------------------------------
// Serve frontend (SPA fallback) — must be last
// ---------------------------------------------------------------------------
const PUBLIC_DIR = path.join(__dirname, 'public');
app.use(express.static(PUBLIC_DIR));
app.get('*', (req, res) => {
  res.sendFile(path.join(PUBLIC_DIR, 'index.html'));
});

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
app.listen(PORT, '0.0.0.0', () => {
  const lanIp = getNetworkIp();
  console.log('\nBodyScan 3D is running:');
  console.log(`  Local:    http://localhost:${PORT}`);
  console.log(`  Network:  http://${lanIp || 'unknown'}:${PORT}  (access from phone)\n`);
});
