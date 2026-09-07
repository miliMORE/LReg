import csv
import io
import json
from datetime import date, datetime
from functools import wraps
from flask import Flask, Response, flash, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.security import check_password_hash, generate_password_hash
from audit import EAT_LABEL, EVENT_LABELS, clear_login_lockout, delete_module, delete_thematic_area, eat_today, format_as_eat, get_active_lockouts, get_audit_events, get_client_ip, get_lockout_status, log_event, record_login_failure, utc_now_str
from config import ADMIN_ROLES, DEFAULT_ADMIN_PASSWORD, FACILITATOR_DEFAULT_PASSWORD, get_super_admin_initial_password, is_super_admin_initial_password, IS_PRODUCTION, LCAF_DEBUG, LCAF_FORCE_HTTPS, LCAF_HOST, LCAF_PORT, LCAF_SESSION_SECURE, LEARNER_DEFAULT_PASSWORD, LOGIN_LOCKOUT_HOURS, MAX_FAILED_LOGIN_ATTEMPTS, MIN_NEW_PASSWORD_LENGTH, SECRET_KEY, SESSION_INACTIVITY_MINUTES, validate_new_password, validate_production_config
from database import STATUS_ASSIGNMENT_SUBMITTED, STATUS_ATTENDED, STATUS_COMPLETED, STATUS_LABELS, area_sort_order_exists, count_active_super_admins, ensure_progress_rows, get_regular_admins, format_session_schedule, get_active_learners, get_active_thematic_areas, get_all_active_modules, get_completion_criteria, get_db, get_learner_by_user_id, get_learner_progress_summary, get_modules_for_area, facilitator_can_access_module, get_all_delivery_sessions, get_delivery_sessions_filtered, get_delivery_sessions_for_user, get_facilitator_area_id_set, facilitator_has_area_access, session_conducted_label, get_facilitator_area_labels, get_facilitator_assigned_areas, get_facilitator_area_ids, get_facilitators, get_modules_for_facilitator_user, get_next_area_sort_order, get_next_learner_id, get_next_module_number, get_next_module_sort_order, init_db, module_number_exists, module_sort_order_exists, recompute_learner_statuses, registry_bootstrap_incomplete, set_facilitator_thematic_areas, thematic_area_name_exists, thematic_area_slug_exists, username_exists, validate_learner_id_for_registration
from schedule_export import SESSION_TYPE_OPTIONS, STATUS_OPTIONS, TIMEFRAME_OPTIONS, build_ics_calendar_name, build_schedule_filename, describe_schedule_filters, filters_are_default, parse_schedule_filters, schedule_rows_to_csv, schedule_rows_to_ics
from seed import seed_if_empty
from validators import suggest_slug_from_name, validate_admin_username, validate_email, validate_full_name, validate_job_title, validate_join_date, validate_kenya_national_id, validate_kenya_phone, validate_module_name, validate_placement_org, validate_positive_int, validate_session_date, validate_session_time, validate_slug, validate_thematic_area_name
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(WTF_CSRF_ENABLED=True, WTF_CSRF_TIME_LIMIT=None, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=LCAF_SESSION_SECURE, REMEMBER_COOKIE_HTTPONLY=True, REMEMBER_COOKIE_SAMESITE='Lax', REMEMBER_COOKIE_SECURE=LCAF_SESSION_SECURE, PREFERRED_URL_SCHEME='https' if LCAF_SESSION_SECURE or LCAF_FORCE_HTTPS else 'http')
csrf = CSRFProtect(app)

@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    flash('Your form session expired or was invalid. Please refresh the page and try again.', 'warning')
    return (redirect(request.referrer or url_for('login')), 400)
app.jinja_env.filters['session_schedule'] = format_session_schedule
app.jinja_env.filters['session_conducted'] = session_conducted_label
app.jinja_env.filters['eat_time'] = format_as_eat
ADMIN_ACCESS_ROLES = list(ADMIN_ROLES)
FACILITATOR_ACCESS_ROLES = ['facilitator'] + ADMIN_ACCESS_ROLES
SKIP_AUDIT_ENDPOINTS = frozenset({'static', 'login', 'logout', 'setup', 'change_password', 'admin_audit', None})
SESSION_IDLE_SKIP_ENDPOINTS = frozenset({'static', 'login', 'logout', 'setup', None})

@app.before_request
def enforce_https_when_configured():
    if not LCAF_FORCE_HTTPS:
        return
    if request.endpoint == 'static':
        return
    forwarded = (request.headers.get('X-Forwarded-Proto') or '').lower()
    if request.is_secure or forwarded == 'https':
        return
    url = request.url
    if url.startswith('http://'):
        return redirect('https://' + url[len('http://'):], code=301)

def _audit_request_meta():
    return {'ip_address': get_client_ip(request), 'method': request.method, 'path': request.path, 'endpoint': request.endpoint}

def _should_log_page_view(endpoint, method):
    if method != 'GET' or not endpoint:
        return False
    if endpoint in SKIP_AUDIT_ENDPOINTS:
        return False
    return endpoint.startswith(('admin_', 'facilitator_', 'learner_', 'my_')) or endpoint in ('dashboard', 'schedule_view')

@app.context_processor
def inject_nav_state():
    endpoint = request.endpoint or ''

    def nav_home_active():
        if endpoint == 'dashboard':
            return True
        role = session.get('role')
        if role in ADMIN_ROLES and (endpoint == 'admin_home' or endpoint.startswith('admin_')):
            return True
        return False

    def nav_facilitator_active():
        return endpoint == 'facilitator_home' or endpoint.startswith('facilitator_')

    def nav_password_active():
        return endpoint == 'change_password'

    def nav_progress_active():
        return endpoint == 'learner_progress'

    def nav_schedule_active():
        return endpoint in ('learner_schedule', 'schedule_view', 'schedule_download')
    return {'nav_home_active': nav_home_active(), 'nav_facilitator_active': nav_facilitator_active(), 'nav_password_active': nav_password_active(), 'nav_progress_active': nav_progress_active(), 'nav_schedule_active': nav_schedule_active(), 'can_manage_admins': session.get('role') == 'super_admin'}

def _is_admin():
    return session.get('role') in ADMIN_ROLES

def _is_super_admin():
    return session.get('role') == 'super_admin'

def _can_manage_admins():
    return _is_super_admin()

def _guard_facilitator_module_access(conn, module_id):
    if not facilitator_can_access_module(conn, session['user_id'], session.get('role'), module_id):
        flash('You are not assigned to facilitate this thematic area. Contact admin for access.', 'danger')
        return False
    return True

def login_required(roles=None):

    def decorator(f):

        @wraps(f)
        def wrapped(*args, **kwargs):
            if 'user_id' not in session:
                flash('Please log in.', 'warning')
                return redirect(url_for('login'))
            if roles and session.get('role') not in roles:
                flash('You do not have permission to access that page.', 'danger')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return wrapped
    return decorator

@app.before_request
def ensure_db():
    init_db()

@app.after_request
def audit_user_activity(response):
    endpoint = request.endpoint
    if endpoint in SKIP_AUDIT_ENDPOINTS:
        return response
    if 'user_id' not in session:
        return response
    try:
        event_type = None
        if request.method == 'POST':
            event_type = 'action'
        elif _should_log_page_view(endpoint, request.method):
            event_type = 'page_view'
        if event_type:
            with get_db() as conn:
                log_event(conn, event_type, user_id=session.get('user_id'), username=session.get('username'), role=session.get('role'), detail={'summary': endpoint}, **_audit_request_meta())
    except Exception:
        pass
    return response

def _parse_utc_session_ts(value):
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None

def _touch_session_activity():
    session['last_activity'] = utc_now_str()
    session.modified = True

@app.before_request
def enforce_session_timeout():
    if request.endpoint in SESSION_IDLE_SKIP_ENDPOINTS:
        return
    if 'user_id' not in session:
        return
    last_activity = session.get('last_activity')
    if last_activity:
        last_dt = _parse_utc_session_ts(last_activity)
        now_dt = _parse_utc_session_ts(utc_now_str())
        if last_dt and now_dt:
            idle_seconds = (now_dt - last_dt).total_seconds()
            if idle_seconds > SESSION_INACTIVITY_MINUTES * 60:
                login_at = session.get('login_at')
                user_id = session.get('user_id')
                username = session.get('username')
                role = session.get('role')
                with get_db() as conn:
                    log_event(conn, 'session_timeout', user_id=user_id, username=username, role=role, detail={'login_at': login_at, 'last_activity': last_activity, 'idle_minutes': SESSION_INACTIVITY_MINUTES}, **_audit_request_meta())
                session.clear()
                flash(f'You were signed out after {SESSION_INACTIVITY_MINUTES} minutes of inactivity. Please sign in again.', 'warning')
                return redirect(url_for('login'))
    _touch_session_activity()

@app.before_request
def enforce_active_account():
    if request.endpoint in ('login', 'logout', 'setup', 'static', None):
        return
    if 'user_id' not in session:
        return
    with get_db() as conn:
        row = conn.execute('SELECT is_active FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    if not row or not row['is_active']:
        session.clear()
        flash('Your account has been deactivated. Contact an administrator.', 'danger')
        return redirect(url_for('login'))

@app.before_request
def enforce_password_change():
    if request.endpoint in ('change_password', 'logout', 'login', 'setup', 'static', None):
        return
    if session.get('must_change_password'):
        return redirect(url_for('change_password'))

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    with get_db() as conn:
        count = conn.execute('SELECT COUNT(*) AS c FROM users').fetchone()['c']
    if count > 0 and 'user_id' not in session:
        flash('Registry already initialized. Please log in.', 'info')
        return redirect(url_for('login'))
    if request.method == 'POST':
        seed_if_empty()
        flash('Registry initialized. Please sign in.', 'success')
        return redirect(url_for('login'))
    return render_template('setup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    with get_db() as conn:
        if conn.execute('SELECT COUNT(*) AS c FROM users').fetchone()['c'] == 0:
            return redirect(url_for('setup'))
        bootstrap_pending = registry_bootstrap_incomplete(conn)
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        meta = _audit_request_meta()
        with get_db() as conn:
            lockout = get_lockout_status(conn, username)
            if lockout['locked']:
                log_event(conn, 'login_locked', username=username, detail={'locked_until': lockout['locked_until'], 'failed_attempts': lockout['failed_attempts']}, **meta)
                flash(f"Too many failed sign-in attempts. This username is locked until {format_as_eat(lockout['locked_until'], include_label=True)}. Wait {LOGIN_LOCKOUT_HOURS} hours or ask an administrator to reset your password.", 'danger')
                return render_template('login.html', bootstrap_pending=registry_bootstrap_incomplete(conn), max_attempts=MAX_FAILED_LOGIN_ATTEMPTS, lockout_hours=LOGIN_LOCKOUT_HOURS)
            user = conn.execute('SELECT * FROM users WHERE LOWER(username) = LOWER(?)', (username,)).fetchone()
            if user and (not user['is_active']):
                record_login_failure(conn, username)
                log_event(conn, 'login_failed', user_id=user['id'], username=username, role=user['role'], detail={'reason': 'account_deactivated'}, **meta)
                flash('This account has been deactivated. Contact an administrator.', 'danger')
            elif user and check_password_hash(user['password_hash'], password):
                if registry_bootstrap_incomplete(conn) and user['role'] != 'super_admin':
                    log_event(conn, 'login_failed', user_id=user['id'], username=username, role=user['role'], detail={'reason': 'bootstrap_incomplete'}, **meta)
                    flash('System setup is not complete. Contact your administrator.', 'warning')
                    return render_template('login.html', bootstrap_pending=True, max_attempts=MAX_FAILED_LOGIN_ATTEMPTS, lockout_hours=LOGIN_LOCKOUT_HOURS)
                clear_login_lockout(conn, username)
                must_change = bool(user['must_change_password'])
                if user['role'] == 'learner' and password == LEARNER_DEFAULT_PASSWORD:
                    must_change = True
                elif user['role'] == 'facilitator' and password == FACILITATOR_DEFAULT_PASSWORD:
                    must_change = True
                elif user['role'] == 'admin' and password == DEFAULT_ADMIN_PASSWORD:
                    must_change = True
                elif user['role'] == 'super_admin' and is_super_admin_initial_password(password):
                    must_change = True
                if must_change and (not user['must_change_password']):
                    conn.execute('UPDATE users SET must_change_password = 1 WHERE id = ?', (user['id'],))
                log_event(conn, 'login_success', user_id=user['id'], username=user['username'], role=user['role'], detail={'must_change_password': must_change}, **meta)
                session.clear()
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['role'] = user['role']
                session['full_name'] = user['full_name']
                session['must_change_password'] = must_change
                session['login_at'] = utc_now_str()
                session['last_activity'] = utc_now_str()
                if session['must_change_password']:
                    flash('Please set a new password before continuing.', 'warning')
                    return redirect(url_for('change_password'))
                flash(f"Welcome, {user['full_name']}.", 'success')
                return redirect(url_for('dashboard'))
            else:
                failure = record_login_failure(conn, username)
                log_event(conn, 'login_locked' if failure['locked'] else 'login_failed', username=username, user_id=user['id'] if user else None, role=user['role'] if user else None, detail={'reason': 'invalid_credentials', 'attempts_left': failure['attempts_left'], 'locked_until': failure.get('locked_until')}, **meta)
                if failure['locked']:
                    flash(f"Too many failed sign-in attempts. This username is locked for {LOGIN_LOCKOUT_HOURS} hours (until {format_as_eat(failure['locked_until'], include_label=True)}). Ask an administrator to reset your password.", 'danger')
                elif failure['attempts_left'] > 0:
                    flash(f"Invalid Learner ID / username or password. {failure['attempts_left']} attempt(s) remaining before a {LOGIN_LOCKOUT_HOURS}-hour lockout.", 'danger')
                else:
                    flash('Invalid Learner ID / username or password.', 'danger')
        with get_db() as conn:
            bootstrap_pending = registry_bootstrap_incomplete(conn)
        return render_template('login.html', bootstrap_pending=bootstrap_pending, max_attempts=MAX_FAILED_LOGIN_ATTEMPTS, lockout_hours=LOGIN_LOCKOUT_HOURS)
    return render_template('login.html', bootstrap_pending=bootstrap_pending, max_attempts=MAX_FAILED_LOGIN_ATTEMPTS, lockout_hours=LOGIN_LOCKOUT_HOURS)

@app.route('/change-password', methods=['GET', 'POST'])
@login_required()
def change_password():
    forced = session.get('must_change_password', False)
    if request.method == 'POST':
        current = request.form.get('current_password', '')
        new_pw = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        with get_db() as conn:
            user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
            if not user or not check_password_hash(user['password_hash'], current):
                flash('Current password is incorrect.', 'danger')
                return render_template('change_password.html', forced=forced, min_password_length=MIN_NEW_PASSWORD_LENGTH)
            if new_pw != confirm:
                flash('New passwords do not match.', 'danger')
                return render_template('change_password.html', forced=forced, min_password_length=MIN_NEW_PASSWORD_LENGTH)
            ok_pw, pw_err = validate_new_password(new_pw)
            if not ok_pw:
                flash(pw_err, 'danger')
                return render_template('change_password.html', forced=forced, min_password_length=MIN_NEW_PASSWORD_LENGTH)
            conn.execute('\n                UPDATE users SET password_hash = ?, must_change_password = 0\n                WHERE id = ?\n                ', (generate_password_hash(new_pw), session['user_id']))
            log_event(conn, 'password_changed', user_id=user['id'], username=user['username'], role=user['role'], detail={'forced': forced}, **_audit_request_meta())
            role = user['role']
            needs_first_admin = False
            if role == 'super_admin':
                needs_first_admin = conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone() is None
        session['must_change_password'] = False
        flash('Password updated successfully.', 'success')
        if session.get('role') == 'super_admin' and needs_first_admin:
            return redirect(url_for('admin_admins'))
        return redirect(url_for('dashboard'))
    return render_template('change_password.html', forced=forced, min_password_length=MIN_NEW_PASSWORD_LENGTH)

@app.route('/logout')
def logout():
    if 'user_id' in session:
        login_at = session.get('login_at')
        duration = None
        if login_at:
            start = datetime.strptime(login_at[:19], '%Y-%m-%d %H:%M:%S')
            mins = int((datetime.now() - start).total_seconds() // 60)
            duration = f'{mins} min'
        with get_db() as conn:
            log_event(conn, 'logout', user_id=session.get('user_id'), username=session.get('username'), role=session.get('role'), detail={'login_at': login_at, 'session_duration': duration}, **_audit_request_meta())
    session.clear()
    flash('Logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required()
def dashboard():
    role = session['role']
    if role == 'learner':
        return redirect(url_for('learner_progress'))
    if role == 'facilitator':
        return redirect(url_for('facilitator_home'))
    return redirect(url_for('admin_home'))

@app.route('/my-progress')
@login_required(roles=['learner'])
def learner_progress():
    with get_db() as conn:
        learner = get_learner_by_user_id(conn, session['user_id'])
        if not learner:
            flash('Learner profile not found.', 'danger')
            return redirect(url_for('logout'))
        summary = get_learner_progress_summary(conn, learner['id'])
        schedule_preview = get_all_delivery_sessions(conn, order_asc=True, limit=5)
    return render_template('learner_progress.html', learner=learner, summary=summary, schedule_preview=schedule_preview)

@app.route('/facilitator')
@login_required(roles=FACILITATOR_ACCESS_ROLES)
def facilitator_home():
    with get_db() as conn:
        uid = session['user_id']
        role = session['role']
        sessions = get_all_delivery_sessions(conn, order_asc=True)
        learners = get_active_learners(conn)
        assigned_areas = get_facilitator_assigned_areas(conn, uid) if role == 'facilitator' else []
        assigned_area_ids = get_facilitator_area_id_set(conn, uid) if role == 'facilitator' else None
    return render_template('facilitator_home.html', sessions=sessions, learners=learners, assigned_areas=assigned_areas, assigned_area_ids=assigned_area_ids, is_admin=_is_admin())

@app.route('/facilitator/session/<int:session_id>/attendance', methods=['GET', 'POST'])
@login_required(roles=FACILITATOR_ACCESS_ROLES)
def facilitator_attendance(session_id):
    with get_db() as conn:
        ds = conn.execute('\n            SELECT ds.*, m.id AS mod_id, m.module_number, m.thematic_area_id,\n                   m.name AS module_name, ta.name AS area_name\n            FROM delivery_sessions ds\n            JOIN modules m ON m.id = ds.module_id\n            JOIN thematic_areas ta ON ta.id = m.thematic_area_id\n            WHERE ds.id = ?\n            ', (session_id,)).fetchone()
        if not ds:
            flash('Session not found.', 'danger')
            return redirect(url_for('facilitator_home'))
        if not _guard_facilitator_module_access(conn, ds['mod_id']):
            return redirect(url_for('facilitator_home'))
        learners = get_active_learners(conn)
        if request.method == 'POST':
            for learner in learners:
                attended = request.form.get(f"learner_{learner['id']}") == 'on'
                conn.execute("\n                    INSERT INTO attendance_records\n                    (delivery_session_id, learner_id, attended, marked_by)\n                    VALUES (?, ?, ?, ?)\n                    ON CONFLICT(delivery_session_id, learner_id) DO UPDATE SET\n                    attended = excluded.attended, marked_by = excluded.marked_by,\n                    marked_at = datetime('now')\n                    ", (session_id, learner['id'], 1 if attended else 0, session['user_id']))
                if attended:
                    conn.execute("\n                        INSERT INTO learner_module_progress (learner_id, module_id, status, attended_at)\n                        VALUES (?, ?, 'attended', datetime('now'))\n                        ON CONFLICT(learner_id, module_id) DO UPDATE SET\n                        attended_at = COALESCE(learner_module_progress.attended_at, datetime('now')),\n                        updated_at = datetime('now')\n                        ", (learner['id'], ds['mod_id']))
            if request.form.get('mark_session_complete') == 'on':
                conducted_date, err = validate_session_date(request.form.get('conducted_date'))
                if err:
                    flash(err, 'danger')
                    return redirect(url_for('facilitator_attendance', session_id=session_id))
                conducted_time, err = validate_session_time(request.form.get('conducted_time'))
                if err:
                    flash(err, 'danger')
                    return redirect(url_for('facilitator_attendance', session_id=session_id))
                conn.execute("\n                    UPDATE delivery_sessions\n                    SET status = 'completed', conducted_date = ?, conducted_time = ?\n                    WHERE id = ?\n                    ", (conducted_date, conducted_time, session_id))
            for learner in learners:
                recompute_learner_statuses(conn, learner['id'])
            flash('Attendance saved.', 'success')
            return redirect(url_for('facilitator_attendance', session_id=session_id))
        attendance = {row['learner_id']: row['attended'] for row in conn.execute('SELECT learner_id, attended FROM attendance_records WHERE delivery_session_id = ?', (session_id,)).fetchall()}
    return render_template('facilitator_attendance.html', ds=ds, learners=learners, attendance=attendance)

@app.route('/facilitator/module/<int:module_id>/assignments', methods=['GET', 'POST'])
@login_required(roles=FACILITATOR_ACCESS_ROLES)
def facilitator_assignments(module_id):
    with get_db() as conn:
        mod = conn.execute('\n            SELECT m.*, ta.name AS area_name FROM modules m\n            JOIN thematic_areas ta ON ta.id = m.thematic_area_id\n            WHERE m.id = ?\n            ', (module_id,)).fetchone()
        if not mod:
            flash('Module not found.', 'danger')
            return redirect(url_for('facilitator_home'))
        if not _guard_facilitator_module_access(conn, module_id):
            return redirect(url_for('facilitator_home'))
        learners = get_active_learners(conn)
        if request.method == 'POST':
            for learner in learners:
                submitted = request.form.get(f"learner_{learner['id']}") == 'on'
                if submitted:
                    conn.execute("\n                        INSERT INTO learner_module_progress\n                        (learner_id, module_id, status, assignment_submitted_at)\n                        VALUES (?, ?, 'assignment_submitted', datetime('now'))\n                        ON CONFLICT(learner_id, module_id) DO UPDATE SET\n                        assignment_submitted_at = datetime('now'), updated_at = datetime('now')\n                        ", (learner['id'], module_id))
                else:
                    conn.execute("\n                        UPDATE learner_module_progress\n                        SET assignment_submitted_at = NULL, updated_at = datetime('now')\n                        WHERE learner_id = ? AND module_id = ?\n                        ", (learner['id'], module_id))
                recompute_learner_statuses(conn, learner['id'])
            flash('Assignment submissions updated.', 'success')
            return redirect(url_for('facilitator_assignments', module_id=module_id))
        submitted = {}
        for learner in learners:
            prog = conn.execute('SELECT assignment_submitted_at FROM learner_module_progress WHERE learner_id = ? AND module_id = ?', (learner['id'], module_id)).fetchone()
            submitted[learner['id']] = bool(prog and prog['assignment_submitted_at'])
    return render_template('facilitator_assignments.html', mod=mod, learners=learners, submitted=submitted)

@app.route('/facilitator/session/new', methods=['GET', 'POST'])
@login_required(roles=FACILITATOR_ACCESS_ROLES)
def facilitator_new_session():
    with get_db() as conn:
        modules = get_modules_for_facilitator_user(conn, session['user_id'], session['role'])
        if request.method == 'POST':
            module_id = int(request.form['module_id'])
            if not _guard_facilitator_module_access(conn, module_id):
                return redirect(url_for('facilitator_home'))
            scheduled_date, err = validate_session_date(request.form.get('scheduled_date'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('facilitator_new_session'))
            scheduled_time, err = validate_session_time(request.form.get('scheduled_time'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('facilitator_new_session'))
            conn.execute("\n                INSERT INTO delivery_sessions\n                (module_id, scheduled_date, scheduled_time, session_type, title,\n                 status, facilitator_id)\n                VALUES (?, ?, ?, ?, ?, 'scheduled', ?)\n                ", (module_id, scheduled_date, scheduled_time, request.form['session_type'], request.form.get('title') or 'Training session', session['user_id']))
            flash(f'Session scheduled for {format_session_schedule(scheduled_date, scheduled_time)}.', 'success')
            return redirect(url_for('facilitator_home'))
    return render_template('facilitator_new_session.html', modules=modules)

@app.route('/facilitator/session/<int:session_id>/edit', methods=['GET', 'POST'])
@login_required(roles=FACILITATOR_ACCESS_ROLES)
def facilitator_edit_session(session_id):
    with get_db() as conn:
        ds = conn.execute('\n            SELECT ds.*, m.id AS mod_id, m.module_number, m.thematic_area_id,\n                   m.name AS module_name, ta.name AS area_name\n            FROM delivery_sessions ds\n            JOIN modules m ON m.id = ds.module_id\n            JOIN thematic_areas ta ON ta.id = m.thematic_area_id\n            WHERE ds.id = ?\n            ', (session_id,)).fetchone()
        if not ds:
            flash('Session not found.', 'danger')
            return redirect(url_for('facilitator_home'))
        if not facilitator_has_area_access(conn, session['user_id'], session.get('role'), ds['thematic_area_id']):
            flash('You can only edit sessions for thematic areas assigned to you.', 'danger')
            return redirect(url_for('facilitator_home'))
        if request.method == 'POST':
            scheduled_date, err = validate_session_date(request.form.get('scheduled_date'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('facilitator_edit_session', session_id=session_id))
            scheduled_time, err = validate_session_time(request.form.get('scheduled_time'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('facilitator_edit_session', session_id=session_id))
            status = request.form.get('status', ds['status'])
            if status not in ('scheduled', 'completed', 'cancelled'):
                status = ds['status']
            conducted_date = ds['conducted_date']
            conducted_time = ds['conducted_time']
            if status == 'completed':
                conducted_date, err = validate_session_date(request.form.get('conducted_date'))
                if err:
                    flash(err, 'danger')
                    return redirect(url_for('facilitator_edit_session', session_id=session_id))
                conducted_time, err = validate_session_time(request.form.get('conducted_time'))
                if err:
                    flash(err, 'danger')
                    return redirect(url_for('facilitator_edit_session', session_id=session_id))
            elif status != 'completed':
                conducted_date = None
                conducted_time = None
            conn.execute('\n                UPDATE delivery_sessions\n                SET scheduled_date = ?, scheduled_time = ?, session_type = ?,\n                    title = ?, status = ?, conducted_date = ?, conducted_time = ?,\n                    notes = ?\n                WHERE id = ?\n                ', (scheduled_date, scheduled_time, request.form['session_type'], request.form.get('title') or 'Training session', status, conducted_date, conducted_time, request.form.get('notes', '').strip() or None, session_id))
            flash('Session updated.', 'success')
            return redirect(url_for('facilitator_home'))
    return render_template('facilitator_edit_session.html', ds=ds)

def _fetch_schedule_sessions(conn, filters, limit=500):
    return get_delivery_sessions_filtered(conn, date_from=filters['date_from'], date_to=filters['date_to'], area_id=filters['area_id'], status=filters['status'], session_type=filters['session_type'], timeframe=filters['timeframe'], today_eat=eat_today(), include_cancelled=filters['include_cancelled'], order_asc=True, limit=limit)

@app.route('/schedule')
@login_required()
def schedule_view():
    filters = parse_schedule_filters(request.args)
    with get_db() as conn:
        areas = get_active_thematic_areas(conn)
        sessions = _fetch_schedule_sessions(conn, filters)
        learner = None
        if session.get('role') == 'learner':
            learner = get_learner_by_user_id(conn, session['user_id'])
            if not learner:
                flash('Learner profile not found.', 'danger')
                return redirect(url_for('logout'))
        area_name = None
        if filters.get('area_id'):
            for area in areas:
                if area['id'] == filters['area_id']:
                    area_name = area['name']
                    break
    filter_summary = describe_schedule_filters(filters, area_name=area_name)
    return render_template('schedule.html', sessions=sessions, areas=areas, filters=filters, filter_summary=filter_summary, filters_are_default=filters_are_default(filters), timeframe_options=TIMEFRAME_OPTIONS, status_options=STATUS_OPTIONS, session_type_options=SESSION_TYPE_OPTIONS, eat_label=EAT_LABEL, learner=learner)

@app.route('/schedule/download')
@login_required()
def schedule_download():
    fmt = (request.args.get('format') or 'csv').strip().lower()
    if fmt not in ('csv', 'ics'):
        flash('Choose CSV (spreadsheet) or ICS (calendar) format.', 'danger')
        return redirect(url_for('schedule_view', **request.args.to_dict(flat=True)))
    filters = parse_schedule_filters(request.args)
    with get_db() as conn:
        sessions = _fetch_schedule_sessions(conn, filters, limit=None)
        area_name = None
        if filters.get('area_id'):
            row = conn.execute('SELECT name FROM thematic_areas WHERE id = ?', (filters['area_id'],)).fetchone()
            if row:
                area_name = row['name']
        log_event(conn, 'action', user_id=session.get('user_id'), username=session.get('username'), role=session.get('role'), detail={'action': 'schedule_download', 'format': fmt, 'session_count': len(sessions), 'filters': describe_schedule_filters(filters, area_name=area_name)}, **_audit_request_meta())
    if fmt == 'ics':
        calendar_name = build_ics_calendar_name(filters, area_name=area_name)
        body = schedule_rows_to_ics(sessions, calendar_name=calendar_name)
        mimetype = 'text/calendar; charset=utf-8'
    else:
        body = schedule_rows_to_csv(sessions)
        mimetype = 'text/csv; charset=utf-8'
    filename = build_schedule_filename(filters, fmt, date.today().isoformat())
    return Response(body, mimetype=mimetype, headers={'Content-Disposition': f'attachment; filename="{filename}"'})

@app.route('/my-schedule')
@login_required(roles=['learner'])
def learner_schedule():
    return redirect(url_for('schedule_view', **request.args.to_dict(flat=True)))

@app.route('/facilitator/learner/<int:learner_id>')
@login_required(roles=FACILITATOR_ACCESS_ROLES)
def facilitator_learner_view(learner_id):
    with get_db() as conn:
        learner = conn.execute('\n            SELECT l.*, u.full_name, u.email, u.username FROM learners l\n            JOIN users u ON u.id = l.user_id WHERE l.id = ?\n            ', (learner_id,)).fetchone()
        if not learner:
            flash('Learner not found.', 'danger')
            return redirect(url_for('facilitator_home'))
        summary = get_learner_progress_summary(conn, learner_id)
    return render_template('learner_progress.html', learner=learner, summary=summary, viewer_role=session['role'])

@app.route('/admin')
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_home():
    with get_db() as conn:
        stats = {'areas': conn.execute('SELECT COUNT(*) AS c FROM thematic_areas WHERE is_active = 1').fetchone()['c'], 'modules': conn.execute('SELECT COUNT(*) AS c FROM modules WHERE is_active = 1').fetchone()['c'], 'learners': conn.execute("SELECT COUNT(*) AS c FROM learners WHERE status = 'active'").fetchone()['c'], 'facilitators': conn.execute("SELECT COUNT(*) AS c FROM users WHERE role = 'facilitator' AND is_active = 1").fetchone()['c'], 'certificates': conn.execute('SELECT COUNT(*) AS c FROM certificates').fetchone()['c'], 'admins': conn.execute("SELECT COUNT(*) AS c FROM users WHERE role = 'admin' AND is_active = 1").fetchone()['c']}
        learners = get_active_learners(conn)
        progress_rows = []
        for learner in learners:
            s = get_learner_progress_summary(conn, learner['id'])
            progress_rows.append({'learner': learner, 'overall_percent': s['overall']['percent'], 'areas_complete': s['overall']['areas_complete'], 'areas_total': s['overall']['areas_total'], 'certificate_eligible': s['certificate_eligible'], 'certificate_issued': s['certificate_issued']})
    return render_template('admin_home.html', stats=stats, progress_rows=progress_rows, is_super_admin=_is_super_admin(), can_manage_admins=_can_manage_admins())

def _validate_area_form(conn, form, exclude_area_id=None):
    name, err = validate_thematic_area_name(form.get('name'))
    if err:
        return (None, err)
    slug_raw = form.get('slug') or suggest_slug_from_name(name)
    slug, err = validate_slug(slug_raw)
    if err:
        return (None, err)
    sort_order, err = validate_positive_int(form.get('sort_order'), 'Sort order', required=True, minimum=1)
    if err:
        return (None, err)
    if thematic_area_name_exists(conn, name, exclude_area_id):
        return (None, f'Thematic area name "{name}" is already in use.')
    if thematic_area_slug_exists(conn, slug, exclude_area_id):
        return (None, f'Slug "{slug}" is already in use.')
    if area_sort_order_exists(conn, sort_order, exclude_area_id):
        return (None, f'Sort order {sort_order} is already assigned to another thematic area. Choose a different order.')
    description = (form.get('description') or '').strip() or None
    return ({'name': name, 'slug': slug, 'sort_order': sort_order, 'description': description}, None)

def _validate_module_form(conn, area_id, form, exclude_module_id=None):
    module_number, err = validate_positive_int(form.get('module_number'), 'Module number', required=True, minimum=1)
    if err:
        return (None, err)
    name, err = validate_module_name(form.get('name'))
    if err:
        return (None, err)
    sort_order, err = validate_positive_int(form.get('sort_order'), 'Sort order', required=True, minimum=1)
    if err:
        return (None, err)
    if module_number_exists(conn, area_id, module_number, exclude_module_id):
        return (None, f'Module number M{module_number} already exists in this thematic area.')
    if module_sort_order_exists(conn, area_id, sort_order, exclude_module_id):
        return (None, f'Sort order {sort_order} is already assigned to another module in this area.')
    description = (form.get('description') or '').strip() or None
    return ({'module_number': module_number, 'name': name, 'sort_order': sort_order, 'description': description}, None)

@app.route('/admin/areas', methods=['GET', 'POST'])
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_areas():
    with get_db() as conn:
        if request.method == 'POST':
            data, err = _validate_area_form(conn, request.form)
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_areas'))
            conn.execute('\n                INSERT INTO thematic_areas (name, slug, description, sort_order)\n                VALUES (?, ?, ?, ?)\n                ', (data['name'], data['slug'], data['description'], data['sort_order']))
            flash(f'''Thematic area "{data['name']}" added.''', 'success')
            return redirect(url_for('admin_areas'))
        areas = conn.execute('SELECT * FROM thematic_areas ORDER BY sort_order, name').fetchall()
        next_sort_order = get_next_area_sort_order(conn)
    return render_template('admin_areas.html', areas=areas, next_sort_order=next_sort_order, is_super_admin=_is_super_admin())

@app.route('/admin/areas/<int:area_id>/edit', methods=['GET', 'POST'])
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_edit_area(area_id):
    with get_db() as conn:
        area = conn.execute('SELECT * FROM thematic_areas WHERE id = ?', (area_id,)).fetchone()
        if not area:
            flash('Thematic area not found.', 'danger')
            return redirect(url_for('admin_areas'))
        if request.method == 'POST':
            data, err = _validate_area_form(conn, request.form, exclude_area_id=area_id)
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_edit_area', area_id=area_id))
            conn.execute('\n                UPDATE thematic_areas\n                SET name = ?, slug = ?, description = ?, sort_order = ?\n                WHERE id = ?\n                ', (data['name'], data['slug'], data['description'], data['sort_order'], area_id))
            flash(f'''Thematic area "{data['name']}" updated.''', 'success')
            return redirect(url_for('admin_areas'))
    return render_template('admin_edit_area.html', area=area)

@app.route('/admin/areas/<int:area_id>/status', methods=['POST'])
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_set_area_status(area_id):
    active = request.form.get('active') == '1'
    with get_db() as conn:
        area = conn.execute('SELECT * FROM thematic_areas WHERE id = ?', (area_id,)).fetchone()
        if not area:
            flash('Thematic area not found.', 'danger')
            return redirect(url_for('admin_areas'))
        conn.execute('UPDATE thematic_areas SET is_active = ? WHERE id = ?', (1 if active else 0, area_id))
        if active:
            flash(f'''Thematic area "{area['name']}" reactivated.''', 'success')
        else:
            flash(f'''Thematic area "{area['name']}" marked obsolete. Existing learner records are preserved; new learners are not required to complete it.''', 'success')
    return redirect(request.referrer or url_for('admin_areas'))

@app.route('/admin/areas/<int:area_id>/modules', methods=['GET', 'POST'])
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_modules(area_id):
    with get_db() as conn:
        area = conn.execute('SELECT * FROM thematic_areas WHERE id = ?', (area_id,)).fetchone()
        if not area:
            flash('Area not found.', 'danger')
            return redirect(url_for('admin_areas'))
        if request.method == 'POST':
            data, err = _validate_module_form(conn, area_id, request.form)
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_modules', area_id=area_id))
            conn.execute('\n                INSERT INTO modules\n                (thematic_area_id, module_number, name, description, sort_order)\n                VALUES (?, ?, ?, ?, ?)\n                ', (area_id, data['module_number'], data['name'], data['description'], data['sort_order']))
            for row in conn.execute('SELECT id FROM learners').fetchall():
                ensure_progress_rows(conn, row['id'])
            flash(f"Module M{data['module_number']} added.", 'success')
            return redirect(url_for('admin_modules', area_id=area_id))
        modules = get_modules_for_area(conn, area_id, active_only=False)
        next_module_number = get_next_module_number(conn, area_id)
        next_sort_order = get_next_module_sort_order(conn, area_id)
    return render_template('admin_modules.html', area=area, modules=modules, next_module_number=next_module_number, next_sort_order=next_sort_order, is_super_admin=_is_super_admin())

@app.route('/admin/areas/<int:area_id>/modules/<int:module_id>/edit', methods=['GET', 'POST'])
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_edit_module(area_id, module_id):
    with get_db() as conn:
        area = conn.execute('SELECT * FROM thematic_areas WHERE id = ?', (area_id,)).fetchone()
        if not area:
            flash('Thematic area not found.', 'danger')
            return redirect(url_for('admin_areas'))
        mod = conn.execute('SELECT * FROM modules WHERE id = ? AND thematic_area_id = ?', (module_id, area_id)).fetchone()
        if not mod:
            flash('Module not found.', 'danger')
            return redirect(url_for('admin_modules', area_id=area_id))
        if request.method == 'POST':
            data, err = _validate_module_form(conn, area_id, request.form, exclude_module_id=module_id)
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_edit_module', area_id=area_id, module_id=module_id))
            conn.execute('\n                UPDATE modules\n                SET module_number = ?, name = ?, description = ?, sort_order = ?\n                WHERE id = ?\n                ', (data['module_number'], data['name'], data['description'], data['sort_order'], module_id))
            flash(f"Module M{data['module_number']} updated.", 'success')
            return redirect(url_for('admin_modules', area_id=area_id))
    return render_template('admin_edit_module.html', area=area, module=mod)

@app.route('/admin/areas/<int:area_id>/modules/<int:module_id>/status', methods=['POST'])
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_set_module_status(area_id, module_id):
    active = request.form.get('active') == '1'
    with get_db() as conn:
        mod = conn.execute('\n            SELECT m.*, ta.name AS area_name\n            FROM modules m JOIN thematic_areas ta ON ta.id = m.thematic_area_id\n            WHERE m.id = ? AND m.thematic_area_id = ?\n            ', (module_id, area_id)).fetchone()
        if not mod:
            flash('Module not found.', 'danger')
            return redirect(url_for('admin_modules', area_id=area_id))
        conn.execute('UPDATE modules SET is_active = ? WHERE id = ?', (1 if active else 0, module_id))
        if active:
            flash(f"Module M{mod['module_number']} ({mod['name']}) reactivated.", 'success')
        else:
            flash(f"Module M{mod['module_number']} ({mod['name']}) marked obsolete. Existing attendance and progress records are preserved.", 'success')
    return redirect(request.referrer or url_for('admin_modules', area_id=area_id))

@app.route('/admin/learners', methods=['GET', 'POST'])
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_learners():
    with get_db() as conn:
        if request.method == 'POST':
            full_name, err = validate_full_name(request.form.get('full_name'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_learners'))
            national_id, err = validate_kenya_national_id(request.form.get('national_id'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_learners'))
            join_date, err = validate_join_date(request.form.get('join_date'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_learners'))
            placement_org, err = validate_placement_org(request.form.get('placement_org'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_learners'))
            email, err = validate_email(request.form.get('email'), required=False)
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_learners'))
            learner_id_raw = request.form.get('learner_id', '').strip()
            learner_id, err = validate_learner_id_for_registration(conn, learner_id_raw)
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_learners'))
            if email and username_exists(conn, email):
                flash('That email is already registered to another account.', 'danger')
                return redirect(url_for('admin_learners'))
            uid = conn.execute("\n                INSERT INTO users\n                (username, password_hash, full_name, email, role, must_change_password)\n                VALUES (?, ?, ?, ?, 'learner', 1)\n                ", (learner_id, generate_password_hash(LEARNER_DEFAULT_PASSWORD), full_name, email)).lastrowid
            cur = conn.execute('\n                INSERT INTO learners\n                (user_id, join_date, placement_org, national_id, notes)\n                VALUES (?, ?, ?, ?, ?)\n                ', (uid, join_date, placement_org, national_id, request.form.get('notes', '').strip() or None))
            ensure_progress_rows(conn, cur.lastrowid)
            recompute_learner_statuses(conn, cur.lastrowid)
            flash(f'Learner {full_name} registered. Learner ID: {learner_id} (initial password: {LEARNER_DEFAULT_PASSWORD}).', 'success')
            return redirect(url_for('admin_learners'))
        learners = conn.execute('\n            SELECT l.*, u.full_name, u.email, u.username, u.must_change_password,\n                   u.is_active AS account_active\n            FROM learners l JOIN users u ON u.id = l.user_id\n            ORDER BY u.username\n            ').fetchall()
        next_learner_id = get_next_learner_id(conn)
    return render_template('admin_learners.html', learners=learners, next_learner_id=next_learner_id, default_password=LEARNER_DEFAULT_PASSWORD)

@app.route('/admin/facilitators', methods=['GET', 'POST'])
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_facilitators():
    with get_db() as conn:
        if request.method == 'POST':
            full_name, err = validate_full_name(request.form.get('full_name'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_facilitators'))
            email, err = validate_email(request.form.get('email'), required=True)
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_facilitators'))
            if username_exists(conn, email):
                flash(f'An account with email {email} already exists (email is the login username).', 'danger')
                return redirect(url_for('admin_facilitators'))
            phone, err = validate_kenya_phone(request.form.get('phone'), required=False)
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_facilitators'))
            job_title, err = validate_job_title(request.form.get('job_title'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_facilitators'))
            conn.execute("\n                INSERT INTO users\n                (username, password_hash, full_name, email, phone, job_title, role, must_change_password)\n                VALUES (?, ?, ?, ?, ?, ?, 'facilitator', 1)\n                ", (email, generate_password_hash(FACILITATOR_DEFAULT_PASSWORD), full_name, email, phone, job_title))
            flash(f'Facilitator {full_name} registered. Login: {email} (initial password: {FACILITATOR_DEFAULT_PASSWORD}).', 'success')
            return redirect(url_for('admin_facilitators'))
        facilitators = []
        for f in get_facilitators(conn):
            facilitators.append({'user': f, 'area_labels': get_facilitator_area_labels(conn, f['id'])})
    return render_template('admin_facilitators.html', facilitators=facilitators, default_password=FACILITATOR_DEFAULT_PASSWORD)

@app.route('/admin/facilitators/<int:user_id>/reset-password', methods=['POST'])
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_reset_facilitator_password(user_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ? AND role = 'facilitator'", (user_id,)).fetchone()
        if not row:
            flash('Facilitator not found.', 'danger')
            return redirect(url_for('admin_facilitators'))
        conn.execute('\n            UPDATE users SET password_hash = ?, must_change_password = 1\n            WHERE id = ?\n            ', (generate_password_hash(FACILITATOR_DEFAULT_PASSWORD), user_id))
        clear_login_lockout(conn, row['username'])
        flash(f"Password for {row['email']} reset to {FACILITATOR_DEFAULT_PASSWORD}. Login lockout cleared if any.", 'success')
    return redirect(url_for('admin_facilitators'))

@app.route('/admin/learners/<int:learner_id>/reset-password', methods=['POST'])
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_reset_learner_password(learner_id):
    with get_db() as conn:
        row = conn.execute('\n            SELECT l.id, u.id AS user_id, u.full_name, u.username\n            FROM learners l JOIN users u ON u.id = l.user_id\n            WHERE l.id = ?\n            ', (learner_id,)).fetchone()
        if not row:
            flash('Learner not found.', 'danger')
            return redirect(url_for('admin_learners'))
        conn.execute('\n            UPDATE users SET password_hash = ?, must_change_password = 1\n            WHERE id = ?\n            ', (generate_password_hash(LEARNER_DEFAULT_PASSWORD), row['user_id']))
        clear_login_lockout(conn, row['username'])
        flash(f"Password for {row['username']} ({row['full_name']}) reset to {LEARNER_DEFAULT_PASSWORD}. Any login lockout was cleared.", 'success')
    return redirect(url_for('admin_learners'))

@app.route('/admin/learners/<int:learner_id>/edit', methods=['GET', 'POST'])
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_edit_learner(learner_id):
    with get_db() as conn:
        learner = conn.execute('\n            SELECT l.*, u.id AS user_id, u.username, u.full_name, u.email, u.phone,\n                   u.is_active AS account_active\n            FROM learners l JOIN users u ON u.id = l.user_id\n            WHERE l.id = ?\n            ', (learner_id,)).fetchone()
        if not learner:
            flash('Learner not found.', 'danger')
            return redirect(url_for('admin_learners'))
        if request.method == 'POST':
            full_name, err = validate_full_name(request.form.get('full_name'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_edit_learner', learner_id=learner_id))
            national_id, err = validate_kenya_national_id(request.form.get('national_id'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_edit_learner', learner_id=learner_id))
            placement_org, err = validate_placement_org(request.form.get('placement_org'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_edit_learner', learner_id=learner_id))
            email, err = validate_email(request.form.get('email'), required=False)
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_edit_learner', learner_id=learner_id))
            phone, err = validate_kenya_phone(request.form.get('phone'), required=False)
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_edit_learner', learner_id=learner_id))
            if email and username_exists(conn, email, exclude_user_id=learner['user_id']):
                flash('That email is already used by another account.', 'danger')
                return redirect(url_for('admin_edit_learner', learner_id=learner_id))
            conn.execute('\n                UPDATE users SET full_name = ?, email = ?, phone = ?\n                WHERE id = ?\n                ', (full_name, email, phone, learner['user_id']))
            conn.execute('\n                UPDATE learners\n                SET placement_org = ?, national_id = ?, notes = ?\n                WHERE id = ?\n                ', (placement_org, national_id, request.form.get('notes', '').strip() or None, learner_id))
            flash(f"Learner {learner['username']} updated successfully.", 'success')
            return redirect(url_for('admin_learners'))
    return render_template('admin_edit_learner.html', learner=learner)

@app.route('/admin/facilitators/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_edit_facilitator(user_id):
    with get_db() as conn:
        facilitator = conn.execute("SELECT * FROM users WHERE id = ? AND role = 'facilitator'", (user_id,)).fetchone()
        if not facilitator:
            flash('Facilitator not found.', 'danger')
            return redirect(url_for('admin_facilitators'))
        if request.method == 'POST':
            full_name, err = validate_full_name(request.form.get('full_name'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_edit_facilitator', user_id=user_id))
            phone, err = validate_kenya_phone(request.form.get('phone'), required=False)
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_edit_facilitator', user_id=user_id))
            job_title, err = validate_job_title(request.form.get('job_title'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_edit_facilitator', user_id=user_id))
            conn.execute('\n                UPDATE users SET full_name = ?, phone = ?, job_title = ?\n                WHERE id = ?\n                ', (full_name, phone, job_title, user_id))
            flash(f"Facilitator {facilitator['email']} updated successfully.", 'success')
            return redirect(url_for('admin_facilitators'))
    return render_template('admin_edit_facilitator.html', facilitator=facilitator)

@app.route('/admin/facilitators/<int:user_id>/areas', methods=['GET', 'POST'])
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_facilitator_areas(user_id):
    with get_db() as conn:
        facilitator = conn.execute("SELECT * FROM users WHERE id = ? AND role = 'facilitator'", (user_id,)).fetchone()
        if not facilitator:
            flash('Facilitator not found.', 'danger')
            return redirect(url_for('admin_facilitators'))
        all_areas = get_active_thematic_areas(conn)
        assigned_ids = set(get_facilitator_area_ids(conn, user_id))
        if request.method == 'POST':
            selected = request.form.getlist('thematic_area_ids')
            set_facilitator_thematic_areas(conn, user_id, selected, session['user_id'])
            flash(f"Thematic area assignments updated for {facilitator['full_name']}.", 'success')
            return redirect(url_for('admin_facilitator_areas', user_id=user_id))
    return render_template('admin_facilitator_areas.html', facilitator=facilitator, all_areas=all_areas, assigned_ids=assigned_ids)

@app.route('/admin/learners/<int:learner_id>/active', methods=['POST'])
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_set_learner_active(learner_id):
    active = request.form.get('active') == '1'
    with get_db() as conn:
        row = conn.execute('\n            SELECT l.id, u.id AS user_id, u.username, u.full_name\n            FROM learners l JOIN users u ON u.id = l.user_id\n            WHERE l.id = ?\n            ', (learner_id,)).fetchone()
        if not row:
            flash('Learner not found.', 'danger')
            return redirect(url_for('admin_learners'))
        conn.execute('UPDATE users SET is_active = ? WHERE id = ?', (1 if active else 0, row['user_id']))
        if active:
            flash(f"Learner {row['username']} ({row['full_name']}) reactivated.", 'success')
        else:
            flash(f"Learner {row['username']} ({row['full_name']}) deactivated. They can no longer sign in.", 'success')
    return redirect(request.referrer or url_for('admin_learners'))

@app.route('/admin/facilitators/<int:user_id>/active', methods=['POST'])
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_set_facilitator_active(user_id):
    active = request.form.get('active') == '1'
    with get_db() as conn:
        row = conn.execute("SELECT id, email, full_name FROM users WHERE id = ? AND role = 'facilitator'", (user_id,)).fetchone()
        if not row:
            flash('Facilitator not found.', 'danger')
            return redirect(url_for('admin_facilitators'))
        conn.execute('UPDATE users SET is_active = ? WHERE id = ?', (1 if active else 0, user_id))
        if active:
            flash(f"Facilitator {row['full_name']} reactivated.", 'success')
        else:
            flash(f"Facilitator {row['full_name']} deactivated. They can no longer sign in.", 'success')
    return redirect(request.referrer or url_for('admin_facilitators'))

@app.route('/admin/certificate/<int:learner_id>', methods=['POST'])
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_issue_certificate(learner_id):
    with get_db() as conn:
        summary = get_learner_progress_summary(conn, learner_id)
        if not summary['certificate_eligible']:
            flash('Learner has not completed all thematic areas.', 'danger')
            return redirect(url_for('admin_home'))
        conn.execute('\n            INSERT INTO certificates (learner_id, issued_by, notes)\n            VALUES (?, ?, ?)\n            ', (learner_id, session['user_id'], request.form.get('notes', '')))
        conn.execute("UPDATE learners SET status = 'graduated' WHERE id = ?", (learner_id,))
        flash('LCAF Certificate recorded.', 'success')
    return redirect(url_for('admin_home'))

@app.route('/admin/admins', methods=['GET', 'POST'])
@login_required(roles=['super_admin'])
def admin_admins():
    with get_db() as conn:
        if request.method == 'POST':
            username, err = validate_admin_username(request.form.get('username'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_admins'))
            full_name, err = validate_full_name(request.form.get('full_name'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_admins'))
            email, err = validate_email(request.form.get('email'), required=False)
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_admins'))
            if username_exists(conn, username):
                flash(f'Username {username} is already in use. Choose a different login name.', 'danger')
                return redirect(url_for('admin_admins'))
            if email and username_exists(conn, email):
                flash('That email is already registered to another account.', 'danger')
                return redirect(url_for('admin_admins'))
            conn.execute("\n                INSERT INTO users\n                (username, password_hash, full_name, email, role, must_change_password)\n                VALUES (?, ?, ?, ?, 'admin', 1)\n                ", (username, generate_password_hash(DEFAULT_ADMIN_PASSWORD), full_name, email))
            flash(f'Admin {full_name} registered. Login: {username} (initial password: {DEFAULT_ADMIN_PASSWORD}).', 'success')
            return redirect(url_for('admin_admins'))
        admins = get_regular_admins(conn)
    return render_template('admin_admins.html', admins=admins, default_password=DEFAULT_ADMIN_PASSWORD)

@app.route('/admin/admins/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required(roles=['super_admin'])
def admin_edit_admin(user_id):
    with get_db() as conn:
        admin_user = conn.execute("SELECT * FROM users WHERE id = ? AND role = 'admin'", (user_id,)).fetchone()
        if not admin_user:
            flash('Admin account not found.', 'danger')
            return redirect(url_for('admin_admins'))
        if request.method == 'POST':
            full_name, err = validate_full_name(request.form.get('full_name'))
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_edit_admin', user_id=user_id))
            email, err = validate_email(request.form.get('email'), required=False)
            if err:
                flash(err, 'danger')
                return redirect(url_for('admin_edit_admin', user_id=user_id))
            if email and username_exists(conn, email, exclude_user_id=user_id):
                flash('That email is already used by another account.', 'danger')
                return redirect(url_for('admin_edit_admin', user_id=user_id))
            conn.execute('\n                UPDATE users SET full_name = ?, email = ?\n                WHERE id = ?\n                ', (full_name, email, user_id))
            flash('Admin details updated.', 'success')
            return redirect(url_for('admin_admins'))
    return render_template('admin_edit_admin.html', admin_user=admin_user)

@app.route('/admin/admins/<int:user_id>/reset-password', methods=['POST'])
@login_required(roles=['super_admin'])
def admin_reset_admin_password(user_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ? AND role = 'admin'", (user_id,)).fetchone()
        if not row:
            flash('Admin account not found.', 'danger')
            return redirect(url_for('admin_admins'))
        conn.execute('\n            UPDATE users SET password_hash = ?, must_change_password = 1\n            WHERE id = ?\n            ', (generate_password_hash(DEFAULT_ADMIN_PASSWORD), user_id))
        clear_login_lockout(conn, row['username'])
        flash(f"Password for {row['username']} reset to {DEFAULT_ADMIN_PASSWORD}. Login lockout cleared if any.", 'success')
    return redirect(url_for('admin_admins'))

@app.route('/admin/admins/<int:user_id>/active', methods=['POST'])
@login_required(roles=['super_admin'])
def admin_set_admin_active(user_id):
    active = request.form.get('active') == '1'
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ? AND role = 'admin'", (user_id,)).fetchone()
        if not row:
            flash('Admin account not found.', 'danger')
            return redirect(url_for('admin_admins'))
        if not active and row['is_active']:
            active_admins = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role = 'admin' AND is_active = 1").fetchone()['c']
            if active_admins <= 1:
                flash('Cannot deactivate the only active admin. Register another admin first.', 'danger')
                return redirect(url_for('admin_admins'))
        conn.execute('UPDATE users SET is_active = ? WHERE id = ?', (1 if active else 0, user_id))
        if active:
            flash(f"Admin {row['full_name']} reactivated.", 'success')
        else:
            flash(f"Admin {row['full_name']} deactivated. They can no longer sign in.", 'success')
    return redirect(url_for('admin_admins'))

@app.route('/admin/areas/<int:area_id>/delete', methods=['POST'])
@login_required(roles=['super_admin'])
def admin_delete_area(area_id):
    with get_db() as conn:
        area = conn.execute('SELECT id, name, slug FROM thematic_areas WHERE id = ?', (area_id,)).fetchone()
        if not area:
            flash('Thematic area not found.', 'danger')
            return redirect(url_for('admin_areas'))
        confirm = (request.form.get('confirm_name') or '').strip()
        if confirm != area['name']:
            flash('Permanent delete cancelled. Type the thematic area name exactly to confirm.', 'danger')
            return redirect(url_for('admin_areas'))
        deleted = delete_thematic_area(conn, area_id)
        if not deleted:
            flash('Thematic area not found.', 'danger')
            return redirect(url_for('admin_areas'))
        log_event(conn, 'area_deleted', user_id=session['user_id'], username=session['username'], role=session['role'], detail=deleted, **_audit_request_meta())
        flash(f'''Thematic area "{deleted['name']}" and all its modules were permanently deleted.''', 'success')
    return redirect(url_for('admin_areas'))

@app.route('/admin/areas/<int:area_id>/modules/<int:module_id>/delete', methods=['POST'])
@login_required(roles=['super_admin'])
def admin_delete_module(area_id, module_id):
    with get_db() as conn:
        mod = conn.execute('\n            SELECT m.id, m.module_number, m.name\n            FROM modules m\n            WHERE m.id = ? AND m.thematic_area_id = ?\n            ', (module_id, area_id)).fetchone()
        if not mod:
            flash('Module not found.', 'danger')
            return redirect(url_for('admin_modules', area_id=area_id))
        confirm = (request.form.get('confirm_name') or '').strip()
        if confirm != mod['name']:
            flash('Permanent delete cancelled. Type the module name exactly to confirm.', 'danger')
            return redirect(url_for('admin_modules', area_id=area_id))
        deleted = delete_module(conn, area_id, module_id)
        if not deleted:
            flash('Module not found.', 'danger')
            return redirect(url_for('admin_modules', area_id=area_id))
        log_event(conn, 'module_deleted', user_id=session['user_id'], username=session['username'], role=session['role'], detail=deleted, **_audit_request_meta())
        flash(f"Module M{deleted['module_number']} ({deleted['name']}) was permanently deleted.", 'success')
    return redirect(url_for('admin_modules', area_id=area_id))

def _audit_filter_args(source=None):
    src = source or request.args
    return {'event_type': (src.get('event_type') or '').strip() or None, 'username': (src.get('username') or '').strip() or None, 'date_from': (src.get('date_from') or '').strip() or None, 'date_to': (src.get('date_to') or '').strip() or None}

@app.route('/admin/audit', methods=['GET', 'POST'])
@login_required(roles=['super_admin'])
def admin_audit():
    with get_db() as conn:
        if request.method == 'POST' and request.form.get('action') == 'clear_lockout':
            username = (request.form.get('username') or '').strip()
            if username:
                clear_login_lockout(conn, username)
                log_event(conn, 'lockout_cleared', user_id=session['user_id'], username=session['username'], role=session['role'], detail={'cleared_username': username}, **_audit_request_meta())
                flash(f'Login lockout cleared for {username}.', 'success')
            return redirect(url_for('admin_audit'))
        filters = _audit_filter_args()
        events = get_audit_events(conn, **filters, limit=300)
        lockouts = get_active_lockouts(conn)
    return render_template('admin_audit.html', events=events, lockouts=lockouts, event_labels=EVENT_LABELS, event_type=filters['event_type'] or '', username_filter=filters['username'] or '', date_from=filters['date_from'] or '', date_to=filters['date_to'] or '', max_attempts=MAX_FAILED_LOGIN_ATTEMPTS, lockout_hours=LOGIN_LOCKOUT_HOURS, eat_label=EAT_LABEL)

@app.route('/admin/audit/download')
@login_required(roles=['super_admin'])
def admin_audit_download():
    filters = _audit_filter_args()
    with get_db() as conn:
        events = get_audit_events(conn, **filters, limit=None)
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow([f'When ({EAT_LABEL})', 'Event type', 'Event label', 'Full name', 'Username', 'Role', 'IP address', 'Method', 'Endpoint', 'Path', 'Detail'])
    for e in events:
        writer.writerow([format_as_eat(e['created_at'], include_label=True), e['event_type'], EVENT_LABELS.get(e['event_type'], e['event_type']), e['full_name'] or '', e['username'] or '', e['role'] or '', e['ip_address'] or '', e['method'] or '', e['endpoint'] or '', e['path'] or '', e['detail'] or ''])
    parts = ['lcaf-activity']
    if filters['date_from']:
        parts.append(f"from-{filters['date_from']}")
    if filters['date_to']:
        parts.append(f"to-{filters['date_to']}")
    if filters['event_type']:
        parts.append(filters['event_type'])
    filename = '-'.join(parts) + f'-{date.today().isoformat()}.csv'
    return Response(output.getvalue(), mimetype='text/csv; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="{filename}"'})

@app.route('/admin/criteria')
@login_required(roles=ADMIN_ACCESS_ROLES)
def admin_criteria():
    with get_db() as conn:
        criteria = get_completion_criteria(conn)
    return render_template('admin_criteria.html', criteria=criteria)
if __name__ == '__main__':
    validate_production_config()
    init_db()
    seed_if_empty()
    url = f'http://{LCAF_HOST}:{LCAF_PORT}'
    print(f'LCAF Registry: {url}')
    if IS_PRODUCTION:
        print('Production mode — use Gunicorn for public hosting (see DEPLOY.txt).')
    app.run(debug=LCAF_DEBUG, host=LCAF_HOST, port=LCAF_PORT)
