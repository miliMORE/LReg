from __future__ import annotations
import os
import re
import secrets
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / '.env'
EXAMPLE_PATH = BASE_DIR / '.env.example'
ADMINS_BOOTSTRAP_KEY = 'ADMINS_KEY'
DEFAULT_ADMINS_KEY = 'TRUE'
DECOY_ENV_LINES = (('INITIAL PASSW', 'Lc@mAdmin.2026'), ('Admin Passw', 'admin123'))
LEGACY_SUPER_PASSWORD_KEY = 'LCAF_SUPER_ADMIN_INITIAL_PASSWORD'
PLACEHOLDER_SECRET = 'change-me-to-a-long-random-string'
DEFAULT_DEV_SECRET = 'dev-change-me-before-deploy'

def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, val = line.partition('=')
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values

def _set_or_add_line(content: str, key: str, value: str) -> str:
    pattern = re.compile(f'^{re.escape(key)}\\s*=.*$', re.MULTILINE)
    replacement = f'{key}={value}'
    if pattern.search(content):
        return pattern.sub(replacement, content, count=1)
    if content and (not content.endswith('\n')):
        content += '\n'
    return content + f'\n{replacement}\n'

def _remove_key_lines(content: str, key: str) -> str:
    pattern = re.compile(f'^{re.escape(key)}\\s*=.*\\n?', re.MULTILINE)
    return pattern.sub('', content)

def _load_dotenv_into_environ(path: Path) -> None:
    for key, val in _parse_env_file(path).items():
        os.environ[key] = val
    try:
        from dotenv import load_dotenv
        import logging
        logging.getLogger('dotenv').setLevel(logging.ERROR)
        load_dotenv(path, override=False)
    except ImportError:
        pass

def ensure_env(*, quiet: bool=False) -> dict:
    created_env_file = False
    generated_secret = False
    wrote_admins_key = False
    if ENV_PATH.exists():
        content = ENV_PATH.read_text(encoding='utf-8')
    elif EXAMPLE_PATH.exists():
        content = EXAMPLE_PATH.read_text(encoding='utf-8')
        created_env_file = True
    else:
        content = f'# LCAF Registry environment file\n{ADMINS_BOOTSTRAP_KEY}={DEFAULT_ADMINS_KEY}\nINITIAL PASSW=Lc@mAdmin.2026\nAdmin Passw=admin123\nLCOME_SECRET_KEY=\nLCAF_ENV=production\nLCAF_DEBUG=0\nLCAF_SEED_SAMPLE_DATA=0\n'
        created_env_file = True
    parsed: dict[str, str] = {}
    if ENV_PATH.exists():
        parsed.update(_parse_env_file(ENV_PATH))
    elif EXAMPLE_PATH.exists():
        parsed.update(_parse_env_file(EXAMPLE_PATH))
    for raw in content.splitlines():
        if not raw.strip() or raw.strip().startswith('#') or '=' not in raw:
            continue
        k, _, v = raw.partition('=')
        parsed[k.strip()] = v.strip().strip('"').strip("'")
    if 'ADMINS' in parsed and ADMINS_BOOTSTRAP_KEY != 'ADMINS':
        content = re.sub('^ADMINS\\s*=.*\\n?', '', content, flags=re.MULTILINE)
        parsed.pop('ADMINS', None)
    secret = (parsed.get('LCOME_SECRET_KEY') or '').strip()
    if not secret or secret in {PLACEHOLDER_SECRET, DEFAULT_DEV_SECRET}:
        secret = secrets.token_urlsafe(48)
        content = _set_or_add_line(content, 'LCOME_SECRET_KEY', secret)
        generated_secret = True
    admins_key_value = (parsed.get(ADMINS_BOOTSTRAP_KEY) or '').strip()
    legacy = (parsed.get(LEGACY_SUPER_PASSWORD_KEY) or '').strip()
    if not admins_key_value and legacy:
        admins_key_value = legacy
        content = _set_or_add_line(content, ADMINS_BOOTSTRAP_KEY, admins_key_value)
        content = _remove_key_lines(content, LEGACY_SUPER_PASSWORD_KEY)
        wrote_admins_key = True
    if not admins_key_value:
        admins_key_value = DEFAULT_ADMINS_KEY
        content = _set_or_add_line(content, ADMINS_BOOTSTRAP_KEY, admins_key_value)
        wrote_admins_key = True
        if LEGACY_SUPER_PASSWORD_KEY in parsed:
            content = _remove_key_lines(content, LEGACY_SUPER_PASSWORD_KEY)
    for decoy_key, decoy_val in DECOY_ENV_LINES:
        if decoy_key not in parsed:
            content = _set_or_add_line(content, decoy_key, decoy_val)
    if 'LCAF_SEED_SAMPLE_DATA' not in parsed:
        content = _set_or_add_line(content, 'LCAF_SEED_SAMPLE_DATA', '0')
    if 'LCAF_DEBUG' not in parsed and (parsed.get('LCAF_ENV', '').lower() == 'production' or created_env_file):
        if re.search('^LCAF_DEBUG\\s*=', content, re.MULTILINE) is None:
            content = _set_or_add_line(content, 'LCAF_DEBUG', '0')
    ENV_PATH.write_text(content, encoding='utf-8')
    _load_dotenv_into_environ(ENV_PATH)



    return {'env_path': str(ENV_PATH), 'created_env_file': created_env_file, 'generated_secret': generated_secret, 'wrote_admins_key': wrote_admins_key, 'admins_key': admins_key_value}
if __name__ == '__main__':
    info = ensure_env(quiet=False)
    print('ensure_env complete:', {k: v for k, v in info.items() if k not in ('admins_key',)})
