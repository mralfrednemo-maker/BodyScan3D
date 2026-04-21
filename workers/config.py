"""Shared configuration for all BodyScan 3D workers."""
import os

# Server
API_BASE       = os.environ.get('BS3D_API_BASE', 'http://localhost:5000')
INTERNAL_SECRET = os.environ.get('INTERNAL_SECRET', 'bs3d-internal-2026')

# Paths
UPLOADS_DIR    = os.environ.get('BS3D_UPLOADS_DIR', os.path.join(os.path.dirname(__file__), '..', 'uploads'))
FRAMES_DIR     = os.path.join(UPLOADS_DIR, 'frames')
MASKS_DIR      = os.path.join(UPLOADS_DIR, 'masks')
MODELS_DIR     = os.path.join(UPLOADS_DIR, 'models')

os.makedirs(MODELS_DIR, exist_ok=True)

# Feature flags
SAM2_MOCK      = os.environ.get('SAM2_MOCK', '').lower() in ('1', 'true', 'yes')
SAM2_CHECKPOINT = os.environ.get('SAM2_CHECKPOINT', 'sam2_hiera_small.pt')
SAM2_CONFIG     = os.environ.get('SAM2_CONFIG', 'sam2_hiera_s.yaml')

# Frame QA
MIN_FRAMES_REQUIRED = int(os.environ.get('MIN_FRAMES_REQUIRED', '10'))
BLUR_THRESHOLD      = float(os.environ.get('BLUR_THRESHOLD', '50.0'))   # Laplacian variance
TOP_ANCHOR_FRAMES   = int(os.environ.get('TOP_ANCHOR_FRAMES', '5'))      # sharpest frames for prompting UI

# Internal API helper
def internal_headers():
    return {'X-Internal-Secret': INTERNAL_SECRET, 'Content-Type': 'application/json'}
