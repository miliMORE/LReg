@echo off
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
  python -m venv venv
  call venv\Scripts\activate.bat
  pip install -r requirements.txt
) else (
  call venv\Scripts\activate.bat
)
python -c "from env_bootstrap import ensure_env; ensure_env(quiet=True)"
python app.py
