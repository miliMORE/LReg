from env_bootstrap import ensure_env
ensure_env(quiet=True)
from config import validate_production_config
from database import init_db
validate_production_config()
init_db()
from app import app
application = app
