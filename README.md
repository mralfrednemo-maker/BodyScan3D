# BodyScan 3D

Professional 3D body scanning tool for plastic surgeons and tattoo artists.

**Tech Stack:** Express + SQLite (better-sqlite3) + React + Three.js + Tailwind CSS + shadcn/ui

## Quick Start

```bash
npm install
npm start
```

Server runs at `http://localhost:5000`. The SQLite database (`data.db`) is created automatically on first run.

## Production

```bash
PORT=5000 NODE_ENV=production node server.js
```

## API

All API routes are prefixed with `/api`. Authentication uses the `X-Visitor-Id` header.

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | /api/health | No | Health check |
| POST | /api/register | No | Create account |
| POST | /api/login | No | Login |
| POST | /api/logout | Yes | Logout |
| GET | /api/user | Yes | Current user |
| GET | /api/clients | Yes | List clients |
| POST | /api/clients | Yes | Create client |
| GET | /api/scans | Yes | List scans |
| GET | /api/scans/recent | Yes | Recent scans |
| POST | /api/scans | Yes | Create scan |
| POST | /api/scans/:id/upload | Yes | Upload 3D model |
| GET | /api/stats | Yes | Dashboard stats |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| PORT | 5000 | Server port |
| NODE_ENV | development | Environment |

## File Uploads

- **3D Models:** `.glb`, `.gltf`, `.obj`, `.stl`, `.ply` — up to 100 MB
- **Photos:** `.jpg`, `.jpeg`, `.png`, `.heic` — up to 20 MB each, 50 at once
- **Tattoo Designs:** `.jpg`, `.jpeg`, `.png` — up to 20 MB

Files are stored in the `uploads/` directory (gitignored).
