import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from config import COMPLETION_CRITERIA, DATABASE_PATH, LEARNER_ID_DIGITS, LEARNER_ID_PREFIX
LEARNER_ID_PATTERN = re.compile(f'^{LEARNER_ID_PREFIX}\\d{{{LEARNER_ID_DIGITS}}}$', re.IGNORECASE)
STATUS_NOT_STARTED = 'not_started'
STATUS_CATCH_UP = 'catch_up_required'
STATUS_IN_PROGRESS = 'in_progress'
STATUS_ATTENDED = 'attended'
STATUS_ASSIGNMENT_SUBMITTED = 'assignment_submitted'
STATUS_COMPLETED = 'completed'
STATUS_VERIFIED = 'verified'
STATUS_EXEMPT = 'exempt'
STATUS_LABELS = {STATUS_NOT_STARTED: 'Not started', STATUS_CATCH_UP: 'Catch-up required', STATUS_IN_PROGRESS: 'In progress', STATUS_ATTENDED: 'Attended session', STATUS_ASSIGNMENT_SUBMITTED: 'Assignment submitted', STATUS_COMPLETED: 'Completed', STATUS_VERIFIED: 'Verified', STATUS_EXEMPT: 'Exempt'}

@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.executescript("\n            CREATE TABLE IF NOT EXISTS users (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                username TEXT NOT NULL UNIQUE,\n                password_hash TEXT NOT NULL,\n                full_name TEXT NOT NULL,\n                email TEXT,\n                phone TEXT,\n                role TEXT NOT NULL CHECK(role IN ('super_admin', 'admin', 'facilitator', 'learner')),\n                is_active INTEGER NOT NULL DEFAULT 1,\n                created_at TEXT NOT NULL DEFAULT (datetime('now'))\n            );\n\n            CREATE TABLE IF NOT EXISTS thematic_areas (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                name TEXT NOT NULL UNIQUE,\n                slug TEXT NOT NULL UNIQUE,\n                description TEXT,\n                sort_order INTEGER NOT NULL DEFAULT 0,\n                is_active INTEGER NOT NULL DEFAULT 1,\n                created_at TEXT NOT NULL DEFAULT (datetime('now'))\n            );\n\n            CREATE TABLE IF NOT EXISTS modules (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                thematic_area_id INTEGER NOT NULL,\n                module_number INTEGER NOT NULL,\n                name TEXT NOT NULL,\n                description TEXT,\n                sort_order INTEGER NOT NULL DEFAULT 0,\n                is_active INTEGER NOT NULL DEFAULT 1,\n                created_at TEXT NOT NULL DEFAULT (datetime('now')),\n                FOREIGN KEY (thematic_area_id) REFERENCES thematic_areas(id) ON DELETE CASCADE,\n                UNIQUE (thematic_area_id, module_number)\n            );\n\n            CREATE TABLE IF NOT EXISTS delivery_sessions (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                module_id INTEGER NOT NULL,\n                scheduled_date TEXT NOT NULL,\n                session_type TEXT NOT NULL DEFAULT 'online'\n                    CHECK(session_type IN ('online', 'practical', 'both')),\n                title TEXT,\n                notes TEXT,\n                status TEXT NOT NULL DEFAULT 'scheduled'\n                    CHECK(status IN ('scheduled', 'completed', 'cancelled')),\n                facilitator_id INTEGER,\n                created_at TEXT NOT NULL DEFAULT (datetime('now')),\n                FOREIGN KEY (module_id) REFERENCES modules(id) ON DELETE CASCADE,\n                FOREIGN KEY (facilitator_id) REFERENCES users(id)\n            );\n\n            CREATE TABLE IF NOT EXISTS learners (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                user_id INTEGER NOT NULL UNIQUE,\n                join_date TEXT NOT NULL,\n                placement_org TEXT,\n                status TEXT NOT NULL DEFAULT 'active'\n                    CHECK(status IN ('active', 'graduated', 'withdrawn')),\n                notes TEXT,\n                created_at TEXT NOT NULL DEFAULT (datetime('now')),\n                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE\n            );\n\n            CREATE TABLE IF NOT EXISTS learner_module_progress (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                learner_id INTEGER NOT NULL,\n                module_id INTEGER NOT NULL,\n                status TEXT NOT NULL DEFAULT 'not_started',\n                attended_at TEXT,\n                assignment_submitted_at TEXT,\n                completed_at TEXT,\n                verified_at TEXT,\n                verified_by INTEGER,\n                criteria_met TEXT,\n                notes TEXT,\n                updated_at TEXT NOT NULL DEFAULT (datetime('now')),\n                FOREIGN KEY (learner_id) REFERENCES learners(id) ON DELETE CASCADE,\n                FOREIGN KEY (module_id) REFERENCES modules(id) ON DELETE CASCADE,\n                FOREIGN KEY (verified_by) REFERENCES users(id),\n                UNIQUE (learner_id, module_id)\n            );\n\n            CREATE TABLE IF NOT EXISTS attendance_records (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                delivery_session_id INTEGER NOT NULL,\n                learner_id INTEGER NOT NULL,\n                attended INTEGER NOT NULL DEFAULT 0,\n                marked_by INTEGER,\n                marked_at TEXT NOT NULL DEFAULT (datetime('now')),\n                notes TEXT,\n                FOREIGN KEY (delivery_session_id) REFERENCES delivery_sessions(id) ON DELETE CASCADE,\n                FOREIGN KEY (learner_id) REFERENCES learners(id) ON DELETE CASCADE,\n                FOREIGN KEY (marked_by) REFERENCES users(id),\n                UNIQUE (delivery_session_id, learner_id)\n            );\n\n            CREATE TABLE IF NOT EXISTS certificates (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                learner_id INTEGER NOT NULL UNIQUE,\n                issued_at TEXT NOT NULL DEFAULT (datetime('now')),\n                issued_by INTEGER,\n                notes TEXT,\n                FOREIGN KEY (learner_id) REFERENCES learners(id) ON DELETE CASCADE,\n                FOREIGN KEY (issued_by) REFERENCES users(id)\n            );\n\n            CREATE TABLE IF NOT EXISTS app_settings (\n                key TEXT PRIMARY KEY,\n                value TEXT NOT NULL\n            );\n\n            CREATE TABLE IF NOT EXISTS facilitator_thematic_areas (\n                id INTEGER PRIMARY KEY AUTOINCREMENT,\n                facilitator_id INTEGER NOT NULL,\n                thematic_area_id INTEGER NOT NULL,\n                assigned_at TEXT NOT NULL DEFAULT (datetime('now')),\n                assigned_by INTEGER,\n                FOREIGN KEY (facilitator_id) REFERENCES users(id) ON DELETE CASCADE,\n                FOREIGN KEY (thematic_area_id) REFERENCES thematic_areas(id) ON DELETE CASCADE,\n                FOREIGN KEY (assigned_by) REFERENCES users(id),\n                UNIQUE (facilitator_id, thematic_area_id)\n            );\n            ")
        conn.execute('INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)', ('completion_criteria', json.dumps(COMPLETION_CRITERIA)))
        _migrate_schema(conn)

def is_admin_role(role):
    return role in ('admin', 'super_admin')

def _migrate_schema(conn):
    for sql in ('ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0', 'ALTER TABLE learners ADD COLUMN national_id TEXT', 'ALTER TABLE users ADD COLUMN job_title TEXT', 'ALTER TABLE delivery_sessions ADD COLUMN scheduled_time TEXT', 'ALTER TABLE delivery_sessions ADD COLUMN conducted_date TEXT', 'ALTER TABLE delivery_sessions ADD COLUMN conducted_time TEXT'):
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    conn.execute("\n        CREATE TABLE IF NOT EXISTS facilitator_thematic_areas (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            facilitator_id INTEGER NOT NULL,\n            thematic_area_id INTEGER NOT NULL,\n            assigned_at TEXT NOT NULL DEFAULT (datetime('now')),\n            assigned_by INTEGER,\n            FOREIGN KEY (facilitator_id) REFERENCES users(id) ON DELETE CASCADE,\n            FOREIGN KEY (thematic_area_id) REFERENCES thematic_areas(id) ON DELETE CASCADE,\n            FOREIGN KEY (assigned_by) REFERENCES users(id),\n            UNIQUE (facilitator_id, thematic_area_id)\n        )\n        ")
    from audit import ensure_audit_tables
    _migrate_users_super_admin_role(conn)
    ensure_audit_tables(conn)
    ensure_super_admin(conn)
    ensure_default_facilitator_assignments(conn)
    ensure_foundation_programme(conn)
    conn.execute("\n        UPDATE delivery_sessions\n        SET conducted_date = scheduled_date,\n            conducted_time = COALESCE(scheduled_time, '09:00')\n        WHERE status = 'completed' AND conducted_date IS NULL\n        ")

def username_exists(conn, username, exclude_user_id=None):
    q = 'SELECT 1 FROM users WHERE LOWER(username) = LOWER(?)'
    params = [username]
    if exclude_user_id:
        q += ' AND id != ?'
        params.append(exclude_user_id)
    return conn.execute(q, params).fetchone() is not None

def get_facilitators(conn, active_only=False):
    q = "\n        SELECT id, username, full_name, email, phone, job_title,\n               is_active, must_change_password, created_at\n        FROM users WHERE role = 'facilitator'\n    "
    if active_only:
        q += ' AND is_active = 1'
    q += ' ORDER BY full_name'
    return conn.execute(q).fetchall()

def get_facilitator_area_ids(conn, facilitator_user_id):
    rows = conn.execute('SELECT thematic_area_id FROM facilitator_thematic_areas WHERE facilitator_id = ?', (facilitator_user_id,)).fetchall()
    return [r['thematic_area_id'] for r in rows]

def get_facilitator_assigned_areas(conn, facilitator_user_id):
    return conn.execute('\n        SELECT ta.id, ta.name, ta.slug\n        FROM thematic_areas ta\n        JOIN facilitator_thematic_areas fta ON fta.thematic_area_id = ta.id\n        WHERE fta.facilitator_id = ? AND ta.is_active = 1\n        ORDER BY ta.sort_order, ta.name\n        ', (facilitator_user_id,)).fetchall()

def facilitator_has_area_access(conn, user_id, role, thematic_area_id):
    if is_admin_role(role):
        return True
    if role != 'facilitator' or not thematic_area_id:
        return False
    return conn.execute('\n            SELECT 1 FROM facilitator_thematic_areas\n            WHERE facilitator_id = ? AND thematic_area_id = ?\n            ', (user_id, thematic_area_id)).fetchone() is not None

def facilitator_can_access_module(conn, user_id, role, module_id):
    if is_admin_role(role):
        return True
    row = conn.execute('SELECT thematic_area_id FROM modules WHERE id = ?', (module_id,)).fetchone()
    if not row:
        return False
    return facilitator_has_area_access(conn, user_id, role, row['thematic_area_id'])

def get_delivery_sessions_for_user(conn, user_id, role, limit=30):
    if is_admin_role(role):
        return conn.execute('\n            SELECT ds.*, m.id AS mod_id, m.name AS module_name, m.module_number,\n                   m.thematic_area_id, ta.name AS area_name\n            FROM delivery_sessions ds\n            JOIN modules m ON m.id = ds.module_id\n            JOIN thematic_areas ta ON ta.id = m.thematic_area_id\n            ORDER BY ds.scheduled_date DESC, ds.scheduled_time DESC\n            LIMIT ?\n            ', (limit,)).fetchall()
    return conn.execute('\n        SELECT ds.*, m.id AS mod_id, m.name AS module_name, m.module_number,\n               m.thematic_area_id, ta.name AS area_name\n        FROM delivery_sessions ds\n        JOIN modules m ON m.id = ds.module_id\n        JOIN thematic_areas ta ON ta.id = m.thematic_area_id\n        JOIN facilitator_thematic_areas fta ON fta.thematic_area_id = ta.id\n        WHERE fta.facilitator_id = ?\n        ORDER BY ds.scheduled_date DESC, ds.scheduled_time DESC\n        LIMIT ?\n        ', (user_id, limit)).fetchall()

def get_modules_for_facilitator_user(conn, user_id, role):
    if is_admin_role(role):
        return get_all_active_modules(conn)
    return conn.execute('\n        SELECT m.*, ta.name AS area_name, ta.slug AS area_slug\n        FROM modules m\n        JOIN thematic_areas ta ON ta.id = m.thematic_area_id\n        JOIN facilitator_thematic_areas fta ON fta.thematic_area_id = ta.id\n        WHERE fta.facilitator_id = ? AND m.is_active = 1 AND ta.is_active = 1\n        ORDER BY ta.sort_order, ta.name, m.sort_order, m.module_number\n        ', (user_id,)).fetchall()

def set_facilitator_thematic_areas(conn, facilitator_user_id, area_ids, assigned_by):
    valid_ids = {row['id'] for row in conn.execute('SELECT id FROM thematic_areas WHERE is_active = 1').fetchall()}
    conn.execute('DELETE FROM facilitator_thematic_areas WHERE facilitator_id = ?', (facilitator_user_id,))
    for area_id in area_ids:
        try:
            aid = int(area_id)
        except (TypeError, ValueError):
            continue
        if aid not in valid_ids:
            continue
        conn.execute('\n            INSERT INTO facilitator_thematic_areas\n            (facilitator_id, thematic_area_id, assigned_by)\n            VALUES (?, ?, ?)\n            ', (facilitator_user_id, aid, assigned_by))

def ensure_foundation_programme(conn):
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'programme_sync_version'").fetchone()
    if row and row['value'] == '2':
        return
    from programme_data import FOUNDATION_THEMATIC_AREAS, LEGACY_AREA_SLUG_MAP, OFFICIAL_AREA_SLUGS
    for old_slug, new_slug in LEGACY_AREA_SLUG_MAP.items():
        if new_slug is None:
            conn.execute('UPDATE thematic_areas SET is_active = 0 WHERE slug = ?', (old_slug,))
        else:
            conn.execute('UPDATE thematic_areas SET slug = ? WHERE slug = ?', (new_slug, old_slug))
    for name, slug, desc, sort_order, modules in FOUNDATION_THEMATIC_AREAS:
        area_row = conn.execute('SELECT id FROM thematic_areas WHERE slug = ?', (slug,)).fetchone()
        if area_row:
            area_id = area_row['id']
            conn.execute('\n                UPDATE thematic_areas\n                SET name = ?, description = ?, sort_order = ?, is_active = 1\n                WHERE id = ?\n                ', (name, desc, sort_order, area_id))
        else:
            cur = conn.execute('\n                INSERT INTO thematic_areas (name, slug, description, sort_order, is_active)\n                VALUES (?, ?, ?, ?, 1)\n                ', (name, slug, desc, sort_order))
            area_id = cur.lastrowid
        for num, mod_name in modules:
            mod_row = conn.execute('\n                SELECT id FROM modules\n                WHERE thematic_area_id = ? AND module_number = ?\n                ', (area_id, num)).fetchone()
            if mod_row:
                conn.execute('\n                    UPDATE modules SET name = ?, sort_order = ?, is_active = 1\n                    WHERE id = ?\n                    ', (mod_name, num, mod_row['id']))
            else:
                conn.execute('\n                    INSERT INTO modules\n                    (thematic_area_id, module_number, name, sort_order, is_active)\n                    VALUES (?, ?, ?, ?, 1)\n                    ', (area_id, num, mod_name, num))
    for area in conn.execute('SELECT id, slug FROM thematic_areas').fetchall():
        if area['slug'] not in OFFICIAL_AREA_SLUGS:
            conn.execute('UPDATE thematic_areas SET is_active = 0 WHERE id = ?', (area['id'],))
    for learner in conn.execute('SELECT id FROM learners').fetchall():
        ensure_progress_rows(conn, learner['id'])
        recompute_learner_statuses(conn, learner['id'])
    conn.execute('INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)', ('programme_sync_version', '2'))

def _migrate_users_super_admin_role(conn):
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()
    if not row or 'super_admin' in (row['sql'] or ''):
        return
    conn.executescript("\n        PRAGMA foreign_keys=OFF;\n        CREATE TABLE users_new (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            username TEXT NOT NULL UNIQUE,\n            password_hash TEXT NOT NULL,\n            full_name TEXT NOT NULL,\n            email TEXT,\n            phone TEXT,\n            role TEXT NOT NULL CHECK(role IN ('super_admin', 'admin', 'facilitator', 'learner')),\n            is_active INTEGER NOT NULL DEFAULT 1,\n            created_at TEXT NOT NULL DEFAULT (datetime('now')),\n            must_change_password INTEGER NOT NULL DEFAULT 0,\n            job_title TEXT\n        );\n        INSERT INTO users_new (\n            id, username, password_hash, full_name, email, phone, role,\n            is_active, created_at, must_change_password, job_title\n        )\n        SELECT\n            id, username, password_hash, full_name, email, phone, role,\n            is_active, created_at, must_change_password, job_title\n        FROM users;\n        DROP TABLE users;\n        ALTER TABLE users_new RENAME TO users;\n        PRAGMA foreign_keys=ON;\n        ")

def registry_bootstrap_incomplete(conn):
    row = conn.execute("\n        SELECT must_change_password FROM users\n        WHERE role = 'super_admin' AND is_active = 1\n        ORDER BY id LIMIT 1\n        ").fetchone()
    if not row:
        return True
    return bool(row['must_change_password'])

def ensure_super_admin(conn):
    from werkzeug.security import generate_password_hash
    from config import DEFAULT_SUPER_ADMIN_USERNAME, get_super_admin_initial_password
    from env_bootstrap import ensure_env
    if conn.execute("SELECT 1 FROM users WHERE role = 'super_admin' LIMIT 1").fetchone():
        return
    ensure_env(quiet=True)
    password = get_super_admin_initial_password()
    if not password:
        raise RuntimeError('Cannot create superadmin: ADMINS_KEY missing from .env. Run: python scripts/generate_env.py')
    conn.execute("\n        INSERT INTO users\n        (username, password_hash, full_name, email, role, must_change_password)\n        VALUES (?, ?, ?, ?, 'super_admin', 1)\n        ", (DEFAULT_SUPER_ADMIN_USERNAME, generate_password_hash(password), 'LCAF Super Administrator', 'info@lightcomeafrica.com'))

def get_regular_admins(conn, active_only=False):
    q = "\n        SELECT id, username, full_name, email, phone,\n               is_active, must_change_password, created_at\n        FROM users WHERE role = 'admin'\n    "
    if active_only:
        q += ' AND is_active = 1'
    q += ' ORDER BY full_name, username'
    return conn.execute(q).fetchall()

def count_active_super_admins(conn, exclude_user_id=None):
    q = "SELECT COUNT(*) AS c FROM users WHERE role = 'super_admin' AND is_active = 1"
    params = []
    if exclude_user_id:
        q += ' AND id != ?'
        params.append(exclude_user_id)
    return conn.execute(q, params).fetchone()['c']

def ensure_default_facilitator_assignments(conn):
    admin = conn.execute("\n        SELECT id FROM users\n        WHERE role IN ('super_admin', 'admin')\n        ORDER BY CASE role WHEN 'super_admin' THEN 0 ELSE 1 END, id\n        LIMIT 1\n        ").fetchone()
    assigned_by = admin['id'] if admin else None
    defaults = [('collinsmilimo47@gmail.com', ['digital-literacy-internet-ethics']), ('grace@lightcomeafrica.com', ['work-ethics-employability'])]
    for email, slugs in defaults:
        fac = conn.execute("SELECT id FROM users WHERE email = ? AND role = 'facilitator'", (email,)).fetchone()
        if not fac:
            continue
        count = conn.execute('SELECT COUNT(*) AS c FROM facilitator_thematic_areas WHERE facilitator_id = ?', (fac['id'],)).fetchone()['c']
        if count > 0:
            continue
        area_ids = []
        for slug in slugs:
            row = conn.execute('SELECT id FROM thematic_areas WHERE slug = ?', (slug,)).fetchone()
            if row:
                area_ids.append(str(row['id']))
        if area_ids:
            set_facilitator_thematic_areas(conn, fac['id'], area_ids, assigned_by)

def get_facilitator_area_labels(conn, facilitator_user_id):
    areas = get_facilitator_assigned_areas(conn, facilitator_user_id)
    return ', '.join((a['name'] for a in areas)) if areas else 'None assigned'

def normalize_learner_id(raw):
    if not raw:
        return None
    candidate = raw.strip().upper()
    if LEARNER_ID_PATTERN.match(candidate):
        return candidate
    return None

def learner_id_exists(conn, learner_id, exclude_user_id=None):
    return username_exists(conn, learner_id, exclude_user_id)

def get_next_learner_id(conn):
    rows = conn.execute("SELECT username FROM users WHERE role = 'learner'").fetchall()
    max_num = 0
    for row in rows:
        normalized = normalize_learner_id(row['username'])
        if normalized:
            max_num = max(max_num, int(normalized[len(LEARNER_ID_PREFIX):]))
    return f'{LEARNER_ID_PREFIX}{max_num + 1:0{LEARNER_ID_DIGITS}d}'

def validate_learner_id_for_registration(conn, raw_id):
    normalized = normalize_learner_id(raw_id)
    if not normalized:
        return (None, f'Learner ID must match format {LEARNER_ID_PREFIX}#### (e.g. {LEARNER_ID_PREFIX}0001) — {LEARNER_ID_DIGITS} digits, no spaces.')
    if learner_id_exists(conn, normalized):
        return (None, f'Learner ID {normalized} is already registered.')
    return (normalized, None)

def get_completion_criteria(conn):
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'completion_criteria'").fetchone()
    if row:
        return json.loads(row['value'])
    return COMPLETION_CRITERIA

def format_session_schedule(scheduled_date, scheduled_time=None):
    if not scheduled_date:
        return '—'
    if scheduled_time:
        return f'{scheduled_date} at {scheduled_time}'
    return scheduled_date

def session_conducted_label(session_row):
    if not session_row or not session_row['conducted_date']:
        return None
    return format_session_schedule(session_row['conducted_date'], session_row['conducted_time'])

def session_effective_delivery_date(session_row):
    if session_row['status'] == 'completed' and session_row['conducted_date']:
        return session_row['conducted_date']
    return session_row['scheduled_date']

def get_all_delivery_sessions(conn, order_asc=False, include_cancelled=False, limit=200):
    return get_delivery_sessions_filtered(conn, include_cancelled=include_cancelled, order_asc=order_asc, limit=limit)

def get_delivery_sessions_filtered(conn, *, date_from=None, date_to=None, area_id=None, status=None, session_type=None, timeframe='all', today_eat=None, include_cancelled=False, order_asc=True, limit=500):
    order = 'ASC' if order_asc else 'DESC'
    q = '\n        SELECT ds.*, m.id AS mod_id, m.name AS module_name, m.module_number,\n               m.thematic_area_id, ta.name AS area_name,\n               u.full_name AS facilitator_name\n        FROM delivery_sessions ds\n        JOIN modules m ON m.id = ds.module_id\n        JOIN thematic_areas ta ON ta.id = m.thematic_area_id\n        LEFT JOIN users u ON u.id = ds.facilitator_id\n        WHERE ta.is_active = 1 AND m.is_active = 1\n    '
    params = []
    if not include_cancelled and status != 'cancelled':
        q += " AND ds.status != 'cancelled'"
    if date_from:
        q += ' AND ds.scheduled_date >= ?'
        params.append(date_from.strip())
    if date_to:
        q += ' AND ds.scheduled_date <= ?'
        params.append(date_to.strip())
    if area_id:
        q += ' AND ta.id = ?'
        params.append(int(area_id))
    if status and status in ('scheduled', 'completed', 'cancelled'):
        q += ' AND ds.status = ?'
        params.append(status)
    if session_type and session_type in ('online', 'practical', 'both'):
        q += ' AND ds.session_type = ?'
        params.append(session_type)
    if timeframe == 'upcoming' and today_eat:
        q += ' AND ds.scheduled_date >= ?'
        params.append(today_eat)
    elif timeframe == 'past' and today_eat:
        q += ' AND ds.scheduled_date < ?'
        params.append(today_eat)
    q += f' ORDER BY ds.scheduled_date {order}, ds.scheduled_time {order}'
    if limit is not None:
        q += ' LIMIT ?'
        params.append(limit)
    return conn.execute(q, params).fetchall()

def get_facilitator_area_id_set(conn, user_id):
    return set(get_facilitator_area_ids(conn, user_id))

def get_next_area_sort_order(conn):
    row = conn.execute('SELECT COALESCE(MAX(sort_order), 0) AS m FROM thematic_areas').fetchone()
    return max(1, row['m'] + 1)

def area_sort_order_exists(conn, sort_order, exclude_area_id=None):
    q = 'SELECT 1 FROM thematic_areas WHERE sort_order = ?'
    params = [sort_order]
    if exclude_area_id:
        q += ' AND id != ?'
        params.append(exclude_area_id)
    return conn.execute(q, params).fetchone() is not None

def thematic_area_slug_exists(conn, slug, exclude_area_id=None):
    q = 'SELECT 1 FROM thematic_areas WHERE LOWER(slug) = LOWER(?)'
    params = [slug]
    if exclude_area_id:
        q += ' AND id != ?'
        params.append(exclude_area_id)
    return conn.execute(q, params).fetchone() is not None

def thematic_area_name_exists(conn, name, exclude_area_id=None):
    q = 'SELECT 1 FROM thematic_areas WHERE LOWER(name) = LOWER(?)'
    params = [name]
    if exclude_area_id:
        q += ' AND id != ?'
        params.append(exclude_area_id)
    return conn.execute(q, params).fetchone() is not None

def get_next_module_number(conn, area_id):
    row = conn.execute('SELECT COALESCE(MAX(module_number), 0) AS m FROM modules WHERE thematic_area_id = ?', (area_id,)).fetchone()
    return row['m'] + 1

def get_next_module_sort_order(conn, area_id):
    row = conn.execute('SELECT COALESCE(MAX(sort_order), 0) AS m FROM modules WHERE thematic_area_id = ?', (area_id,)).fetchone()
    return max(1, row['m'] + 1)

def module_number_exists(conn, area_id, module_number, exclude_module_id=None):
    q = 'SELECT 1 FROM modules WHERE thematic_area_id = ? AND module_number = ?'
    params = [area_id, module_number]
    if exclude_module_id:
        q += ' AND id != ?'
        params.append(exclude_module_id)
    return conn.execute(q, params).fetchone() is not None

def module_sort_order_exists(conn, area_id, sort_order, exclude_module_id=None):
    q = 'SELECT 1 FROM modules WHERE thematic_area_id = ? AND sort_order = ?'
    params = [area_id, sort_order]
    if exclude_module_id:
        q += ' AND id != ?'
        params.append(exclude_module_id)
    return conn.execute(q, params).fetchone() is not None

def get_active_thematic_areas(conn):
    return conn.execute('SELECT * FROM thematic_areas WHERE is_active = 1 ORDER BY sort_order, name').fetchall()

def get_modules_for_area(conn, area_id, active_only=True):
    q = 'SELECT * FROM modules WHERE thematic_area_id = ?'
    if active_only:
        q += ' AND is_active = 1'
    q += ' ORDER BY sort_order, module_number'
    return conn.execute(q, (area_id,)).fetchall()

def get_all_active_modules(conn):
    return conn.execute('\n        SELECT m.*, ta.name AS area_name, ta.slug AS area_slug\n        FROM modules m\n        JOIN thematic_areas ta ON ta.id = m.thematic_area_id\n        WHERE m.is_active = 1 AND ta.is_active = 1\n        ORDER BY ta.sort_order, ta.name, m.sort_order, m.module_number\n        ').fetchall()

def get_learner_by_user_id(conn, user_id):
    return conn.execute('SELECT l.*, u.full_name, u.email, u.username, u.must_change_password FROM learners l JOIN users u ON u.id = l.user_id WHERE l.user_id = ?', (user_id,)).fetchone()

def get_last_completed_session_date(conn, module_id):
    rows = conn.execute("\n        SELECT scheduled_date, conducted_date, status\n        FROM delivery_sessions\n        WHERE module_id = ? AND status = 'completed'\n        ", (module_id,)).fetchall()
    if not rows:
        return None
    dates = [session_effective_delivery_date(r) for r in rows]
    return max(dates)

def ensure_progress_rows(conn, learner_id):
    modules = get_all_active_modules(conn)
    for mod in modules:
        conn.execute('\n            INSERT OR IGNORE INTO learner_module_progress (learner_id, module_id, status)\n            VALUES (?, ?, ?)\n            ', (learner_id, mod['id'], STATUS_NOT_STARTED))

def recompute_learner_statuses(conn, learner_id):
    learner = conn.execute('SELECT * FROM learners WHERE id = ?', (learner_id,)).fetchone()
    if not learner:
        return
    join_date = learner['join_date']
    modules = get_all_active_modules(conn)
    for mod in modules:
        prog = conn.execute('SELECT * FROM learner_module_progress WHERE learner_id = ? AND module_id = ?', (learner_id, mod['id'])).fetchone()
        if not prog:
            continue
        if prog['status'] in (STATUS_VERIFIED, STATUS_EXEMPT):
            continue
        last_session = get_last_completed_session_date(conn, mod['id'])
        has_attendance = conn.execute('\n            SELECT 1 FROM attendance_records ar\n            JOIN delivery_sessions ds ON ds.id = ar.delivery_session_id\n            WHERE ar.learner_id = ? AND ds.module_id = ? AND ar.attended = 1\n            LIMIT 1\n            ', (learner_id, mod['id'])).fetchone()
        assignment_at = prog['assignment_submitted_at']
        if has_attendance:
            attended_at = prog['attended_at'] or datetime.now().isoformat(timespec='seconds')
        else:
            attended_at = None
        if assignment_at and (attended_at or has_attendance):
            new_status = STATUS_COMPLETED
        elif assignment_at:
            new_status = STATUS_ASSIGNMENT_SUBMITTED
        elif attended_at or has_attendance:
            new_status = STATUS_ATTENDED
        elif last_session and last_session < join_date:
            new_status = STATUS_CATCH_UP
        else:
            upcoming = conn.execute("\n                SELECT 1 FROM delivery_sessions\n                WHERE module_id = ? AND status IN ('scheduled', 'completed')\n                AND scheduled_date >= ?\n                LIMIT 1\n                ", (mod['id'], join_date)).fetchone()
            new_status = STATUS_IN_PROGRESS if upcoming else STATUS_NOT_STARTED
            if last_session and last_session < join_date:
                new_status = STATUS_CATCH_UP
        conn.execute("\n            UPDATE learner_module_progress\n            SET status = ?,\n                attended_at = ?,\n                completed_at = CASE\n                    WHEN ? = ? THEN COALESCE(completed_at, datetime('now'))\n                    ELSE NULL\n                END,\n                criteria_met = ?,\n                updated_at = datetime('now')\n            WHERE learner_id = ? AND module_id = ?\n            ", (new_status, attended_at, new_status, STATUS_COMPLETED, json.dumps({'attendance': bool(attended_at or has_attendance), 'assignment': bool(assignment_at)}), learner_id, mod['id']))

def get_learner_progress_summary(conn, learner_id):
    ensure_progress_rows(conn, learner_id)
    recompute_learner_statuses(conn, learner_id)
    areas = get_active_thematic_areas(conn)
    summary = {'areas': [], 'overall': {'total_modules': 0, 'completed_modules': 0, 'percent': 0}, 'certificate_eligible': False}
    done_statuses = {STATUS_COMPLETED, STATUS_VERIFIED, STATUS_EXEMPT}
    areas_complete = 0
    for area in areas:
        mods = get_modules_for_area(conn, area['id'])
        total = len(mods)
        completed = 0
        modules_detail = []
        for mod in mods:
            prog = conn.execute('SELECT * FROM learner_module_progress WHERE learner_id = ? AND module_id = ?', (learner_id, mod['id'])).fetchone()
            status = prog['status'] if prog else STATUS_NOT_STARTED
            if status in done_statuses:
                completed += 1
            modules_detail.append({'module_id': mod['id'], 'module_number': mod['module_number'], 'name': mod['name'], 'status': status, 'status_label': STATUS_LABELS.get(status, status), 'attended_at': prog['attended_at'] if prog else None, 'assignment_submitted_at': prog['assignment_submitted_at'] if prog else None})
        pct = round(completed / total * 100) if total else 0
        area_complete = total > 0 and completed == total
        if area_complete:
            areas_complete += 1
        summary['areas'].append({'area_id': area['id'], 'name': area['name'], 'slug': area['slug'], 'total_modules': total, 'completed_modules': completed, 'percent': pct, 'area_complete': area_complete, 'modules': modules_detail})
        summary['overall']['total_modules'] += total
        summary['overall']['completed_modules'] += completed
    om = summary['overall']
    om['percent'] = round(om['completed_modules'] / om['total_modules'] * 100) if om['total_modules'] else 0
    om['areas_total'] = len(areas)
    om['areas_complete'] = areas_complete
    summary['certificate_eligible'] = len(areas) > 0 and areas_complete == len(areas)
    cert = conn.execute('SELECT * FROM certificates WHERE learner_id = ?', (learner_id,)).fetchone()
    summary['certificate_issued'] = cert is not None
    if cert:
        summary['certificate_issued_at'] = cert['issued_at']
    return summary

def get_active_learners(conn):
    return conn.execute("\n        SELECT l.*, u.full_name, u.email, u.username, l.national_id\n        FROM learners l\n        JOIN users u ON u.id = l.user_id\n        WHERE l.status = 'active' AND u.is_active = 1\n        ORDER BY u.username\n        ").fetchall()
