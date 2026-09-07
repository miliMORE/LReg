import json
from datetime import datetime, timedelta, timezone
from config import LOGIN_LOCKOUT_HOURS, MAX_FAILED_LOGIN_ATTEMPTS
try:
    from zoneinfo import ZoneInfo
    EAT_TZ = ZoneInfo('Africa/Nairobi')
except Exception:
    EAT_TZ = timezone(timedelta(hours=3))
EAT_LABEL = 'East Africa Time (EAT, UTC+3)'
UTC = timezone.utc
EVENT_LABELS = {'login_success': 'Signed in', 'login_failed': 'Failed sign-in', 'login_locked': 'Sign-in blocked (lockout)', 'logout': 'Signed out', 'password_changed': 'Password changed', 'page_view': 'Page viewed', 'action': 'Action performed', 'area_deleted': 'Thematic area deleted', 'module_deleted': 'Module deleted', 'lockout_cleared': 'Login lockout cleared', 'session_timeout': 'Signed out (inactivity)'}

def get_client_ip(req):
    forwarded = req.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return req.remote_addr or ''

def _username_key(username):
    return (username or '').strip().lower()

def utc_now():
    return datetime.now(UTC)

def utc_now_str():
    return utc_now().strftime('%Y-%m-%d %H:%M:%S')

def eat_today():
    return datetime.now(EAT_TZ).strftime('%Y-%m-%d')

def _parse_stored_utc(value):
    if not value:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(value[:19], fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None

def format_as_eat(value, include_label=False):
    dt = _parse_stored_utc(value)
    if not dt:
        return value or '—'
    eat = dt.astimezone(EAT_TZ)
    text = eat.strftime('%Y-%m-%d %H:%M:%S')
    if include_label:
        return f'{text} EAT'
    return text

def eat_date_range_bounds(date_from=None, date_to=None):
    start_utc = end_utc = None
    if date_from:
        start_eat = datetime.strptime(date_from.strip(), '%Y-%m-%d').replace(hour=0, minute=0, second=0, tzinfo=EAT_TZ)
        start_utc = start_eat.astimezone(UTC).strftime('%Y-%m-%d %H:%M:%S')
    if date_to:
        end_eat = datetime.strptime(date_to.strip(), '%Y-%m-%d').replace(hour=23, minute=59, second=59, tzinfo=EAT_TZ)
        end_utc = end_eat.astimezone(UTC).strftime('%Y-%m-%d %H:%M:%S')
    return (start_utc, end_utc)

def ensure_audit_tables(conn):
    conn.executescript("\n        CREATE TABLE IF NOT EXISTS audit_events (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            created_at TEXT NOT NULL DEFAULT (datetime('now')),\n            event_type TEXT NOT NULL,\n            user_id INTEGER,\n            username TEXT,\n            role TEXT,\n            ip_address TEXT,\n            method TEXT,\n            path TEXT,\n            endpoint TEXT,\n            detail TEXT,\n            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL\n        );\n\n        CREATE TABLE IF NOT EXISTS login_lockouts (\n            username_lower TEXT PRIMARY KEY,\n            failed_attempts INTEGER NOT NULL DEFAULT 0,\n            locked_until TEXT,\n            updated_at TEXT NOT NULL DEFAULT (datetime('now'))\n        );\n\n        CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at);\n        CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_events(user_id);\n        CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_events(event_type);\n        ")

def log_event(conn, event_type, *, user_id=None, username=None, role=None, ip_address=None, method=None, path=None, endpoint=None, detail=None):
    detail_text = detail
    if detail is not None and (not isinstance(detail, str)):
        detail_text = json.dumps(detail, default=str)
    conn.execute('\n        INSERT INTO audit_events\n        (created_at, event_type, user_id, username, role, ip_address, method, path, endpoint, detail)\n        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n        ', (utc_now_str(), event_type, user_id, username, role, ip_address, method, path, endpoint, detail_text))

def get_lockout_status(conn, username):
    key = _username_key(username)
    if not key:
        return {'locked': False, 'failed_attempts': 0, 'locked_until': None, 'attempts_left': MAX_FAILED_LOGIN_ATTEMPTS}
    row = conn.execute('SELECT failed_attempts, locked_until FROM login_lockouts WHERE username_lower = ?', (key,)).fetchone()
    if not row:
        return {'locked': False, 'failed_attempts': 0, 'locked_until': None, 'attempts_left': MAX_FAILED_LOGIN_ATTEMPTS}
    locked_until = row['locked_until']
    until_dt = _parse_stored_utc(locked_until)
    now = utc_now()
    if until_dt and until_dt > now:
        return {'locked': True, 'failed_attempts': row['failed_attempts'], 'locked_until': locked_until, 'attempts_left': 0}
    if until_dt and until_dt <= now:
        conn.execute('\n            UPDATE login_lockouts\n            SET failed_attempts = 0, locked_until = NULL, updated_at = ?\n            WHERE username_lower = ?\n            ', (utc_now_str(), key))
        return {'locked': False, 'failed_attempts': 0, 'locked_until': None, 'attempts_left': MAX_FAILED_LOGIN_ATTEMPTS}
    attempts_left = max(0, MAX_FAILED_LOGIN_ATTEMPTS - row['failed_attempts'])
    return {'locked': False, 'failed_attempts': row['failed_attempts'], 'locked_until': None, 'attempts_left': attempts_left}

def record_login_failure(conn, username):
    key = _username_key(username)
    if not key:
        return {'locked': False, 'attempts_left': MAX_FAILED_LOGIN_ATTEMPTS, 'locked_until': None}
    status = get_lockout_status(conn, username)
    if status['locked']:
        return status
    row = conn.execute('SELECT failed_attempts FROM login_lockouts WHERE username_lower = ?', (key,)).fetchone()
    failed = (row['failed_attempts'] if row else 0) + 1
    locked_until = None
    locked = False
    if failed >= MAX_FAILED_LOGIN_ATTEMPTS:
        locked_until = (utc_now() + timedelta(hours=LOGIN_LOCKOUT_HOURS)).strftime('%Y-%m-%d %H:%M:%S')
        locked = True
        failed = MAX_FAILED_LOGIN_ATTEMPTS
    now_str = utc_now_str()
    conn.execute('\n        INSERT INTO login_lockouts (username_lower, failed_attempts, locked_until, updated_at)\n        VALUES (?, ?, ?, ?)\n        ON CONFLICT(username_lower) DO UPDATE SET\n            failed_attempts = excluded.failed_attempts,\n            locked_until = excluded.locked_until,\n            updated_at = excluded.updated_at\n        ', (key, failed, locked_until, now_str))
    attempts_left = 0 if locked else max(0, MAX_FAILED_LOGIN_ATTEMPTS - failed)
    return {'locked': locked, 'failed_attempts': failed, 'locked_until': locked_until, 'attempts_left': attempts_left}

def clear_login_lockout(conn, username):
    key = _username_key(username)
    if not key:
        return
    conn.execute('DELETE FROM login_lockouts WHERE username_lower = ?', (key,))

def delete_thematic_area(conn, area_id):
    row = conn.execute('SELECT id, name, slug FROM thematic_areas WHERE id = ?', (area_id,)).fetchone()
    if not row:
        return None
    conn.execute('DELETE FROM thematic_areas WHERE id = ?', (area_id,))
    return dict(row)

def delete_module(conn, area_id, module_id):
    row = conn.execute('\n        SELECT m.id, m.module_number, m.name, ta.name AS area_name\n        FROM modules m\n        JOIN thematic_areas ta ON ta.id = m.thematic_area_id\n        WHERE m.id = ? AND m.thematic_area_id = ?\n        ', (module_id, area_id)).fetchone()
    if not row:
        return None
    conn.execute('DELETE FROM modules WHERE id = ?', (module_id,))
    return dict(row)

def get_audit_events(conn, *, event_type=None, username=None, user_id=None, date_from=None, date_to=None, limit=300):
    q = '\n        SELECT ae.*, u.full_name\n        FROM audit_events ae\n        LEFT JOIN users u ON u.id = ae.user_id\n        WHERE 1=1\n    '
    params = []
    if event_type:
        q += ' AND ae.event_type = ?'
        params.append(event_type)
    if username:
        q += ' AND LOWER(ae.username) = LOWER(?)'
        params.append(username.strip())
    if user_id:
        q += ' AND ae.user_id = ?'
        params.append(user_id)
    start_utc, end_utc = eat_date_range_bounds(date_from, date_to)
    if start_utc:
        q += ' AND ae.created_at >= ?'
        params.append(start_utc)
    if end_utc:
        q += ' AND ae.created_at <= ?'
        params.append(end_utc)
    q += ' ORDER BY ae.created_at DESC, ae.id DESC'
    if limit is not None:
        q += ' LIMIT ?'
        params.append(limit)
    return conn.execute(q, params).fetchall()

def get_active_lockouts(conn):
    return conn.execute('\n        SELECT username_lower, failed_attempts, locked_until, updated_at\n        FROM login_lockouts\n        WHERE locked_until IS NOT NULL AND locked_until > ?\n        ORDER BY locked_until DESC\n        ', (utc_now_str(),)).fetchall()
