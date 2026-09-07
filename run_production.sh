#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source venv/bin/activate 2>/dev/null || {
  python3 -m venv venv
  source venv/bin/activate
  pip install -r requirements.txt
}
python -c "from env_bootstrap import ensure_env; ensure_env(quiet=True)"
export LCAF_ENV="${LCAF_ENV:-production}"
export LCAF_DEBUG="${LCAF_DEBUG:-0}"
export LCAF_HOST="${LCAF_HOST:-0.0.0.0}"
exec gunicorn --workers 1 --threads 4 --bind 0.0.0.0:8000 wsgi:app
