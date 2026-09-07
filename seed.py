from datetime import date, timedelta
from werkzeug.security import generate_password_hash
from env_bootstrap import ensure_env
ensure_env(quiet=True)
from config import DEFAULT_SUPER_ADMIN_USERNAME, FACILITATOR_DEFAULT_PASSWORD, LEARNER_DEFAULT_PASSWORD, SEED_SAMPLE_DATA, get_super_admin_initial_password
from database import ensure_foundation_programme, ensure_progress_rows, get_db, init_db, recompute_learner_statuses, set_facilitator_thematic_areas
from programme_data import FOUNDATION_THEMATIC_AREAS

def seed_if_empty():
    init_db()
    with get_db() as conn:
        if conn.execute('SELECT COUNT(*) AS c FROM users').fetchone()['c'] > 0:
            return False

        def add_user(username, password, full_name, email, role, must_change_password=1):
            cur = conn.execute('\n                INSERT INTO users\n                (username, password_hash, full_name, email, role, must_change_password)\n                VALUES (?, ?, ?, ?, ?, ?)\n                ', (username, generate_password_hash(password), full_name, email, role, must_change_password))
            return cur.lastrowid
        ensure_env(quiet=False)
        super_pw = get_super_admin_initial_password()
        if not super_pw:
            raise RuntimeError('ADMINS_KEY missing after ensure_env. Run: python scripts/generate_env.py')
        super_id = add_user(DEFAULT_SUPER_ADMIN_USERNAME, super_pw, 'LCAF Super Administrator', 'info@lightcomeafrica.com', 'super_admin', must_change_password=1)
        ensure_foundation_programme(conn)
        if not SEED_SAMPLE_DATA:
            print('No day-to-day admin was seeded (by design).')
            return True
        fac_dl = add_user('collinsmilimo47@gmail.com', FACILITATOR_DEFAULT_PASSWORD, 'Collins Milimo', 'collinsmilimo47@gmail.com', 'facilitator', must_change_password=1)
        conn.execute('UPDATE users SET phone = ?, job_title = ? WHERE id = ?', ('+254714421248', 'Digital Literacy Facilitator', fac_dl))
        fac_wr = add_user('grace@lightcomeafrica.com', FACILITATOR_DEFAULT_PASSWORD, 'Grace Wanjiku', 'grace@lightcomeafrica.com', 'facilitator', must_change_password=1)
        conn.execute('UPDATE users SET job_title = ? WHERE id = ?', ('Work Ethics & Employability Facilitator', fac_wr))
        area_ids = {}
        for _name, slug, _desc, _order, _mods in FOUNDATION_THEMATIC_AREAS:
            row = conn.execute('SELECT id FROM thematic_areas WHERE slug = ?', (slug,)).fetchone()
            if row:
                area_ids[slug] = row['id']
        set_facilitator_thematic_areas(conn, fac_dl, [str(area_ids['digital-literacy-internet-ethics'])], super_id)
        set_facilitator_thematic_areas(conn, fac_wr, [str(area_ids['work-ethics-employability'])], super_id)
        dl_mods = conn.execute("\n            SELECT m.id, m.module_number FROM modules m\n            JOIN thematic_areas ta ON ta.id = m.thematic_area_id\n            WHERE ta.slug = 'digital-literacy-internet-ethics'\n            ORDER BY m.module_number\n            ").fetchall()
        base = date(2026, 6, 1)
        for i, mod in enumerate(dl_mods):
            session_date = (base + timedelta(weeks=i)).isoformat()
            conn.execute("\n                INSERT INTO delivery_sessions\n                (module_id, scheduled_date, scheduled_time, conducted_date, conducted_time,\n                 session_type, title, status, facilitator_id)\n                VALUES (?, ?, '09:00', ?, '09:00', 'online', ?, 'completed', ?)\n                ", (mod['id'], session_date, session_date, f"Digital Literacy M{mod['module_number']} — Live Session", fac_dl))
        for mod in dl_mods[5:]:
            future_date = (date(2026, 7, 15) + timedelta(weeks=mod['module_number'] - 6)).isoformat()
            conn.execute("\n                INSERT INTO delivery_sessions\n                (module_id, scheduled_date, scheduled_time, session_type, title, status, facilitator_id)\n                VALUES (?, ?, '14:00', 'both', ?, 'scheduled', ?)\n                ", (mod['id'], future_date, f"Digital Literacy M{mod['module_number']} — Rolling Session", fac_dl))
        learners_data = [('LCA0001', 'Amina Ochieng', '12345601', 'amina@example.com', '2026-05-15', 'Kisumu NGO'), ('LCA0002', 'Brian Otieno', '23456702', 'brian@example.com', '2026-06-20', 'County Office'), ('LCA0003', 'Cynthia Akinyi', '34567803', 'cynthia@example.com', '2026-07-01', 'Community School'), ('LCA0004', 'David Mwangi', '45678904', 'david@example.com', '2026-06-01', 'Local SME'), ('LCA0005', 'Eva Wambui', '56789005', 'eva@example.com', '2026-07-10', 'Health Clinic')]
        for learner_id, name, national_id, email, join, placement in learners_data:
            uid = add_user(learner_id, LEARNER_DEFAULT_PASSWORD, name, email, 'learner', must_change_password=1)
            cur = conn.execute('\n                INSERT INTO learners\n                (user_id, join_date, placement_org, national_id)\n                VALUES (?, ?, ?, ?)\n                ', (uid, join, placement, national_id))
            lid = cur.lastrowid
            ensure_progress_rows(conn, lid)
            recompute_learner_statuses(conn, lid)
        brian = conn.execute("SELECT l.id FROM learners l JOIN users u ON u.id = l.user_id WHERE u.username = 'LCA0002'").fetchone()
        for mod in dl_mods[:5]:
            sess = conn.execute('SELECT id FROM delivery_sessions WHERE module_id = ? LIMIT 1', (mod['id'],)).fetchone()
            if sess:
                conn.execute('\n                    INSERT INTO attendance_records (delivery_session_id, learner_id, attended, marked_by)\n                    VALUES (?, ?, 1, ?)\n                    ', (sess['id'], brian['id'], fac_dl))
            conn.execute('\n                UPDATE learner_module_progress\n                SET attended_at = datetime(\'now\'), assignment_submitted_at = datetime(\'now\'),\n                    status = \'completed\', completed_at = datetime(\'now\'),\n                    criteria_met = \'{"attendance": true, "assignment": true}\'\n                WHERE learner_id = ? AND module_id = ?\n                ', (brian['id'], mod['id']))
        amina = conn.execute("SELECT l.id FROM learners l JOIN users u ON u.id = l.user_id WHERE u.username = 'LCA0001'").fetchone()
        for mod in dl_mods:
            sess = conn.execute("SELECT id FROM delivery_sessions WHERE module_id = ? AND status = 'completed' LIMIT 1", (mod['id'],)).fetchone()
            if sess:
                conn.execute('\n                    INSERT OR IGNORE INTO attendance_records\n                    (delivery_session_id, learner_id, attended, marked_by)\n                    VALUES (?, ?, 1, ?)\n                    ', (sess['id'], amina['id'], fac_dl))
            conn.execute('\n                UPDATE learner_module_progress\n                SET attended_at = datetime(\'now\'), assignment_submitted_at = datetime(\'now\'),\n                    status = \'completed\', completed_at = datetime(\'now\'),\n                    criteria_met = \'{"attendance": true, "assignment": true}\'\n                WHERE learner_id = ? AND module_id = ?\n                ', (amina['id'], mod['id']))
        recompute_learner_statuses(conn, brian['id'])
        recompute_learner_statuses(conn, amina['id'])
        for row in conn.execute('SELECT id FROM learners').fetchall():
            recompute_learner_statuses(conn, row['id'])
        return True
if __name__ == '__main__':
    seed_if_empty()
