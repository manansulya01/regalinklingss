import os
import re
import json
import time
import socket
import secrets
import logging
import sqlite3
from datetime import datetime
from functools import wraps
from jinja2 import DictLoader

from flask import (Flask, render_template, request, redirect, url_for,
                   session, send_file, abort, flash, jsonify, Response)
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOOKS_DIR = os.path.join(BASE_DIR, "books")
USERS_FILE = os.path.join(BASE_DIR, "users.json")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
SECRET_FILE = os.path.join(BASE_DIR, "secret_key.txt")

ALLOWED_EXTENSIONS = {".pdf"}
ACTIVE_WINDOW_SECONDS = 15 * 60
ONLINE_WINDOW_SECONDS = 60
MAX_MESSAGE_LENGTH = 2000
DB_FILE = os.path.join(BASE_DIR, "regal.db")

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 8080,
    "server_name": "Regal Inklings",
    "max_upload_mb": 500,
}

log = logging.getLogger("regal")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s",
                    datefmt="%H:%M:%S")

active_users = {}  # username -> last seen epoch


def timestamp():
    return datetime.now().strftime("%H:%M:%S")


def log_event(msg):
    print(f"[{timestamp()}] {msg}")


def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError("config must be an object")
    except (OSError, ValueError, json.JSONDecodeError):
        cfg = dict(DEFAULT_CONFIG)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)

    # Keep a broken/hand-edited config from preventing the server from starting.
    if not isinstance(cfg.get("host"), str) or not cfg["host"].strip():
        cfg["host"] = DEFAULT_CONFIG["host"]
    try:
        cfg["port"] = int(cfg.get("port", DEFAULT_CONFIG["port"]))
    except (TypeError, ValueError):
        cfg["port"] = DEFAULT_CONFIG["port"]
    if not (1 <= cfg["port"] <= 65535):
        cfg["port"] = DEFAULT_CONFIG["port"]
    if not isinstance(cfg.get("server_name"), str) or not cfg["server_name"].strip():
        cfg["server_name"] = DEFAULT_CONFIG["server_name"]
    try:
        cfg["max_upload_mb"] = int(cfg.get("max_upload_mb", DEFAULT_CONFIG["max_upload_mb"]))
    except (TypeError, ValueError):
        cfg["max_upload_mb"] = DEFAULT_CONFIG["max_upload_mb"]
    if cfg["max_upload_mb"] < 1:
        cfg["max_upload_mb"] = DEFAULT_CONFIG["max_upload_mb"]
    return cfg


def load_users():
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        users = data.get("users", []) if isinstance(data, dict) else []
        if not isinstance(users, list):
            raise ValueError("users must be a list")
        return users
    except (OSError, ValueError, json.JSONDecodeError, KeyError):
        users = [{
            "username": "k4ge",
            "password_hash": generate_password_hash(
                os.environ.get("REGAL_DEFAULT_ADMIN_PASSWORD", "Regal123!")
            ),
            "role": "admin",
            "display_name": "Manan Sulya",
        }]
        save_users(users)
        return users


def save_users(users):
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"users": users}, f, indent=4)
    os.replace(tmp, USERS_FILE)


def find_user(username):
    for u in load_users():
        if not isinstance(u, dict):
            continue
        if u.get("username") == username and u.get("password_hash") and u.get("role"):
            return u
    return None


def create_default_users():
    # Optional override for deployments. Clean installs work immediately.
    default_admin_password = os.environ.get("REGAL_DEFAULT_ADMIN_PASSWORD", "Regal123!")
    if not default_admin_password:
        default_admin_password = "Regal123!"

    users = [{
        "username": "k4ge",
        "password_hash": generate_password_hash(default_admin_password),
        "role": "admin",
        "display_name": "Manan Sulya",
    }]
    save_users(users)
    return users


def get_secret_key():
    if os.path.exists(SECRET_FILE):
        try:
            with open(SECRET_FILE, "r", encoding="utf-8") as f:
                key = f.read().strip()
            if key:
                return key
        except OSError:
            pass
    key = secrets.token_hex(32)
    with open(SECRET_FILE, "w", encoding="utf-8") as f:
        f.write(key)
    return key


def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def human_size(num):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num < 1024 or unit == "TB":
            if unit == "B":
                return f"{num} {unit}"
            return f"{num:.1f} {unit}"
        num /= 1024


def list_books():
    books = []
    if os.path.isdir(BOOKS_DIR):
        for name in sorted(os.listdir(BOOKS_DIR), key=str.lower):
            path = os.path.join(BOOKS_DIR, name)
            if os.path.isfile(path):
                ext = os.path.splitext(name)[1].lower()
                if ext in ALLOWED_EXTENSIONS:
                    books.append({
                        "filename": name,
                        "title": os.path.splitext(name)[0],
                        "ext": ext.lstrip(".").upper(),
                        "size": os.path.getsize(path),
                        "size_human": human_size(os.path.getsize(path)),
                        "mtime": os.path.getmtime(path),
                    })
    return books


def safe_book_path(filename):
    """Resolve filename inside books dir, or None if invalid/unsafe."""
    if not filename or filename in (".", ".."):
        return None
    candidate = os.path.realpath(os.path.join(BOOKS_DIR, filename))
    real_books = os.path.realpath(BOOKS_DIR)
    if candidate != real_books and candidate.startswith(real_books + os.sep):
        if os.path.isfile(candidate):
            ext = os.path.splitext(candidate)[1].lower()
            if ext in ALLOWED_EXTENSIONS:
                return candidate
    return None


def touch_active(username):
    active_users[username] = time.time()


def active_now():
    cutoff = time.time() - ACTIVE_WINDOW_SECONDS
    return sorted(u for u, t in active_users.items() if t >= cutoff)


def is_online(username):
    t = active_users.get(username)
    return t is not None and (time.time() - t) <= ONLINE_WINDOW_SECONDS


def login_required(role=None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = session.get("username")
            if not user:
                return redirect(url_for("login"))
            if role and session.get("role") != role:
                abort(403)
            touch_active(user)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_hex(16)
        session["csrf_token"] = token
    return token


def csrf_ok():
    sent = request.form.get("csrf_token", "")
    return sent and sent == session.get("csrf_token")


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT NOT NULL,
            body TEXT NOT NULL,
            ts REAL NOT NULL,
            room TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room, id)")
        conn.execute("""CREATE TABLE IF NOT EXISTS reads (
            username TEXT NOT NULL,
            room TEXT NOT NULL,
            last_id INTEGER NOT NULL,
            PRIMARY KEY (username, room)
        )""")
        conn.commit()
    finally:
        conn.close()


def dm_room(a, b):
    return "dm:" + ":".join(sorted([a, b]))


def room_for_request(args, username):
    """Return room name or None if unauthorized/invalid."""
    with_user = args.get("with", "").strip()
    if with_user:
        if with_user == username or not find_user(with_user):
            return None
        return dm_room(username, with_user)
    room = args.get("room", "group")
    if room != "group":
        return None
    return "group"


def unread_counts(username):
    conn = db()
    try:
        rows = conn.execute(
            "SELECT room, MAX(id) AS max_id FROM messages WHERE sender != ? GROUP BY room",
            (username,)).fetchall()
        reads = {}
        for r in conn.execute("SELECT room, last_id FROM reads WHERE username=?", (username,)):
            reads[r["room"]] = r["last_id"]
        group = 0
        dms = {}
        for row in rows:
            room = row["room"]
            last_id = reads.get(room, 0)
            n = conn.execute(
                "SELECT COUNT(*) c FROM messages WHERE room=? AND id>? AND sender!=?",
                (room, last_id, username)).fetchone()["c"]
            if n > 0:
                if room == "group":
                    group = n
                else:
                    parts = room.split(":")[1:]
                    other = parts[0] if parts[1] == username else parts[1]
                    dms[other] = n
        return {"group": group, "dms": dms,
                "total": group + sum(dms.values())}
    finally:
        conn.close()


def mark_read(username, room):
    conn = db()
    try:
        row = conn.execute("SELECT MAX(id) m FROM messages WHERE room=?", (room,)).fetchone()
        last_id = row["m"] or 0
        conn.execute("INSERT INTO reads(username, room, last_id) VALUES(?,?,?) "
                     "ON CONFLICT(username, room) DO UPDATE SET last_id=excluded.last_id",
                     (username, room, last_id))
        conn.commit()
    finally:
        conn.close()


def csrf_header_ok():
    token = request.headers.get("X-CSRFToken", "")
    return token and token == session.get("csrf_token")



# ================= SINGLE-FILE EMBEDDED ASSETS =================
TEMPLATES = {
    "_nav.html": "<nav class=\"nav\">\n    {% if session['role'] == 'admin' %}\n    <a href=\"{{ url_for('admin') }}\" class=\"{{ 'active' if page == 'admin' }}\">DASHBOARD</a>\n    {% endif %}\n    <a href=\"{{ url_for('library') }}\" class=\"{{ 'active' if page == 'library' }}\">LIBRARY</a>\n    <a href=\"{{ url_for('chat') }}\" class=\"{{ 'active' if page == 'chat' }}\">CHAT <span id=\"nav-badge\" class=\"badge\" style=\"display:none\"></span></a>\n    <a href=\"{{ url_for('members') }}\" class=\"{{ 'active' if page == 'members' }}\">MEMBERS</a>\n    <a href=\"{{ url_for('logout') }}\">LOGOUT</a>\n</nav>\n<nav class=\"bottomnav\">\n    {% if session['role'] == 'admin' %}\n    <a href=\"{{ url_for('admin') }}\" class=\"{{ 'active' if page == 'admin' }}\">&#128202;<span>Admin</span></a>\n    {% endif %}\n    <a href=\"{{ url_for('library') }}\" class=\"{{ 'active' if page == 'library' }}\">&#128218;<span>Library</span></a>\n    <a href=\"{{ url_for('chat') }}\" class=\"{{ 'active' if page == 'chat' }}\">&#128172;<span>Chat</span></a>\n    <a href=\"{{ url_for('members') }}\" class=\"{{ 'active' if page == 'members' }}\">&#128101;<span>Members</span></a>\n    <a href=\"{{ url_for('logout') }}\">&#128682;<span>Logout</span></a>\n</nav>\n",
    "admin.html": "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>Regal Inklings — Admin</title>\n<link rel=\"stylesheet\" href=\"{{ url_for('static', filename='style.css') }}\">\n<script src=\"{{ url_for('static', filename='app.js') }}\" defer></script>\n<script src=\"{{ url_for('static', filename='chat.js') }}\" defer></script>\n</head>\n<body>\n{% set page = 'admin' %}\n<div class=\"wrap wide\">\n    <header class=\"brand\"><h1>REGAL INKLINGS</h1><div class=\"sub\">The librarian's desk — {{ display_name }}</div></header>\n    {% include '_nav.html' %}\n\n    {% with messages = get_flashed_messages() %}\n        {% for m in messages %}<div class=\"flash\">{{ m }}</div>{% endfor %}\n    {% endwith %}\n\n    <h2>OVERVIEW</h2>\n    <div class=\"stats\">\n        <div class=\"stat\"><div class=\"n\">{{ books|length }}</div><div class=\"l\">Books</div></div>\n        <div class=\"stat\"><div class=\"n\">{{ member_count }}</div><div class=\"l\">Members</div></div>\n        <div class=\"stat\"><div class=\"n\">{{ online_count }}</div><div class=\"l\">Online Now</div></div>\n    </div>\n\n    <h2 id=\"books\">BOOK LIBRARY</h2>\n    <div class=\"card\">\n        {% if books %}\n        <table>\n            {% for book in books %}\n            <tr>\n                <td>{{ book.filename }}</td>\n                <td class=\"muted\">{{ book.size_human }}</td>\n                <td style=\"text-align:right;\">\n                    <form class=\"inline-form\" method=\"post\" action=\"{{ url_for('delete_book') }}\"\n                          onsubmit=\"return confirm('Are you sure you want to delete:\\n\\n{{ book.filename }}?');\">\n                        <input type=\"hidden\" name=\"csrf_token\" value=\"{{ csrf_token() }}\">\n                        <input type=\"hidden\" name=\"filename\" value=\"{{ book.filename }}\">\n                        <button class=\"btn danger small\" type=\"submit\">DELETE</button>\n                    </form>\n                </td>\n            </tr>\n            {% endfor %}\n        </table>\n        {% else %}\n        <p>No books have been uploaded yet.</p>\n        {% endif %}\n    </div>\n\n    <div class=\"card\">\n        <h3>Upload PDF</h3>\n        <form method=\"post\" action=\"{{ url_for('upload') }}\" enctype=\"multipart/form-data\" id=\"upload-form\">\n            <input type=\"hidden\" name=\"csrf_token\" value=\"{{ csrf_token() }}\">\n            <input type=\"file\" name=\"book\" accept=\".pdf\" required>\n            <p style=\"margin-top:12px;\"><button class=\"btn\" type=\"submit\" id=\"upload-btn\">UPLOAD PDF</button></p>\n        </form>\n    </div>\n\n    <h2 id=\"users\">MEMBERS</h2>\n    <div class=\"card\">\n        <h3>Create Student Account</h3>\n        <form method=\"post\" action=\"{{ url_for('create_user') }}\">\n            <input type=\"hidden\" name=\"csrf_token\" value=\"{{ csrf_token() }}\">\n            <label>Username</label>\n            <input type=\"text\" name=\"username\" required>\n            <label>Password</label>\n            <input type=\"text\" name=\"password\" required>\n            <p style=\"margin-top:12px;\"><button class=\"btn\" type=\"submit\">CREATE ACCOUNT</button></p>\n        </form>\n    </div>\n\n    <div class=\"card\">\n        <table>\n            {% for u in students %}\n            <tr>\n                <td>{{ u.username }}</td>\n                <td>\n                    <form class=\"inline-form row\" method=\"post\" action=\"{{ url_for('reset_password') }}\">\n                        <input type=\"hidden\" name=\"csrf_token\" value=\"{{ csrf_token() }}\">\n                        <input type=\"hidden\" name=\"username\" value=\"{{ u.username }}\">\n                        <input type=\"text\" name=\"password\" placeholder=\"New password\" required style=\"max-width:160px;\">\n                        <button class=\"btn small secondary\" type=\"submit\">RESET PASSWORD</button>\n                    </form>\n                </td>\n                <td style=\"text-align:right;\">\n                    <form class=\"inline-form\" method=\"post\" action=\"{{ url_for('delete_user') }}\"\n                          onsubmit=\"return confirm('Are you sure you want to delete user:\\n\\n{{ u.username }}?');\">\n                        <input type=\"hidden\" name=\"csrf_token\" value=\"{{ csrf_token() }}\">\n                        <input type=\"hidden\" name=\"username\" value=\"{{ u.username }}\">\n                        <button class=\"btn danger small\" type=\"submit\">DELETE</button>\n                    </form>\n                </td>\n            </tr>\n            {% endfor %}\n        </table>\n    </div>\n\n    <h2>CHAT</h2>\n    <div class=\"card\">\n        <p><a class=\"btn secondary small\" href=\"{{ url_for('chat') }}\">OPEN GROUP CHAT</a></p>\n        <h3 style=\"margin-top:16px;\">Recent Group Messages</h3>\n        {% if recent_messages %}\n        <table>\n            {% for m in recent_messages %}\n            <tr>\n                <td><strong>{{ m.sender }}</strong><br><span class=\"muted\">{{ m.body[:80] }}</span></td>\n                <td style=\"text-align:right;\">\n                    <form class=\"inline-form\" method=\"post\" action=\"{{ url_for('admin_delete_message') }}\"\n                          onsubmit=\"return confirm('Delete this message?');\">\n                        <input type=\"hidden\" name=\"csrf_token\" value=\"{{ csrf_token() }}\">\n                        <input type=\"hidden\" name=\"id\" value=\"{{ m.id }}\">\n                        <button class=\"btn danger small\" type=\"submit\">DELETE</button>\n                    </form>\n                </td>\n            </tr>\n            {% endfor %}\n        </table>\n        {% else %}\n        <p class=\"muted\">No group messages yet.</p>\n        {% endif %}\n    </div>\n\n    <div class=\"card\">\n        <h3>Change Admin Password</h3>\n        <form method=\"post\" action=\"{{ url_for('change_password') }}\">\n            <input type=\"hidden\" name=\"csrf_token\" value=\"{{ csrf_token() }}\">\n            <label>Current Password</label>\n            <input type=\"password\" name=\"current_password\" required>\n            <label>New Password</label>\n            <input type=\"password\" name=\"new_password\" required>\n            <p style=\"margin-top:12px;\"><button class=\"btn\" type=\"submit\">CHANGE PASSWORD</button></p>\n        </form>\n    </div>\n</div>\n<script>\ndocument.getElementById('upload-form').addEventListener('submit', function(){\n    document.getElementById('upload-btn').disabled = true;\n    document.getElementById('upload-btn').textContent = 'UPLOADING...';\n});\n</script>\n</body>\n</html>\n",
    "chat.html": "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>Regal Inklings — Chat</title>\n<link rel=\"stylesheet\" href=\"{{ url_for('static', filename='style.css') }}\">\n<script src=\"{{ url_for('static', filename='app.js') }}\"></script>\n<script src=\"{{ url_for('static', filename='chat.js') }}\" defer></script>\n</head>\n<body>\n{% set page = 'chat' %}\n<div class=\"wrap wide\">\n    <header class=\"brand\">\n        <h1>REGAL INKLINGS</h1>\n        <div class=\"sub\">The reading room.</div>\n    </header>\n    {% include '_nav.html' %}\n\n    <div class=\"chat-layout\">\n        <aside class=\"chat-side\" id=\"chat-side\">\n            <h3>GROUPS</h3>\n            <a href=\"{{ url_for('chat') }}\" class=\"side-link {{ 'active' if not dm }}\">💬 Regal Inklings <span class=\"badge\" id=\"badge-group\" style=\"display:none\"></span></a>\n            <h3>DIRECT MESSAGES</h3>\n            <div id=\"dm-list\">\n                {% for other in others %}\n                <a href=\"{{ url_for('chat', with=other) }}\" class=\"side-link {{ 'active' if dm == other }}\">{{ other }} <span class=\"badge\" data-dm=\"{{ other }}\" style=\"display:none\"></span></a>\n                {% endfor %}\n            </div>\n        </aside>\n\n        <main class=\"chat-main\">\n            <div class=\"chat-head\">\n                {% if dm %}\n                <a href=\"{{ url_for('chat') }}\" class=\"back\">←</a>\n                <strong>{{ dm }}</strong> <span class=\"dot {{ 'online' if dm_online else 'offline' }}\"></span> <span class=\"muted\" id=\"dm-status\">{{ 'Online' if dm_online else 'Offline' }}</span>\n                {% else %}\n                <strong>REGAL INKLINGS</strong> <span class=\"muted\">Group Chat</span>\n                {% endif %}\n            </div>\n\n            <div id=\"messages\" class=\"messages\" data-me=\"{{ username }}\" data-dm=\"{{ dm or '' }}\"></div>\n\n            <form id=\"send-form\" class=\"sendbar\">\n                <input type=\"hidden\" id=\"csrf\" name=\"csrf_token\" value=\"{{ csrf_token() }}\">\n                <textarea id=\"body\" rows=\"1\" maxlength=\"2000\" placeholder=\"Type a message...\"></textarea>\n                <button class=\"btn small\" type=\"submit\" id=\"send-btn\">SEND</button>\n            </form>\n            <p id=\"chat-error\" class=\"error\" style=\"display:none\"></p>\n        </main>\n    </div>\n</div>\n</body>\n</html>\n",
    "error.html": "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>Regal Inklings — {{ code }}</title>\n<link rel=\"stylesheet\" href=\"{{ url_for('static', filename='style.css') }}\">\n</head>\n<body>\n<div class=\"wrap\">\n    <header class=\"brand\"><h1>REGAL INKLINGS</h1></header>\n    <div class=\"card\" style=\"text-align:center;\">\n        <h2>{{ code }}</h2>\n        <p>{{ message }}</p>\n        <p style=\"margin-top:16px;\"><a class=\"btn\" href=\"{{ url_for('library') }}\">RETURN TO LIBRARY</a></p>\n    </div>\n</div>\n</body>\n</html>\n",
    "library.html": "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>Regal Inklings — Library</title>\n<link rel=\"stylesheet\" href=\"{{ url_for('static', filename='style.css') }}\">\n<script src=\"{{ url_for('static', filename='app.js') }}\" defer></script>\n</head>\n<body>\n<div class=\"wrap\">\n    <header class=\"brand\"><h1>REGAL INKLINGS</h1></header>\n    <p style=\"text-align:center;\">Welcome, <strong>{{ username }}</strong></p>\n    <nav class=\"nav\">\n        <a href=\"{{ url_for('library') }}\">Library</a>\n        <a href=\"{{ url_for('logout') }}\">Log Out</a>\n    </nav>\n\n    <input type=\"text\" id=\"search\" placeholder=\"Search books...\" class=\"search\" oninput=\"filterBooks()\">\n\n    {% if books %}\n        {% for book in books %}\n        <div class=\"card book book-card\" data-title=\"{{ book.title }}\">\n            <div>\n                <div class=\"title\">📕 {{ book.title }}</div>\n                <div class=\"meta\">{{ book.ext }} • {{ book.size_human }}</div>\n            </div>\n            <a class=\"btn download-btn\" href=\"{{ url_for('download', filename=book.filename) }}\" download>DOWNLOAD PDF</a>\n        </div>\n        {% endfor %}\n    {% else %}\n        <div class=\"card\" style=\"text-align:center;\">\n            <p>Your library is currently empty.</p>\n            <p class=\"muted\">Please check back later.</p>\n        </div>\n    {% endif %}\n</div>\n</body>\n</html>\n",
    "login.html": "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>Regal Inklings — Sign In</title>\n<link rel=\"stylesheet\" href=\"{{ url_for('static', filename='style.css') }}\">\n</head>\n<body>\n<div class=\"wrap\">\n    <header class=\"brand\">\n        <h1>REGAL INKLINGS</h1>\n        <div class=\"sub\">Member Library</div>\n    </header>\n    <div class=\"card login-box\">\n        <div class=\"emoji\">📚</div>\n        {% if error %}<p class=\"error\">{{ error }}</p>{% endif %}\n        <form method=\"post\" action=\"{{ url_for('login') }}\">\n            <label for=\"username\">Username</label>\n            <input type=\"text\" id=\"username\" name=\"username\" autocomplete=\"username\" required autofocus>\n            <label for=\"password\">Password</label>\n            <input type=\"password\" id=\"password\" name=\"password\" autocomplete=\"current-password\" required>\n            <p style=\"margin-top:16px;\"><button class=\"btn\" type=\"submit\">SIGN IN</button></p>\n        </form>\n        <p class=\"muted\" style=\"margin-top:14px;\">Regal Inklings Library</p>\n    </div>\n</div>\n</body>\n</html>\n",
    "members.html": "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>Regal Inklings — Members</title>\n<link rel=\"stylesheet\" href=\"{{ url_for('static', filename='style.css') }}\">\n<script src=\"{{ url_for('static', filename='chat.js') }}\" defer></script>\n</head>\n<body>\n{% set page = 'members' %}\n<div class=\"wrap\">\n    <header class=\"brand\">\n        <h1>REGAL INKLINGS</h1>\n        <div class=\"sub\">The reading circle.</div>\n    </header>\n    <p style=\"text-align:center;\">Signed in as <strong>{{ username }}</strong></p>\n    {% include '_nav.html' %}\n\n    <h2>MEMBERS</h2>\n    {% for m in members %}\n    <div class=\"card member\">\n        <div class=\"member-info\">\n            <span class=\"dot {{ 'online' if m.online else 'offline' }}\"></span>\n            <div>\n                <div class=\"title\">{{ m.username }}{% if m.is_me %} <span class=\"muted\">(you)</span>{% endif %}</div>\n                <div class=\"meta\">{{ 'Administrator' if m.role == 'admin' else 'Member' }} &bull; {{ 'Online' if m.online else 'Offline' }}</div>\n            </div>\n        </div>\n        {% if not m.is_me %}\n        <a class=\"btn small secondary\" href=\"{{ url_for('chat', with=m.username) }}\">MESSAGE</a>\n        {% endif %}\n    </div>\n    {% endfor %}\n</div>\n</body>\n</html>\n",
    "student.html": "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>Regal Inklings — Book Library</title>\n<link rel=\"stylesheet\" href=\"{{ url_for('static', filename='style.css') }}\">\n<script src=\"{{ url_for('static', filename='app.js') }}\" defer></script>\n<script src=\"{{ url_for('static', filename='chat.js') }}\" defer></script>\n</head>\n<body>\n{% set page = 'library' %}\n<div class=\"wrap\">\n    <header class=\"brand\">\n        <h1>REGAL INKLINGS</h1>\n        <div class=\"sub\">Your private reading room.</div>\n    </header>\n    <p style=\"text-align:center;\">Welcome, <strong>{{ username }}</strong></p>\n    {% include '_nav.html' %}\n\n    <h2>AVAILABLE BOOKS</h2>\n\n    <input type=\"text\" id=\"search\" placeholder=\"Search the library...\" class=\"search\" oninput=\"filterBooks()\">\n\n    {% if books %}\n        {% for book in books %}\n        <div class=\"card book book-card\" data-title=\"{{ book.title }}\">\n            <div class=\"book-info\">\n                <div class=\"book-icon\">&#128214;</div>\n                <div>\n                    <div class=\"title\">{{ book.title }}</div>\n                    <div class=\"meta\">PDF &bull; {{ book.size_human }}\n                        {% if book.mtime and (now - book.mtime) < 604800 %} &bull; <span class=\"gold\">Added recently</span>{% endif %}\n                    </div>\n                </div>\n            </div>\n            <a class=\"btn small download-btn\" href=\"{{ url_for('download', filename=book.filename) }}\" download>DOWNLOAD PDF</a>\n        </div>\n        {% endfor %}\n    {% else %}\n        <div class=\"card\" style=\"text-align:center;\">\n            <p><strong>NO BOOKS AVAILABLE</strong></p>\n            <p>The library is currently empty.</p>\n            <p class=\"muted\">Check back once books have been added.</p>\n        </div>\n    {% endif %}\n</div>\n</body>\n</html>\n",
    "upload_ok.html": "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>Regal Inklings — Upload</title>\n<link rel=\"stylesheet\" href=\"{{ url_for('static', filename='style.css') }}\">\n</head>\n<body>\n<div class=\"wrap\">\n    <header class=\"brand\"><h1>REGAL INKLINGS</h1></header>\n    <div class=\"card\" style=\"text-align:center;\">\n        <h2>Upload successful!</h2>\n        <p><strong>{{ filename }}</strong></p>\n        <p style=\"margin-top:16px;\"><a class=\"btn\" href=\"{{ url_for('library') if session.get('role') != 'admin' else url_for('admin') }}\">BACK TO ADMIN PANEL</a></p>\n    </div>\n</div>\n</body>\n</html>\n",
}

STATIC_ASSETS = {
    "app.js": "document.addEventListener('click', function (e) {\n    const btn = e.target.closest('a.download-btn');\n    if (!btn || btn.dataset.busy) { return; }\n    btn.dataset.busy = '1';\n    const original = btn.textContent;\n    btn.textContent = 'DOWNLOADING...';\n    setTimeout(function () { btn.textContent = 'DOWNLOADED'; }, 1200);\n    setTimeout(function () {\n        btn.textContent = original;\n        delete btn.dataset.busy;\n    }, 3000);\n});\n\nfunction filterBooks() {\n    const q = document.getElementById('search').value.toLowerCase();\n    document.querySelectorAll('.book-card').forEach(function (card) {\n        const title = card.dataset.title.toLowerCase();\n        card.style.display = title.includes(q) ? '' : 'none';\n    });\n}\n\nfunction confirmDelete(form, name) {\n    if (confirm('Are you sure you want to delete:\\n\\n' + name + '?')) {\n        form.submit();\n    }\n    return false;\n}\n",
    "chat.js": "(function () {\n    const box = document.getElementById('messages');\n    const form = document.getElementById('send-form');\n    const body = document.getElementById('body');\n    const sendBtn = document.getElementById('send-btn');\n    const errBox = document.getElementById('chat-error');\n    const navBadge = document.getElementById('nav-badge');\n    let lastCount = 0;\n    let nearBottom = true;\n\n    function roomParams() {\n        if (!box) return null;\n        const dm = box.dataset.dm;\n        return dm ? 'with=' + encodeURIComponent(dm) : 'room=group';\n    }\n\n    function fmtTime(ts) {\n        const d = new Date(ts * 1000);\n        let h = d.getHours(), m = d.getMinutes();\n        const ap = h >= 12 ? 'PM' : 'AM';\n        h = h % 12 || 12;\n        return h + ':' + (m < 10 ? '0' : '') + m + ' ' + ap;\n    }\n\n    function appendMessage(m) {\n        const wrap = document.createElement('div');\n        wrap.className = 'msg' + (m.mine ? ' mine' : '');\n        const name = document.createElement('div');\n        name.className = 'msg-name';\n        name.textContent = m.mine ? 'You' : m.sender;\n        const bubble = document.createElement('div');\n        bubble.className = 'msg-bubble';\n        bubble.textContent = m.body;\n        const meta = document.createElement('div');\n        meta.className = 'msg-time';\n        meta.textContent = fmtTime(m.ts);\n        wrap.appendChild(name);\n        wrap.appendChild(bubble);\n        wrap.appendChild(meta);\n        if (m.mine) {\n            const del = document.createElement('button');\n            del.className = 'msg-del';\n            del.textContent = '×';\n            del.title = 'Delete message';\n            del.addEventListener('click', function () { deleteMessage(m.id, wrap); });\n            wrap.appendChild(del);\n        } else if (document.body.dataset.role === 'admin') {\n            // admin handled via dashboard\n        }\n        box.appendChild(wrap);\n    }\n\n    function renderMessages(msgs) {\n        const empty = box.dataset.empty || '';\n        if (msgs.length !== lastCount) {\n            box.innerHTML = '';\n            if (msgs.length === 0) {\n                const e = document.createElement('div');\n                e.className = 'empty';\n                e.innerHTML = empty;\n                box.appendChild(e);\n            } else {\n                msgs.forEach(appendMessage);\n            }\n            lastCount = msgs.length;\n            if (nearBottom) box.scrollTop = box.scrollHeight;\n        }\n    }\n\n    async function poll() {\n        if (!box) {\n            pollUnread();\n            return;\n        }\n        try {\n            const r = await fetch('/api/messages?' + roomParams(), { credentials: 'same-origin' });\n            if (!r.ok) return;\n            const data = await r.json();\n            renderMessages(data.messages);\n            if (data.other_online !== undefined) {\n                const st = document.getElementById('dm-status');\n                if (st) st.textContent = data.other_online ? 'Online' : 'Offline';\n            }\n            pollUnread();\n        } catch (e) { /* ignore */ }\n    }\n\n    async function pollUnread() {\n        try {\n            const r = await fetch('/api/unread', { credentials: 'same-origin' });\n            if (!r.ok) return;\n            const u = await r.json();\n            const g = document.getElementById('badge-group');\n            if (g) { g.style.display = u.group > 0 ? '' : 'none'; g.textContent = u.group; }\n            document.querySelectorAll('.badge[data-dm]').forEach(function (b) {\n                const n = u.dms[b.dataset.dm] || 0;\n                b.style.display = n > 0 ? '' : 'none';\n                b.textContent = n;\n            });\n            if (navBadge) { navBadge.style.display = u.total > 0 ? '' : 'none'; navBadge.textContent = u.total; }\n            document.title = u.total > 0 ? '(' + u.total + ') Regal Inklings' : 'Regal Inklings';\n        } catch (e) { /* ignore */ }\n    }\n\n    async function deleteMessage(id, el) {\n        const csrf = document.getElementById('csrf');\n        try {\n            const r = await fetch('/api/messages/' + id + '/delete', {\n                method: 'POST',\n                headers: { 'X-CSRFToken': csrf ? csrf.value : '' },\n                credentials: 'same-origin'\n            });\n            if (r.ok) { el.style.opacity = '0'; setTimeout(function(){ el.remove(); lastCount--; }, 200); }\n            else showErr('Unable to delete message.');\n        } catch (e) { showErr('Unable to delete message.'); }\n    }\n\n    function showErr(t) {\n        if (!errBox) return;\n        errBox.textContent = t;\n        errBox.style.display = '';\n        setTimeout(function () { errBox.style.display = 'none'; }, 4000);\n    }\n\n    if (form) {\n        form.addEventListener('submit', async function (e) {\n            e.preventDefault();\n            const text = body.value.trim();\n            if (!text) return;\n            sendBtn.disabled = true;\n            const csrf = document.getElementById('csrf');\n            try {\n                const dm = box.dataset.dm;\n                const payload = dm ? { with: dm, body: text } : { room: 'group', body: text };\n                const r = await fetch('/api/messages', {\n                    method: 'POST',\n                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf ? csrf.value : '' },\n                    body: JSON.stringify(payload),\n                    credentials: 'same-origin'\n                });\n                if (r.ok) {\n                    body.value = '';\n                    lastCount = -1;\n                    await poll();\n                } else {\n                    const d = await r.json().catch(function () { return {}; });\n                    showErr(d.error || (r.status === 403 ? 'You do not have permission to perform this action.' : 'Unable to send message. Please try again.'));\n                }\n            } catch (e2) { showErr('Unable to send message. Please try again.'); }\n            sendBtn.disabled = false;\n        });\n\n        body.addEventListener('keydown', function (e) {\n            if (e.key === 'Enter' && !e.shiftKey) {\n                e.preventDefault();\n                form.dispatchEvent(new Event('submit'));\n            }\n        });\n    }\n\n    if (box) {\n        box.dataset.empty = (box.dataset.dm\n            ? 'No messages yet.<br>Start the conversation.'\n            : 'Welcome to Regal Inklings.<br>This is the group\\'s reading room.<br>Start the conversation.');\n        box.addEventListener('scroll', function () {\n            nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;\n        });\n        poll();\n        setInterval(poll, 2000);\n    } else {\n        pollUnread();\n        setInterval(pollUnread, 3000);\n    }\n})();\n",
    "style.css": ":root {\n    --cream: #faf6ee;\n    --card: #fffdf7;\n    --ink: #2f2a24;\n    --gold: #b08d3e;\n    --gold-dark: #8a6d2c;\n    --brown: #6b5433;\n    --muted: #8a8175;\n    --border: #e8dfc9;\n}\n\n* { box-sizing: border-box; margin: 0; padding: 0; }\n\nbody {\n    background: var(--cream);\n    color: var(--ink);\n    font-family: Georgia, \"Times New Roman\", serif;\n    line-height: 1.5;\n    padding: 16px;\n}\n\n.wrap { max-width: 760px; margin: 0 auto; }\n\nheader.brand {\n    text-align: center;\n    padding: 24px 0 8px;\n}\nheader.brand h1 {\n    letter-spacing: 4px;\n    font-size: 1.6rem;\n    color: var(--brown);\n}\nheader.brand .sub { color: var(--muted); font-style: italic; }\n\n.nav {\n    display: flex; flex-wrap: wrap; gap: 10px; justify-content: center;\n    margin: 12px 0 20px;\n}\n.nav a {\n    color: var(--gold-dark); text-decoration: none; font-weight: bold;\n    padding: 6px 12px; border-radius: 20px;\n}\n.nav a:hover { background: #f0e8d2; }\n\n.card {\n    background: var(--card);\n    border: 1px solid var(--border);\n    border-radius: 14px;\n    padding: 18px 20px;\n    margin-bottom: 16px;\n    box-shadow: 0 2px 6px rgba(80, 60, 20, 0.06);\n}\n\n.book { display: flex; justify-content: space-between; align-items: center; gap: 14px; flex-wrap: wrap; }\n.book .title { font-size: 1.15rem; font-weight: bold; }\n.book .meta { color: var(--muted); font-size: 0.9rem; }\n\n.btn {\n    display: inline-block;\n    background: var(--gold);\n    color: white;\n    border: none;\n    border-radius: 10px;\n    padding: 12px 22px;\n    font-size: 1rem;\n    font-weight: bold;\n    text-decoration: none;\n    cursor: pointer;\n    min-height: 48px;\n    font-family: inherit;\n}\n.btn:hover { background: var(--gold-dark); }\n.btn.secondary { background: #efe7d3; color: var(--brown); }\n.btn.secondary:hover { background: #e3d7b8; }\n.btn.danger { background: #b0503c; }\n.btn.danger:hover { background: #8f3e2e; }\n.btn.small { padding: 8px 14px; min-height: 40px; font-size: 0.9rem; }\n\ninput[type=text], input[type=password], input[type=file] {\n    width: 100%;\n    padding: 12px;\n    border: 1px solid var(--border);\n    border-radius: 10px;\n    font-size: 1rem;\n    background: white;\n    font-family: inherit;\n}\n\n.search { margin-bottom: 16px; }\n\nlabel { display: block; margin: 12px 0 4px; font-weight: bold; color: var(--brown); }\n\n.stats { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 16px; }\n.stat {\n    flex: 1; min-width: 120px; text-align: center;\n    background: var(--card); border: 1px solid var(--border);\n    border-radius: 14px; padding: 16px 8px;\n}\n.stat .n { font-size: 1.8rem; font-weight: bold; color: var(--gold-dark); }\n.stat .l { color: var(--muted); text-transform: uppercase; font-size: 0.75rem; letter-spacing: 2px; }\n\ntable { width: 100%; border-collapse: collapse; }\ntd, th { padding: 10px 8px; border-bottom: 1px solid var(--border); text-align: left; }\nth { color: var(--muted); text-transform: uppercase; font-size: 0.75rem; letter-spacing: 1px; }\n\n.flash {\n    background: #f3ead0; border: 1px solid var(--gold);\n    border-radius: 10px; padding: 10px 14px; margin-bottom: 14px;\n}\n\n.login-box { max-width: 420px; margin: 40px auto; text-align: center; }\n.login-box .emoji { font-size: 3rem; margin: 10px 0; }\n.error { color: #a33d2a; margin: 10px 0; }\n\n.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }\n.muted { color: var(--muted); }\n.inline-form { display: inline; }\n\n@media (max-width: 560px) {\n    .book { flex-direction: column; align-items: stretch; text-align: center; }\n    .btn { width: 100%; text-align: center; }\n    td .btn.small { width: auto; }\n}\n\n/* ---------- Upgrade additions ---------- */\n.wrap.wide { max-width: 1060px; }\n.nav a.active { background: #efe3c2; }\n.gold { color: var(--gold-dark); font-style: italic; }\n\nheader.brand .sub { letter-spacing: 1px; }\n\nh2 { color: var(--brown); letter-spacing: 2px; font-size: 1.05rem; margin: 26px 0 12px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }\nh3 { color: var(--brown); margin-bottom: 10px; }\n\n.book { align-items: center; }\n.book-info { display: flex; gap: 14px; align-items: center; }\n.book-icon { font-size: 2rem; }\n\n.member { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }\n.member-info { display: flex; gap: 12px; align-items: center; }\n.member .title { font-weight: bold; font-size: 1.1rem; }\n\n.dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; }\n.dot.online { background: #4a9e4a; box-shadow: 0 0 0 3px rgba(74,158,74,0.15); }\n.dot.offline { background: #c8c0ae; }\n\n.badge {\n    display: inline-block; min-width: 20px; padding: 1px 7px;\n    background: var(--gold); color: white; border-radius: 12px;\n    font-size: 0.75rem; font-weight: bold; text-align: center;\n}\n\n/* Chat */\n.chat-layout { display: grid; grid-template-columns: 240px 1fr; gap: 16px; align-items: start; }\n.chat-side { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 14px; box-shadow: 0 2px 6px rgba(80,60,20,0.06); }\n.chat-side h3 { font-size: 0.75rem; letter-spacing: 2px; color: var(--muted); margin: 12px 0 6px; }\n.side-link { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 9px 10px; border-radius: 10px; color: var(--ink); text-decoration: none; margin-bottom: 4px; }\n.side-link:hover { background: #f0e8d2; }\n.side-link.active { background: #efe3c2; font-weight: bold; }\n\n.chat-main { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 16px; display: flex; flex-direction: column; height: 70vh; min-height: 420px; box-shadow: 0 2px 6px rgba(80,60,20,0.06); }\n.chat-head { padding-bottom: 10px; border-bottom: 1px solid var(--border); margin-bottom: 10px; }\n.chat-head .back { text-decoration: none; color: var(--gold-dark); font-weight: bold; margin-right: 8px; }\n\n.messages { flex: 1; overflow-y: auto; padding: 6px 2px; display: flex; flex-direction: column; gap: 10px; }\n.msg { max-width: 75%; align-self: flex-start; animation: fadein .25s ease; }\n.msg.mine { align-self: flex-end; text-align: right; }\n.msg-name { font-size: 0.8rem; color: var(--muted); margin-bottom: 2px; }\n.msg-bubble { background: #f1e9d4; color: var(--ink); border-radius: 14px 14px 14px 4px; padding: 10px 14px; white-space: pre-wrap; word-break: break-word; }\n.msg.mine .msg-bubble { background: var(--gold); color: #fffdf7; border-radius: 14px 14px 4px 14px; }\n.msg-time { font-size: 0.72rem; color: var(--muted); margin-top: 2px; }\n.msg-del { border: none; background: none; color: var(--muted); cursor: pointer; font-size: 0.9rem; padding: 0 4px; }\n.msg-del:hover { color: #b0503c; }\n\n.empty { text-align: center; color: var(--muted); padding: 40px 10px; font-style: italic; }\n\n.sendbar { display: flex; gap: 8px; margin-top: 10px; }\n.sendbar textarea { flex: 1; resize: none; padding: 12px; border: 1px solid var(--border); border-radius: 12px; font-family: inherit; font-size: 1rem; background: white; min-height: 48px; max-height: 120px; }\n.sendbar .btn { min-height: 48px; }\n\n/* Bottom nav (mobile) */\n.bottomnav { display: none; }\n\n@keyframes fadein { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }\n\n.btn, .nav a, .side-link, .card, .badge { transition: background-color .2s ease, color .2s ease, opacity .2s ease, transform .15s ease; }\n.btn:hover, .side-link:hover, .nav a:hover { transform: translateY(-1px); }\n\n@media (prefers-reduced-motion: reduce) {\n    * { animation: none !important; transition: none !important; }\n}\n\n@media (max-width: 720px) {\n    .chat-layout { grid-template-columns: 1fr; }\n    .chat-side { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px; }\n    .chat-side h3 { width: 100%; margin: 4px 0 0; }\n    .side-link { padding: 6px 10px; border: 1px solid var(--border); border-radius: 20px; margin: 0; font-size: 0.9rem; }\n    .chat-main { height: calc(100vh - 320px); min-height: 360px; }\n    .msg { max-width: 85%; }\n    .nav { display: none; }\n    .bottomnav {\n        display: flex; justify-content: space-around; position: fixed; bottom: 0; left: 0; right: 0;\n        background: var(--card); border-top: 1px solid var(--border); padding: 8px 0; z-index: 50;\n    }\n    .bottomnav a { text-decoration: none; color: var(--brown); font-size: 1.2rem; text-align: center; }\n    .bottomnav a span { display: block; font-size: 0.65rem; letter-spacing: 1px; }\n    .bottomnav a.active { color: var(--gold-dark); font-weight: bold; }\n    body { padding-bottom: 64px; }\n}\n",
}

def create_app():
    config = load_config()
    os.makedirs(BOOKS_DIR, exist_ok=True)
    first_run = not os.path.exists(USERS_FILE)
    if first_run:
        create_default_users()
    init_db()

    app = Flask(__name__, static_folder=None)
    app.secret_key = get_secret_key()
    app.jinja_loader = DictLoader(TEMPLATES)

    @app.route("/static/<path:filename>", endpoint="static")
    def static_files(filename):
        asset = STATIC_ASSETS.get(filename)
        if asset is None:
            abort(404)
        mimetype = "text/css" if filename.endswith(".css") else "application/javascript"
        return Response(asset, mimetype=mimetype)

    app.config["MAX_CONTENT_LENGTH"] = config["max_upload_mb"] * 1024 * 1024
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = False

    app.jinja_env.globals["csrf_token"] = csrf_token

    # ---------- Auth ----------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("username"):
            return redirect(url_for("library"))
        error = None
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = find_user(username)
            if user and check_password_hash(user["password_hash"], password):
                session.clear()
                session["username"] = user["username"]
                session["role"] = user["role"]
                session.permanent = False
                touch_active(user["username"])
                log_event(f"{user['username']} logged in")
                if user["role"] == "admin":
                    return redirect(url_for("admin"))
                return redirect(url_for("library"))
            error = "Invalid username or password."
            log_event(f"Failed login attempt for '{username}'")
        return render_template("login.html", error=error)

    @app.route("/logout", methods=["GET", "POST"])
    def logout():
        username = session.get("username")
        session.clear()
        if username:
            log_event(f"{username} logged out")
        return redirect(url_for("login"))

    # ---------- Student library ----------
    @app.route("/")
    @login_required()
    def library():
        return render_template("student.html", username=session["username"],
                               role=session.get("role"), now=time.time(),
                               books=list_books())

    @app.route("/download/<path:filename>")
    @login_required()
    def download(filename):
        path = safe_book_path(filename)
        if path is None:
            log_event(f"Blocked missing/unsafe download: {filename!r} by {session.get('username')}")
            abort(404)
        log_event(f"{session['username']} downloaded {os.path.basename(path)}")
        return send_file(path, as_attachment=True,
                         download_name=os.path.basename(path))

    # ---------- Members ----------
    @app.route("/members")
    @login_required()
    def members():
        users = load_users()
        me = session["username"]
        member_list = []
        for u in users:
            member_list.append({
                "username": u["username"],
                "role": u["role"],
                "online": is_online(u["username"]),
                "is_me": u["username"] == me,
            })
        admins_first = sorted(member_list, key=lambda m: (m["role"] != "admin", m["username"].lower()))
        return render_template("members.html", members=admins_first,
                               username=session["username"],
                               role=session.get("role"))

    # ---------- Chat ----------
    @app.route("/chat")
    @login_required()
    def chat():
        users = load_users()
        others = [u["username"] for u in users if u["username"] != session["username"]]
        with_user = request.args.get("with", "").strip()
        if with_user:
            if with_user == session["username"] or not find_user(with_user):
                return redirect(url_for("chat"))
            return render_template("chat.html", username=session["username"],
                                   role=session.get("role"), dm=with_user,
                                   dm_online=is_online(with_user), others=others)
        return render_template("chat.html", username=session["username"],
                               role=session.get("role"), dm=None, others=others)

    @app.route("/messages")
    @login_required()
    def messages_page():
        return redirect(url_for("chat"))

    @app.route("/api/messages", methods=["GET"])
    @login_required()
    def api_messages():
        room = room_for_request(request.args, session["username"])
        if room is None:
            abort(403)
        conn = db()
        try:
            rows = conn.execute(
                "SELECT id, sender, body, ts FROM messages WHERE room=? ORDER BY id DESC LIMIT 200",
                (room,)).fetchall()
            msgs = [{"id": r["id"], "sender": r["sender"], "body": r["body"],
                     "ts": r["ts"], "mine": r["sender"] == session["username"]}
                    for r in reversed(rows)]
        finally:
            conn.close()
        mark_read(session["username"], room)
        resp = {"messages": msgs, "me": session["username"]}
        if request.args.get("with"):
            resp["other_online"] = is_online(request.args.get("with"))
        return jsonify(resp)

    @app.route("/api/messages", methods=["POST"])
    @login_required()
    def api_send():
        if not csrf_header_ok():
            abort(403)
        data = request.get_json(silent=True) or {}
        room = room_for_request(data, session["username"])
        if room is None:
            abort(403)
        body = (data.get("body") or "").strip()
        if not body:
            return jsonify({"error": "Message cannot be empty."}), 400
        if len(body) > MAX_MESSAGE_LENGTH:
            return jsonify({"error": "Message is too long (max 2000 characters)."}), 400
        conn = db()
        try:
            cur = conn.execute(
                "INSERT INTO messages(sender, body, ts, room) VALUES(?,?,?,?)",
                (session["username"], body, time.time(), room))
            mid = cur.lastrowid
            conn.commit()
        finally:
            conn.close()
        return jsonify({"id": mid, "sender": session["username"],
                        "body": body, "ts": time.time(), "mine": True})

    @app.route("/api/messages/<int:mid>/delete", methods=["POST"])
    @login_required()
    def api_delete_message(mid):
        if not csrf_header_ok():
            abort(403)
        conn = db()
        try:
            row = conn.execute("SELECT sender FROM messages WHERE id=?", (mid,)).fetchone()
            if row is None:
                abort(404)
            if row["sender"] != session["username"] and session.get("role") != "admin":
                abort(403)
            conn.execute("DELETE FROM messages WHERE id=?", (mid,))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.route("/api/unread")
    @login_required()
    def api_unread():
        return jsonify(unread_counts(session["username"]))

    @app.route("/admin/delete-message", methods=["POST"])
    @login_required(role="admin")
    def admin_delete_message():
        if not csrf_ok():
            abort(403)
        try:
            mid = int(request.form.get("id", "0"))
        except ValueError:
            abort(400)
        conn = db()
        try:
            conn.execute("DELETE FROM messages WHERE id=?", (mid,))
            conn.commit()
        finally:
            conn.close()
        log_event(f"admin deleted message {mid}")
        flash("Message deleted.")
        return redirect(url_for("admin"))

    # ---------- Admin ----------
    @app.route("/admin")
    @login_required(role="admin")
    def admin():
        users = load_users()
        students = [u for u in users if u["role"] == "student"]
        books = list_books()
        total = sum(b["size"] for b in books)
        conn = db()
        try:
            recent = [dict(r) for r in conn.execute(
                "SELECT id, sender, body, ts FROM messages WHERE room='group' ORDER BY id DESC LIMIT 20"
            ).fetchall()]
        finally:
            conn.close()
        return render_template("admin.html", users=users, students=students,
                               books=books, total_size=human_size(total),
                               active=active_now(), username=session["username"],
                               online_count=sum(1 for u in users if is_online(u["username"])),
                               member_count=len(users), recent_messages=recent,
                               display_name=(find_user(session["username"]) or {}).get("display_name", session["username"]))

    @app.route("/admin/upload", methods=["POST"])
    @login_required(role="admin")
    def upload():
        if not csrf_ok():
            abort(403)
        file = request.files.get("book")
        if not file or not file.filename:
            flash("No file selected.")
            return redirect(url_for("admin"))
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            flash("Only PDF (.pdf) files are allowed.")
            return redirect(url_for("admin"))
        filename = os.path.basename(file.filename.replace("\\", "/").split("/")[-1]).strip()
        if not filename or filename in (".", ".."):
            flash("Invalid filename.")
            return redirect(url_for("admin"))
        # strip characters that are unsafe on Windows
        filename = re.sub(r'[<>:"|?*\x00-\x1f]', "_", filename)
        dest = os.path.join(BOOKS_DIR, filename)
        if os.path.exists(dest):
            base, e = os.path.splitext(filename)
            n = 2
            while os.path.exists(dest):
                dest = os.path.join(BOOKS_DIR, f"{base} ({n}){e}")
                n += 1
            filename = os.path.basename(dest)
        file.save(dest)
        log_event(f"admin uploaded {filename}")
        return render_template("upload_ok.html", filename=filename)

    @app.route("/admin/delete-book", methods=["POST"])
    @login_required(role="admin")
    def delete_book():
        if not csrf_ok():
            abort(403)
        filename = request.form.get("filename", "")
        path = safe_book_path(filename)
        if path is None:
            abort(403)
        os.remove(path)
        log_event(f"admin deleted {filename}")
        flash(f"Deleted {filename}.")
        return redirect(url_for("admin"))

    @app.route("/admin/create-user", methods=["POST"])
    @login_required(role="admin")
    def create_user():
        if not csrf_ok():
            abort(403)
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,32}", username or ""):
            flash("Username must be 3-32 letters, numbers, _ or -.")
            return redirect(url_for("admin"))
        if len(password) < 4:
            flash("Password must be at least 4 characters.")
            return redirect(url_for("admin"))
        users = load_users()
        if any(u["username"] == username for u in users):
            flash("That username already exists.")
            return redirect(url_for("admin"))
        users.append({"username": username,
                      "password_hash": generate_password_hash(password),
                      "role": "student"})
        save_users(users)
        log_event(f"admin created user {username}")
        flash(f"Created user {username}.")
        return redirect(url_for("admin"))

    @app.route("/admin/reset-password", methods=["POST"])
    @login_required(role="admin")
    def reset_password():
        if not csrf_ok():
            abort(403)
        username = request.form.get("username", "")
        new_password = request.form.get("password", "")
        if len(new_password) < 4:
            flash("Password must be at least 4 characters.")
            return redirect(url_for("admin"))
        users = load_users()
        for u in users:
            if u["username"] == username and u["role"] == "student":
                u["password_hash"] = generate_password_hash(new_password)
                save_users(users)
                log_event(f"admin reset password for {username}")
                flash(f"Password reset for {username}.")
                return redirect(url_for("admin"))
        flash("Student not found.")
        return redirect(url_for("admin"))

    @app.route("/admin/delete-user", methods=["POST"])
    @login_required(role="admin")
    def delete_user():
        if not csrf_ok():
            abort(403)
        username = request.form.get("username", "")
        users = load_users()
        remaining = [u for u in users
                     if not (u["username"] == username and u["role"] == "student")]
        if len(remaining) == len(users):
            flash("Student not found.")
        else:
            save_users(remaining)
            active_users.pop(username, None)
            log_event(f"admin deleted user {username}")
            flash(f"Deleted user {username}.")
        return redirect(url_for("admin"))

    @app.route("/admin/change-password", methods=["POST"])
    @login_required(role="admin")
    def change_password():
        if not csrf_ok():
            abort(403)
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        user = find_user(session["username"])
        if not user or not check_password_hash(user["password_hash"], current):
            flash("Current password is incorrect.")
            return redirect(url_for("admin"))
        if len(new) < 4:
            flash("New password must be at least 4 characters.")
            return redirect(url_for("admin"))
        users = load_users()
        for u in users:
            if u["username"] == session["username"]:
                u["password_hash"] = generate_password_hash(new)
        save_users(users)
        log_event("admin changed own password")
        flash("Password updated.")
        return redirect(url_for("admin"))

    # ---------- Errors ----------
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403,
                               message="Access denied."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404,
                               message="That page could not be found."), 404

    @app.errorhandler(413)
    def too_large(e):
        return render_template("error.html", code=413,
                               message="That file is too large."), 413

    @app.errorhandler(500)
    def server_error(e):
        return render_template("error.html", code=500,
                               message="Something went wrong."), 500

    if first_run:
        print()
        print("FIRST RUN: Created users.json with the admin account 'k4ge'.")
        print("Log in with username 'k4ge' and the configured admin password.")
        print("You can change it after logging in via the admin panel.")
        print()

    @app.route("/health")
    def health():
        return jsonify({"ok": True, "server": config.get("server_name", "Regal Inklings")})

    return app, config


app, config = create_app()

if __name__ == "__main__":
    lan = get_lan_ip()
    port = int(config["port"])
    print("=" * 44)
    print("       REGAL INKLINGS SERVER")
    print("=" * 44)
    print()
    print(f"Local:   http://127.0.0.1:{port}")
    print(f"Network: http://{lan}:{port}")
    print()
    print(f"Students: http://{lan}:{port}")
    print(f"Admin:    http://{lan}:{port}/admin")
    print()
    print("If other devices cannot connect, Windows Firewall")
    print(f"may be blocking port {port}.")
    print()
    print("Admin username: k4ge")
    print("Admin password: " + ("<set by REGAL_DEFAULT_ADMIN_PASSWORD>" if os.environ.get("REGAL_DEFAULT_ADMIN_PASSWORD") else "Regal123!"))
    print()
    print("Press CTRL+C to stop the server.")
    print("=" * 44)
    log_event("Server started")
    app.run(host=config["host"], port=port, threaded=True, debug=False)