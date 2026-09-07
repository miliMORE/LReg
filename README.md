# LReg (LCAF Registry)

Flask + SQLite registry application for LCAF programme data — schedules, validation, seeding, and audit-friendly workflows.

## Stack

- Python 3.10+
- Flask / Flask-WTF
- SQLite (`registry.db`)
- Gunicorn (production) or built-in Flask server (local)
- cPanel Passenger support via `passenger_wsgi.py`

## Features

- Programme registry UI and data model (`app.py`, `database.py`)
- Validation helpers (`validators.py`)
- Seeding and bootstrap (`seed.py`, `env_bootstrap.py`)
- Schedule export (`schedule_export.py`)
- Audit utilities (`audit.py`)
- Production and cPanel entrypoints (`wsgi.py`, `passenger_wsgi.py`)

## Install

```bash
python -m venv venv
# Windows:  venv\Scripts\activate
# Unix:     source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set LCOME_SECRET_KEY
```

For live hosts, set `LCAF_ENV=production` and `LCAF_DEBUG=0`.

## Run

```bash
# Local
./run.sh          # or: run.bat / python app.py

# Production
gunicorn --workers 1 --threads 4 --bind 0.0.0.0:8000 wsgi:app

# cPanel
# passenger_wsgi.py (callable: application)
```

## Notes

- Use **one** Gunicorn worker with SQLite
- Persist and back up `registry.db`
- Do not commit `.env` or `registry.db`
