# app.py
from flask import Flask, render_template, request, jsonify
import requests
import sqlite3
import os
import threading
import time

app = Flask(__name__)
DB_PATH = 'events.db'

# ========== DATABASE INIT ==========
def init_db():
    if not os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            is_admin INTEGER NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            date TEXT NOT NULL,
            venue TEXT NOT NULL,
            price REAL NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0)''')
        c.executemany('INSERT INTO users (username, is_admin) VALUES (?, ?)', [
            ('admin', 1), ('metalhead123', 0), ('rockfan2024', 0)
        ])
        events = [
            ("Metallica Live", "2025-07-20", "OAKA Stadium", 75.0, 1),
            ("Rockwave Festival", "2025-07-28", "TerraVibe Park", 60.0, 2),
            ("Slipknot Night", "2025-08-10", "Technopolis", 55.0, 3),
            ("Judas Priest Reunion", "2025-08-15", "Gazi Music Hall", 70.0, 4),
            ("Sabaton & Guests", "2025-08-25", "Faliro Indoor Hall", 50.0, 5),
        ]
        c.executemany('INSERT INTO events (name, date, venue, price, sort_order) VALUES (?, ?, ?, ?, ?)', events)
        conn.commit()
        conn.close()

def get_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, username, is_admin FROM users')
    users = [{'id': r[0], 'username': r[1], 'is_admin': r[2]} for r in c.fetchall()]
    conn.close()
    return users

def get_events():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, name, date, venue, price, sort_order FROM events ORDER BY sort_order ASC, id ASC')
    events = [{'id': r[0], 'name': r[1], 'date': r[2], 'venue': r[3], 'price': r[4], 'sort_order': r[5]} for r in c.fetchall()]
    conn.close()
    return events

def migrate_events_sort_order():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    cols = [row[1] for row in c.execute("PRAGMA table_info(events)").fetchall()]
    if "sort_order" not in cols:
        c.execute("ALTER TABLE events ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
        # give existing rows a stable order based on id
        c.execute("SELECT id FROM events ORDER BY id ASC")
        ids = [r[0] for r in c.fetchall()]
        for i, eid in enumerate(ids, start=1):
            c.execute("UPDATE events SET sort_order=? WHERE id=?", (i, eid))
        conn.commit()
    conn.close()

def repair_sort_order():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM events ORDER BY id ASC")
    ids = [r[0] for r in c.fetchall()]
    for i, eid in enumerate(ids, start=1):
        c.execute("UPDATE events SET sort_order=? WHERE id=?", (i, eid))
    conn.commit()
    conn.close()

def _blind_fetch(url: str, timeout=(2, 5), allow_redirects=True):
    try:
        requests.get(url, timeout=timeout, allow_redirects=allow_redirects)
    except Exception:
        # Swallow errors; blind mode must not leak anything to caller
        pass

# ========== ROUTES ==========

@app.route('/')
def index():
    return render_template('index.html')

# --- Public JSON Endpoints ---
@app.route('/users')
def users():
    return jsonify(get_users())

@app.route('/events')
def events():
    return jsonify(get_events())

@app.route('/price', methods=['POST'])
def get_price():
    data = request.get_json(silent=True) or {}
    event_id = data.get('event_id')

    # Optional toggles for URL mode
    blind = bool(data.get('blind'))                      # default False: non-blind (returns data)
    allow_redirects = data.get('allow_redirects', True)  # follow redirects by default
    timeout = data.get('timeout', 5)                     # read timeout seconds (connect timeout fixed at 2s)

    # URL branch (SSRF sink)
    if isinstance(event_id, str) and (event_id.startswith("http://") or event_id.startswith("https://")):
        if blind:
            # BOOLEAN-BLIND: do the fetch synchronously, reveal only success/failure (status==200)
            try:
                resp = requests.get(event_id, allow_redirects=allow_redirects, timeout=(2, timeout))
                accepted = (resp.status_code == 200)  # strictly 200 as requested
            except Exception:
                accepted = False
            # return a uniform, content-free boolean result
            return jsonify({"accepted": accepted}), 200

        # NON-BLIND: return upstream data (what you had before, plus timeouts)
        try:
            resp = requests.get(event_id, allow_redirects=allow_redirects, timeout=(2, timeout))
            return jsonify({
                "target": event_id,
                "status": resp.status_code,
                "content": resp.text
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 502

    # Normal DB lookup path (unchanged)
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT price FROM events WHERE id=?', (event_id,))
        row = c.fetchone()
        conn.close()
        if row:
            return jsonify({'price': row[0]})
        return jsonify({'error': 'Event not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ======== ADMIN PANEL (NO AUTH) =========

@app.route('/admin')
def admin_panel():
    return render_template('admin_panel.html')

@app.route('/admin/users')
def admin_users():
    return jsonify(get_users())

@app.route('/admin/events')
def admin_events():
    return jsonify(get_events())

# ----- ADMIN JSON ENDPOINTS -----
@app.route('/admin/users/add', methods=['POST'])
def admin_add_user():
    username = request.json.get('username')
    is_admin = 1 if request.json.get('is_admin') else 0
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('INSERT INTO users (username, is_admin) VALUES (?, ?)', (username, is_admin))
        conn.commit()
        return jsonify({'status': 'ok'})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Username already exists'}), 409
    finally:
        conn.close()

@app.route('/admin/users/delete', methods=['GET', 'POST'])
def admin_delete_user():
    # accept either querystring or JSON (keeps your SSRF tests easy)
    username = request.args.get('username')
    if not username and request.is_json:
        payload = request.get_json(silent=True) or {}
        username = payload.get('username')

    if not username:
        return jsonify({'error': 'Missing username'}), 400

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM users WHERE username=? AND is_admin=0', (username,))
    deleted = c.rowcount
    conn.commit()
    conn.close()

    if deleted == 0:
        # Either user doesn’t exist or was admin (guard prevents admin deletion)
        return jsonify({'error': 'User not found or cannot be deleted'}), 404

    return jsonify({'status': 'deleted', 'deleted': deleted}), 200

@app.route('/admin/events/add', methods=['POST'])
def admin_add_event():
    data = request.json
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO events (name, date, venue, price) VALUES (?, ?, ?, ?)',
              (data['name'], data['date'], data['venue'], data['price']))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})

@app.route('/admin/events/delete', methods=['GET', 'POST'])
def admin_delete_event():
    # accept either querystring or JSON
    name = request.args.get('name')
    if not name and request.is_json:
        payload = request.get_json(silent=True) or {}
        name = payload.get('name')

    if not name:
        return jsonify({'error': 'Missing event name'}), 400

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM events WHERE name=?', (name,))
    deleted = c.rowcount
    conn.commit()
    conn.close()

    if deleted == 0:
        return jsonify({'error': 'Event not found'}), 404

    return jsonify({'status': 'deleted', 'deleted': deleted}), 200

@app.route('/admin/events/reorder', methods=['POST'])
def admin_reorder_events():
    payload = request.get_json(silent=True) or {}
    event_id = payload.get('event_id')
    direction = payload.get('direction')  # "up" or "down"

    if not event_id or direction not in ("up", "down"):
        return jsonify({"error": "Missing event_id or invalid direction"}), 400

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT id, sort_order FROM events WHERE id=?", (event_id,))
    cur = c.fetchone()
    if not cur:
        conn.close()
        return jsonify({"error": "Event not found"}), 404

    cur_id, cur_order = cur

    if direction == "up":
        c.execute("SELECT id, sort_order FROM events WHERE sort_order < ? ORDER BY sort_order DESC LIMIT 1", (cur_order,))
    else:
        c.execute("SELECT id, sort_order FROM events WHERE sort_order > ? ORDER BY sort_order ASC LIMIT 1", (cur_order,))

    neighbor = c.fetchone()
    if not neighbor:
        conn.close()
        return jsonify({"status": "ok", "note": "Already at edge"}), 200

    nb_id, nb_order = neighbor

    # swap
    c.execute("UPDATE events SET sort_order=? WHERE id=?", (nb_order, cur_id))
    c.execute("UPDATE events SET sort_order=? WHERE id=?", (cur_order, nb_id))

    conn.commit()
    conn.close()
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    init_db()
    migrate_events_sort_order()
    repair_sort_order()
    app.run(debug=True)
