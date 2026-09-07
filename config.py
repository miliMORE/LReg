import os
import re
from env_bootstrap import ensure_env
ensure_env(quiet=True)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, '.env'), override=True)
except ImportError:
    pass
DATABASE_PATH = os.environ.get('LCAF_DATABASE_PATH', os.path.join(BASE_DIR, 'registry.db'))
LCAF_ENV = os.environ.get('LCAF_ENV', 'development').strip().lower()
IS_PRODUCTION = LCAF_ENV == 'production'
DEFAULT_SECRET_KEY = 'dev-change-me-before-deploy'
SECRET_KEY = os.environ.get('LCOME_SECRET_KEY', DEFAULT_SECRET_KEY)
LCAF_HOST = os.environ.get('LCAF_HOST', '127.0.0.1')
LCAF_PORT = int(os.environ.get('LCAF_PORT', '5000'))
LCAF_DEBUG = os.environ.get('LCAF_DEBUG', '0' if IS_PRODUCTION else '1').strip() == '1'
LCAF_SESSION_SECURE = os.environ.get('LCAF_SESSION_SECURE', '1' if IS_PRODUCTION else '0').strip() == '1'
LCAF_FORCE_HTTPS = os.environ.get('LCAF_FORCE_HTTPS', '0').strip() == '1'
SEED_SAMPLE_DATA = os.environ.get('LCAF_SEED_SAMPLE_DATA', '0' if IS_PRODUCTION else '1').strip() == '1'
COMPLETION_CRITERIA = {'attendance': {'label': 'Attended training session(s)', 'required': True}, 'assignment': {'label': 'Submitted required assignment', 'required': True}}
DEFAULT_SUPER_ADMIN_USERNAME = 'superadmin'
DEFAULT_ADMIN_USERNAME = 'admin'
DEFAULT_ADMIN_PASSWORD = 'admin123'
ADMIN_ROLES = frozenset({'admin', 'super_admin'})
LEARNER_DEFAULT_PASSWORD = 'learner123'
FACILITATOR_DEFAULT_PASSWORD = 'Lcaf@2026'
LEARNER_ID_PREFIX = 'LCA'
LEARNER_ID_DIGITS = 4
MIN_NEW_PASSWORD_LENGTH = 8
MAX_FAILED_LOGIN_ATTEMPTS = 5
LOGIN_LOCKOUT_HOURS = 12
SESSION_INACTIVITY_MINUTES = int(os.environ.get('LCAF_SESSION_INACTIVITY_MINUTES', '15'))
DEFAULT_ADMINS_KEY = 'TRUE'

def get_admins_key():
    for key in ('ADMINS_KEY', 'LCAF_SUPER_ADMIN_INITIAL_PASSWORD'):
        value = (os.environ.get(key) or '').strip()
        if value:
            return value
    return DEFAULT_ADMINS_KEY

def get_super_admin_initial_password():
    return get_admins_key()

def is_super_admin_initial_password(password):
    initial = get_admins_key()
    return bool(initial) and password == initial
DEFAULT_SUPER_ADMIN_PASSWORD = get_admins_key() or ''

def _forbidden_passwords():
    forbidden = {LEARNER_DEFAULT_PASSWORD, FACILITATOR_DEFAULT_PASSWORD, DEFAULT_ADMIN_PASSWORD, 'LcafSuper@2026', 'LcafOwner@2026', DEFAULT_ADMINS_KEY, 'TRUE', 'True', 'true', 'Lc@mAdmin.2026', DEFAULT_ADMIN_PASSWORD, DEFAULT_SECRET_KEY, 'change-me-to-a-long-random-string'}
    sa = get_admins_key()
    if sa:
        forbidden.add(sa)
    return forbidden
DEFAULT_PASSWORDS = _forbidden_passwords()

def validate_new_password(password):
    if password is None:
        return (False, 'Password is required.')
    if len(password) < MIN_NEW_PASSWORD_LENGTH:
        return (False, f'New password must be at least {MIN_NEW_PASSWORD_LENGTH} characters.')
    if is_super_admin_initial_password(password):
        return (False, 'Choose a new personal password — you cannot keep the temporary bootstrap value from .env (ADMINS_KEY).')
    if password in _forbidden_passwords():
        return (False, 'Choose a personal password — not a temporary or default system password.')
    if not re.search('[A-Za-z]', password):
        return (False, 'Password must include at least one letter.')
    if not re.search('[0-9]', password):
        return (False, 'Password must include at least one number.')
    if password.strip() != password or ' ' in password:
        return (False, 'Password must not contain spaces.')
    return (True, None)

def validate_production_config():
    secret = os.environ.get('LCOME_SECRET_KEY', DEFAULT_SECRET_KEY)
    if not IS_PRODUCTION:
        if secret == DEFAULT_SECRET_KEY and LCAF_HOST not in ('127.0.0.1', 'localhost'):
            raise RuntimeError('Refusing to bind outside localhost with the default secret key. Run: python scripts/generate_env.py  (or start the app once to auto-create .env)')
        return
    if secret == DEFAULT_SECRET_KEY or secret == 'change-me-to-a-long-random-string':
        raise RuntimeError('Production mode requires a real LCOME_SECRET_KEY in .env. Start once to auto-generate, or run: python scripts/generate_env.py')
    if LCAF_DEBUG:
        raise RuntimeError('Production mode cannot run with LCAF_DEBUG=1. Set LCAF_DEBUG=0 in .env.')
