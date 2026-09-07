LCAF Registry
=============

Flask + SQLite.

Requirements
  Python 3.10+
  pip

Install
  python -m venv venv
  Windows:  venv\Scripts\activate
  Unix:     source venv/bin/activate
  pip install -r requirements.txt
  copy .env.example to .env and set LCOME_SECRET_KEY
  set LCAF_ENV=production and LCAF_DEBUG=0 for live hosts

Run
  Local:      run.bat  |  python app.py  |  ./run.sh
  Production: gunicorn --workers 1 --threads 4 --bind 0.0.0.0:8000 wsgi:app
  cPanel:     passenger_wsgi.py (callable: application)

Notes
  - One Gunicorn worker with SQLite
  - Persist and back up registry.db
  - Do not commit .env or registry.db
