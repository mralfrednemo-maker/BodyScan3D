'use strict';

// Suppress experimental warnings for node:sqlite
process.removeAllListeners('warning');
process.on('warning', (w) => {
  if (w.name === 'ExperimentalWarning') return;
  console.warn(w.message);
});

const express = require('express');
const path = require('path');
const fs = require('fs');
const cors = require('cors');
const multer = require('multer');
const bcrypt = require('bcryptjs');
const { v4: uuidv4 } = require('uuid');
const { DatabaseSync } = require('node:sqlite');

const app = express();
const PORT = process.env.PORT || 5000;
const DB_PATH = path.join(__dirname, 'data.db');
const UPLOADS_DIR = path.join(__dirname, 'uploads');

// Ensure uploads directory exists
if (!fs.existsSync(UPLOADS_DIR)) fs.mkdirSync(UPLOADS_DIR, { recursive: true });

// ---------------------------------------------------------------------------
// Database setup (node:sqlite — built into Node 22+/24, no compilation)
// ---------------------------------------------------------------------------
const db = new DatabaseSync(DB_PATH);

db.exec("PRAGMA journal_mode = WAL");
db.exec("PRAGMA foreign_keys = ON");

db.exec(`
  CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    displayName TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'professional',
    specialization TEXT,
    created_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    visitor_id TEXT UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    professional_id INTEGER NOT NULL,
    firstName TEXT NOT NULL,
    lastName TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    professional_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    bodyPart TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Processing',
    modelUrl TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS scan_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL,
    photoUrl TEXT NOT NULL,
    sortOrder INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS tattoo_designs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    professional_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    imageUrl TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS scan_annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL,
    posX REAL NOT NULL,
    posY REAL NOT NULL,
    posZ REAL NOT NULL,
    normalX REAL NOT NULL DEFAULT 0,
    normalY REAL NOT NULL DEFAULT 1,
    normalZ REAL NOT NULL DEFAULT 0,
    note TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT '#14b8a6',
    created_at TEXT DEFAULT (datetime('now'))
  );
`);

// Helper: convert null-prototype rows to plain objects for JSON serialisation
function row(r) {
  if (!r) return r;
  return Object.assign({}, r);
}
function rows(arr) {
  return arr.map(row);
}

// Prepare-and-run helpers (node:sqlite uses positional ? params)
const q = {
  get: (sql, ...params) => row(db.prepare(sql).get(...params)),
  all: (sql, ...params) => rows(db.prepare(sql).all(...params)),
  run: (sql, ...params) => db.prepare(sql).run(...params)
};

// ---------------------------------------------------------------------------
// Middleware
// ---------------------------------------------------------------------------
app.use(cors({
  origin: true,
  methods: ['GET', 'POST', 'PATCH', 'PUT', 'DELETE', 'OPTIONS'],
  allowedHeaders: ['Content-Type', 'X-Visitor-Id', 'X-Session-Id'],
  credentials: false
}));

app.options('*', (req, res) => res.sendStatus(204));
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// Serve uploaded files
app.use('/uploads', express.static(UPLOADS_DIR));

// ---------------------------------------------------------------------------
// File upload storage
// ---------------------------------------------------------------------------
const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, UPLOADS_DIR),
  filename: (req, file, cb) => {
    const ext = path.extname(file.originalname).toLowerCase();
    cb(null, `${uuidv4()}${ext}`);
  }
});

const upload = multer({
  storage,
  limits: { fileSize: 100 * 1024 * 1024 } // 100 MB
});

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
  if (!session) return null;
  return q.get('SELECT id, username, displayName, role, specialization FROM users WHERE id = ?', session.user_id);
}

function requireAuth(req, res, next) {
  const user = getSessionUser(req);
  if (!user) return res.status(401).json({ message: 'Unauthorized' });
  req.user = user;
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
const VALID_ROLES = ['professional', 'client'];
const VALID_SPECIALIZATIONS = ['Plastic Surgeon', 'Tattoo Artist', 'Other'];

app.post('/api/register', async (req, res) => {
  try {
    const { username, password, displayName, role = 'professional', specialization } = req.body;
    if (!username || !password || !displayName) {
      return res.status(400).json({ message: 'username, password and displayName are required' });
    }
    if (!VALID_ROLES.includes(role)) {
      return res.status(400).json({ message: `role must be one of: ${VALID_ROLES.join(', ')}` });
    }
    if (q.get('SELECT id FROM users WHERE username = ?', username)) {
      return res.status(400).json({ message: 'Username already taken' });
    }
    const hashed = await bcrypt.hash(password, 10);
    const result = q.run(
      'INSERT INTO users (username, password, displayName, role, specialization) VALUES (?, ?, ?, ?, ?)',
      username, hashed, displayName, role, specialization || null
    );
    const user = { id: Number(result.lastInsertRowid), username, displayName, role, specialization };
    const visitorId = getVisitorId(req) || uuidv4();
    q.run('INSERT OR REPLACE INTO sessions (id, visitor_id, user_id) VALUES (?, ?, ?)', uuidv4(), visitorId, user.id);
    res.status(201).json(user);
  } catch (err) {
    res.status(500).json({ message: err.message });
  }
});

app.post('/api/login', async (req, res) => {
  try {
    const { username, password } = req.body;
    if (!username || !password) return res.status(400).json({ message: 'username and password required' });
    const userRow = q.get('SELECT * FROM users WHERE username = ?', username);
    if (!userRow) return res.status(401).json({ message: 'Invalid credentials' });
    const valid = await bcrypt.compare(password, userRow.password);
    if (!valid) return res.status(401).json({ message: 'Invalid credentials' });
    const user = { id: userRow.id, username: userRow.username, displayName: userRow.displayName, role: userRow.role, specialization: userRow.specialization };
    const visitorId = getVisitorId(req) || uuidv4();
    q.run('INSERT OR REPLACE INTO sessions (id, visitor_id, user_id) VALUES (?, ?, ?)', uuidv4(), visitorId, user.id);
    res.json(user);
  } catch (err) {
    res.status(500).json({ message: err.message });
  }
});

app.post('/api/logout', requireAuth, (req, res) => {
  const visitorId = getVisitorId(req);
  if (visitorId) q.run('DELETE FROM sessions WHERE visitor_id = ?', visitorId);
  res.json({ message: 'Logged out' });
});

app.get('/api/user', requireAuth, (req, res) => {
  res.json(req.user);
});

// ---------------------------------------------------------------------------
// Routes — Clients
// ---------------------------------------------------------------------------
app.get('/api/clients', requireAuth, (req, res) => {
  res.json(q.all('SELECT * FROM clients WHERE professional_id = ? ORDER BY created_at DESC', req.user.id));
});

app.post('/api/clients', requireAuth, (req, res) => {
  const { firstName, lastName, email, phone, notes } = req.body;
  if (!firstName || !lastName) return res.status(400).json({ message: 'firstName and lastName are required' });
  const result = q.run(
    'INSERT INTO clients (professional_id, firstName, lastName, email, phone, notes) VALUES (?, ?, ?, ?, ?, ?)',
    req.user.id, firstName, lastName, email || null, phone || null, notes || null
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
    'UPDATE clients SET firstName=COALESCE(?,firstName), lastName=COALESCE(?,lastName), email=COALESCE(?,email), phone=COALESCE(?,phone), notes=COALESCE(?,notes) WHERE id=?',
    firstName || null, lastName || null, email || null, phone || null, notes || null, req.params.id
  );
  res.json(q.get('SELECT * FROM clients WHERE id = ?', req.params.id));
});

app.delete('/api/clients/:id', requireAuth, (req, res) => {
  const result = q.run('DELETE FROM clients WHERE id = ? AND professional_id = ?', req.params.id, req.user.id);
  if (result.changes === 0) return res.status(404).json({ message: 'Client not found' });
  res.json({ message: 'Deleted' });
});

app.get('/api/clients/:id/scans', requireAuth, (req, res) => {
  if (!q.get('SELECT id FROM clients WHERE id = ? AND professional_id = ?', req.params.id, req.user.id)) {
    return res.status(404).json({ message: 'Client not found' });
  }
  res.json(q.all('SELECT * FROM scans WHERE client_id = ? ORDER BY created_at DESC', req.params.id));
});

// ---------------------------------------------------------------------------
// Routes — Scans
// ---------------------------------------------------------------------------
app.get('/api/scans', requireAuth, (req, res) => {
  res.json(q.all('SELECT * FROM scans WHERE professional_id = ? ORDER BY created_at DESC', req.user.id));
});

// IMPORTANT: /recent must be registered before /:id or Express will match "recent" as an id
app.get('/api/scans/recent', requireAuth, (req, res) => {
  const limit = Math.min(Math.max(1, parseInt(req.query.limit) || 5), 100);
  const sql = `
    SELECT s.*, c.firstName, c.lastName
    FROM scans s
    JOIN clients c ON s.client_id = c.id
    WHERE s.professional_id = ?
    ORDER BY s.created_at DESC
    LIMIT ?
  `;
  res.json(q.all(sql, req.user.id, limit));
});

app.post('/api/scans', requireAuth, (req, res) => {
  const { clientId, title, bodyPart, notes } = req.body;
  if (!clientId || !title || !bodyPart) return res.status(400).json({ message: 'clientId, title and bodyPart are required' });
  if (!q.get('SELECT id FROM clients WHERE id = ? AND professional_id = ?', clientId, req.user.id)) {
    return res.status(404).json({ message: 'Client not found' });
  }
  const result = q.run(
    'INSERT INTO scans (client_id, professional_id, title, bodyPart, notes) VALUES (?, ?, ?, ?, ?)',
    clientId, req.user.id, title, bodyPart, notes || null
  );
  res.status(201).json(q.get('SELECT * FROM scans WHERE id = ?', Number(result.lastInsertRowid)));
});

app.get('/api/scans/:id', requireAuth, (req, res) => {
  const scan = q.get('SELECT * FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id);
  if (!scan) return res.status(404).json({ message: 'Scan not found' });
  res.json(scan);
});

app.patch('/api/scans/:id', requireAuth, (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id)) {
    return res.status(404).json({ message: 'Scan not found' });
  }
  const VALID_SCAN_STATUSES = ['Processing', 'Ready', 'Archived'];
  const { title, bodyPart, status, notes } = req.body;
  if (status && !VALID_SCAN_STATUSES.includes(status)) {
    return res.status(400).json({ message: `status must be one of: ${VALID_SCAN_STATUSES.join(', ')}` });
  }
  q.run(
    'UPDATE scans SET title=COALESCE(?,title), bodyPart=COALESCE(?,bodyPart), status=COALESCE(?,status), notes=COALESCE(?,notes) WHERE id=?',
    title || null, bodyPart || null, status || null, notes || null, req.params.id
  );
  res.json(q.get('SELECT * FROM scans WHERE id = ?', req.params.id));
});

app.delete('/api/scans/:id', requireAuth, (req, res) => {
  const result = q.run('DELETE FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id);
  if (result.changes === 0) return res.status(404).json({ message: 'Scan not found' });
  res.json({ message: 'Deleted' });
});

app.post('/api/scans/:id/upload', requireAuth, upload.single('model'), (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id)) {
    return res.status(404).json({ message: 'Scan not found' });
  }
  if (!req.file) return res.status(400).json({ message: 'No file uploaded' });
  const modelUrl = `/uploads/${req.file.filename}`;
  q.run("UPDATE scans SET modelUrl=?, status='Ready' WHERE id=?", modelUrl, req.params.id);
  res.json(q.get('SELECT * FROM scans WHERE id = ?', req.params.id));
});

// ---------------------------------------------------------------------------
// Routes — Scan Photos
// ---------------------------------------------------------------------------
app.get('/api/scans/:id/photos', requireAuth, (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id)) {
    return res.status(404).json({ message: 'Scan not found' });
  }
  res.json(q.all('SELECT * FROM scan_photos WHERE scan_id = ? ORDER BY sortOrder ASC', req.params.id));
});

app.post('/api/scans/:id/photos', requireAuth, upload.array('photos', 50), (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id)) {
    return res.status(404).json({ message: 'Scan not found' });
  }
  if (!req.files || req.files.length === 0) return res.status(400).json({ message: 'No files uploaded' });
  const existing = q.get('SELECT COUNT(*) as cnt FROM scan_photos WHERE scan_id = ?', req.params.id);
  const insertPhoto = db.prepare('INSERT INTO scan_photos (scan_id, photoUrl, sortOrder) VALUES (?, ?, ?)');
  req.files.forEach((file, i) => {
    insertPhoto.run(req.params.id, `/uploads/${file.filename}`, existing.cnt + i);
  });
  res.status(201).json(q.all('SELECT * FROM scan_photos WHERE scan_id = ? ORDER BY sortOrder ASC', req.params.id));
});

app.delete('/api/scans/:id/photos/:photoId', requireAuth, (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id)) {
    return res.status(404).json({ message: 'Scan not found' });
  }
  const result = q.run('DELETE FROM scan_photos WHERE id = ? AND scan_id = ?', req.params.photoId, req.params.id);
  if (result.changes === 0) return res.status(404).json({ message: 'Photo not found' });
  res.json({ message: 'Deleted' });
});

// ---------------------------------------------------------------------------
// Routes — Tattoo Designs
// ---------------------------------------------------------------------------
app.get('/api/tattoo-designs', requireAuth, (req, res) => {
  res.json(q.all('SELECT * FROM tattoo_designs WHERE professional_id = ? ORDER BY created_at DESC', req.user.id));
});

app.post('/api/tattoo-designs', requireAuth, upload.single('design'), (req, res) => {
  if (!req.file) return res.status(400).json({ message: 'No file uploaded' });
  const name = req.body.name || path.parse(req.file.originalname).name;
  const imageUrl = `/uploads/${req.file.filename}`;
  const result = q.run('INSERT INTO tattoo_designs (professional_id, name, imageUrl) VALUES (?, ?, ?)', req.user.id, name, imageUrl);
  res.status(201).json(q.get('SELECT * FROM tattoo_designs WHERE id = ?', Number(result.lastInsertRowid)));
});

app.delete('/api/tattoo-designs/:id', requireAuth, (req, res) => {
  const result = q.run('DELETE FROM tattoo_designs WHERE id = ? AND professional_id = ?', req.params.id, req.user.id);
  if (result.changes === 0) return res.status(404).json({ message: 'Design not found' });
  res.json({ message: 'Deleted' });
});

// ---------------------------------------------------------------------------
// Routes — Annotations
// ---------------------------------------------------------------------------
app.get('/api/scans/:id/annotations', requireAuth, (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id)) {
    return res.status(404).json({ message: 'Scan not found' });
  }
  res.json(q.all('SELECT * FROM scan_annotations WHERE scan_id = ? ORDER BY created_at ASC', req.params.id));
});

app.post('/api/scans/:id/annotations', requireAuth, (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id)) {
    return res.status(404).json({ message: 'Scan not found' });
  }
  const { posX, posY, posZ, normalX = 0, normalY = 1, normalZ = 0, note, color = '#14b8a6' } = req.body;
  if (posX == null || posY == null || posZ == null || !note) {
    return res.status(400).json({ message: 'posX, posY, posZ and note are required' });
  }
  const result = q.run(
    'INSERT INTO scan_annotations (scan_id, posX, posY, posZ, normalX, normalY, normalZ, note, color) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
    req.params.id, posX, posY, posZ, normalX, normalY, normalZ, note, color
  );
  res.status(201).json(q.get('SELECT * FROM scan_annotations WHERE id = ?', Number(result.lastInsertRowid)));
});

app.delete('/api/scans/:id/annotations/:annotationId', requireAuth, (req, res) => {
  if (!q.get('SELECT id FROM scans WHERE id = ? AND professional_id = ?', req.params.id, req.user.id)) {
    return res.status(404).json({ message: 'Scan not found' });
  }
  const result = q.run('DELETE FROM scan_annotations WHERE id = ? AND scan_id = ?', req.params.annotationId, req.params.id);
  if (result.changes === 0) return res.status(404).json({ message: 'Annotation not found' });
  res.json({ message: 'Deleted' });
});

// ---------------------------------------------------------------------------
// Routes — Stats
// ---------------------------------------------------------------------------
app.get('/api/stats', requireAuth, (req, res) => {
  const uid = req.user.id;
  const totalClients = q.get('SELECT COUNT(*) as cnt FROM clients WHERE professional_id = ?', uid).cnt;
  const totalScans = q.get('SELECT COUNT(*) as cnt FROM scans WHERE professional_id = ?', uid).cnt;
  const totalPhotos = q.get(
    'SELECT COUNT(*) as cnt FROM scan_photos sp JOIN scans s ON sp.scan_id = s.id WHERE s.professional_id = ?', uid
  ).cnt;
  const totalModels = q.get(
    "SELECT COUNT(*) as cnt FROM scans WHERE professional_id = ? AND modelUrl IS NOT NULL AND status = 'Ready'", uid
  ).cnt;
  res.json({ totalClients, totalScans, totalPhotos, totalModels });
});

// ---------------------------------------------------------------------------
// Serve frontend (SPA fallback)
// ---------------------------------------------------------------------------
const PUBLIC_DIR = path.join(__dirname, 'public');
app.use(express.static(PUBLIC_DIR));
app.get('*', (req, res) => {
  res.sendFile(path.join(PUBLIC_DIR, 'index.html'));
});

// ---------------------------------------------------------------------------
// Start server — bind 0.0.0.0 for LAN access
// ---------------------------------------------------------------------------
app.listen(PORT, '0.0.0.0', () => {
  console.log(`\nBodyScan 3D is running:`);
  console.log(`  Local:    http://localhost:${PORT}`);
  console.log(`  Network:  http://192.168.178.36:${PORT}  (access from phone)\n`);
});
