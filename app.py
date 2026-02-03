from flask import Flask, request, jsonify, render_template, redirect, session, make_response, abort
import sqlite3
import uuid
import time
import requests
import random
import os
import base64
from math import radians, cos, sin, asin, sqrt
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)

# =========================================================
# ✅ SECRET KEY (safe for deploy)
# =========================================================
app.secret_key = os.environ.get("SECRET_KEY", "moodmaps_super_secret_key_123")

# =========================================================
# ✅ OVERPASS URL (fallback list)
# =========================================================
OVERPASS_URLS = [
    os.environ.get("OVERPASS_URL", "").strip() or "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
]

# =========================================================
# ✅ UPLOAD CONFIG
# =========================================================
UPLOAD_FOLDER = os.path.join("static", "uploads", "pfp")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}
MAX_PFP_SIZE_MB = 4

# =========================================================
# ✅ BREVO CONFIG (Render-safe)
# =========================================================
BREVO_API_KEY = (os.environ.get("BREVO_API_KEY") or "").strip()
BREVO_SENDER = (os.environ.get("BREVO_SENDER") or "").strip()

# =========================================================
# ✅ DB HELPERS (Better handling)
# =========================================================
DB_PATH = os.environ.get("DB_PATH", "users.db")


def get_db():
    """
    Better SQLite connection:
    - busy_timeout prevents 'database is locked'
    - WAL mode improves concurrent access
    """
    db = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA journal_mode=WAL;")
        db.execute("PRAGMA synchronous=NORMAL;")
        db.execute("PRAGMA busy_timeout=30000;")
        db.execute("PRAGMA foreign_keys=ON;")
    except:
        pass
    return db


def ensure_column(db, table, coldef_sql):
    table_info = db.execute(f"PRAGMA table_info({table})").fetchall()
    cols = [r["name"] for r in table_info]
    colname = coldef_sql.split()[0]
    if colname not in cols:
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {coldef_sql}")
        except Exception as e:
            print("⚠️ ensure_column error:", e)


def safe_username(s: str):
    s = (s or "").strip().lower()
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789_"
    if not s:
        return ""
    if " " in s:
        return ""
    if len(s) < 3:
        return ""
    if any(ch not in allowed for ch in s):
        return ""
    return s


def allowed_file(filename):
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXT


# =========================================================
# ✅ DB INIT
# =========================================================
with get_db() as db:
    db.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            remember_token TEXT
        )
    """)

    ensure_column(db, "users", "username TEXT")
    ensure_column(db, "users", "is_private INTEGER DEFAULT 0")
    ensure_column(db, "users", "current_mood TEXT")
    ensure_column(db, "users", "profile_pic TEXT")

    db.execute("""
        CREATE TABLE IF NOT EXISTS favorites(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            place_id TEXT NOT NULL,
            name TEXT,
            category TEXT,
            lat REAL,
            lon REAL,
            created_at INTEGER,
            UNIQUE(user_id, place_id)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS password_resets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            expires_at INTEGER NOT NULL
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS follows(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            follower_id INTEGER NOT NULL,
            following_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at INTEGER,
            UNIQUE(follower_id, following_id)
        )
    """)

    try:
        db.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
    except:
        pass

    try:
        db.execute("CREATE INDEX IF NOT EXISTS idx_users_name ON users(name)")
    except:
        pass

    # =========================================================
    # ✅ MAINTENANCE MODE (DB META STORAGE)
    # =========================================================
    db.execute("""
        CREATE TABLE IF NOT EXISTS app_meta(
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    db.execute("INSERT OR IGNORE INTO app_meta(key, value) VALUES(?,?)", ("maintenance_mode", "0"))


# =========================================================
# ✅ MAINTENANCE HELPERS
# =========================================================
def get_maintenance_mode():
    try:
        with get_db() as db:
            row = db.execute("SELECT value FROM app_meta WHERE key=?", ("maintenance_mode",)).fetchone()
            if not row:
                return False
            return str(row["value"]) == "1"
    except:
        return False


def set_maintenance_mode(is_on: bool):
    try:
        with get_db() as db:
            db.execute("UPDATE app_meta SET value=? WHERE key=?",
                       ("1" if is_on else "0", "maintenance_mode"))
    except Exception as e:
        print("⚠️ set_maintenance_mode error:", e)


@app.context_processor
def inject_globals():
    return {
        "maintenance_mode": get_maintenance_mode()
    }


# =========================================================
# ✅ MAINTENANCE GATE
# =========================================================
@app.before_request
def maintenance_gate():
    try:
        if request.path.startswith("/static/"):
            return None

        if get_maintenance_mode():
            allowed_paths = {
                "/login",
                "/logout",
                "/forgot",
                "/reset",
                "/api/forgot",
                "/api/reset",
                "/api/admin/maintenance",
            }

            if request.path in allowed_paths:
                return None

            return render_template("maintenance.html"), 503
    except Exception as e:
        print("⚠️ maintenance_gate error:", e)
        return None


# =========================================================
# ✅ EMAIL TEMPLATE
# =========================================================
def build_reset_email_html(code: str):
    return f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>MoodMap Reset Code</title>
</head>
<body style="margin:0;background:#05070f;font-family:Inter,Arial,sans-serif;color:#e5e7eb;">
  <div style="max-width:640px;margin:0 auto;padding:28px 16px;">
    <div style="
      border-radius:22px;
      background: radial-gradient(circle at 15% 20%, rgba(167,139,250,0.35), transparent 55%),
                  radial-gradient(circle at 85% 70%, rgba(34,211,238,0.32), transparent 55%),
                  linear-gradient(135deg, rgba(255,255,255,0.10), rgba(255,255,255,0.04));
      border:1px solid rgba(255,255,255,0.16);
      padding:26px 22px;
      box-shadow:0 40px 120px rgba(0,0,0,0.65);
    ">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;">
        <div style="
          width:44px;height:44px;border-radius:14px;
          display:flex;align-items:center;justify-content:center;
          font-weight:900;color:white;
          background:linear-gradient(135deg,#22d3ee,#a78bfa);
        ">M</div>
        <div>
          <div style="font-size:18px;font-weight:900;letter-spacing:-0.2px;">MoodMap</div>
          <div style="opacity:.72;font-size:13px;margin-top:3px;">Password reset request</div>
        </div>
      </div>

      <div style="font-size:14px;line-height:1.65;opacity:.92;">
        Hey 👋 <br><br>
        Use this 6-digit code to reset your password:
      </div>

      <div style="
        margin:16px 0 10px;
        border-radius:18px;
        border:1px solid rgba(255,255,255,0.16);
        background:rgba(0,0,0,0.25);
        padding:14px 14px;
        text-align:center;
      ">
        <div style="font-size:30px;font-weight:1000;letter-spacing:6px;color:white;">
          {code}
        </div>
        <div style="font-size:12px;opacity:.68;margin-top:6px;">
          Valid for 10 minutes
        </div>
      </div>

      <div style="font-size:13px;opacity:.72;line-height:1.55;margin-top:12px;">
        If you didn’t request a password reset, you can safely ignore this email.
      </div>

      <div style="margin-top:18px;border-top:1px solid rgba(255,255,255,0.10);padding-top:14px;
                  font-size:12px;opacity:.55;">
        © {time.strftime("%Y")} MoodMap — Smart mood based recommendations
      </div>
    </div>
  </div>
</body>
</html>
"""


# =========================================================
# ✅ BREVO EMAIL SENDING (THE REAL FIX)
# =========================================================
def send_reset_email(to_email: str, code: str) -> bool:
    if not BREVO_API_KEY or not BREVO_SENDER:
        print("❌ BREVO env vars missing")
        print("   BREVO_API_KEY:", "SET" if BREVO_API_KEY else "EMPTY")
        print("   BREVO_SENDER:", BREVO_SENDER or "EMPTY")
        return False

    try:
        url = "https://api.brevo.com/v3/smtp/email"
        html = build_reset_email_html(code)

        payload = {
            "sender": {
                "name": "MoodMap",
                "email": BREVO_SENDER
            },
            "to": [{"email": to_email}],
            "subject": "MoodMap Password Reset Code",
            "htmlContent": html,
            "textContent": f"Your MoodMap reset code is {code}. Valid for 10 minutes."
        }

        headers = {
            "accept": "application/json",
            "api-key": BREVO_API_KEY,
            "content-type": "application/json"
        }

        print("📨 Sending reset email via Brevo API ->", to_email)
        r = requests.post(url, json=payload, headers=headers, timeout=15)

        if r.status_code in (200, 201, 202):
            print("✅ Brevo email sent ✅")
            return True

        print("❌ Brevo failed:", r.status_code, r.text)
        return False

    except Exception as e:
        print("❌ Brevo exception:", repr(e))
        return False


# =========================================================
# ✅ AUTH HELPERS
# =========================================================
def current_user():
    if "user_id" in session:
        return session["user_id"]

    token = request.cookies.get("remember_token")
    if token:
        with get_db() as db:
            row = db.execute("SELECT id FROM users WHERE remember_token=?", (token,)).fetchone()
            if row:
                session["user_id"] = row["id"]
                return row["id"]
    return None


def user_row(uid):
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def user_by_username(username):
    username = (username or "").strip().lower()
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


def follow_status(viewer_id, target_id):
    if not viewer_id:
        return "none"
    with get_db() as db:
        row = db.execute("""
            SELECT status FROM follows
            WHERE follower_id=? AND following_id=?
        """, (viewer_id, target_id)).fetchone()
    if not row:
        return "none"
    return row["status"]


def can_view_private(viewer_id, target_id):
    if viewer_id and viewer_id == target_id:
        return True
    with get_db() as db:
        row = db.execute("""
            SELECT status FROM follows
            WHERE follower_id=? AND following_id=? AND status='accepted'
        """, (viewer_id, target_id)).fetchone()
    return True if row else False


def delete_profile_pic_file(profile_pic_url: str):
    try:
        if not profile_pic_url:
            return
        url = profile_pic_url.strip()
        if not url.startswith("/"):
            return

        rel_path = url.lstrip("/")
        abs_path = os.path.abspath(rel_path)

        allowed_root = os.path.abspath(UPLOAD_FOLDER)
        if not abs_path.startswith(allowed_root):
            return

        if os.path.exists(abs_path):
            os.remove(abs_path)
    except Exception as e:
        print("⚠️ delete_profile_pic_file error:", e)


# =========================================================
# ✅ ADMIN CHECK
# =========================================================
def is_admin(uid):
    try:
        return uid and int(uid) == 1
    except:
        return False


# =========================================================
# ✅ ROUTES
# =========================================================
@app.route("/")
def home():
    uid = current_user()
    if not uid:
        return redirect("/login")

    user = user_row(uid)

    if not user:
        session.clear()
        resp = redirect("/login")
        resp.delete_cookie("remember_token")
        return resp

    return render_template(
        "index.html",
        username=user["name"],
        user_username=user["username"] or ""
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect("/")

    if request.method == "POST":
        data = request.json
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        remember = data.get("remember", False)

        with get_db() as db:
            user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()

        if not user or not check_password_hash(user["password"], password):
            return jsonify({"success": False, "message": "Invalid email or password"})

        session["user_id"] = user["id"]
        resp = make_response(jsonify({"success": True, "name": user["name"], "username": user["username"]}))

        if remember:
            token = str(uuid.uuid4())
            with get_db() as db:
                db.execute("UPDATE users SET remember_token=? WHERE id=?", (token, user["id"]))
            resp.set_cookie("remember_token", token, max_age=60 * 60 * 24 * 30, httponly=True)

        return resp

    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user():
        return redirect("/")

    if request.method == "POST":
        data = request.json
        name = data.get("name", "").strip()
        username = safe_username(data.get("username", ""))
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        is_private = 1 if data.get("is_private", False) else 0

        if not name or not username or not email or not password:
            return jsonify({"success": False, "message": "All fields are required"})

        hashed = generate_password_hash(password)

        try:
            with get_db() as db:
                existing = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
                if existing:
                    return jsonify({"success": False, "message": "Username already taken"})

                cur = db.execute("""
                    INSERT INTO users(name, username, email, password, is_private, current_mood)
                    VALUES(?,?,?,?,?,?)
                """, (name, username, email, hashed, is_private, "work"))

                new_user_id = cur.lastrowid

            session["user_id"] = new_user_id
            return jsonify({"success": True, "username": username})

        except Exception as e:
            print("Signup error:", e)
            return jsonify({"success": False, "message": "Email already exists"})

    return render_template("signup.html")


@app.route("/logout")
def logout():
    session.clear()
    resp = redirect("/login")
    resp.delete_cookie("remember_token")
    return resp


# =========================================================
# ✅ MAINTENANCE ADMIN ENDPOINT
# =========================================================
@app.route("/api/admin/maintenance", methods=["GET", "POST"])
def api_admin_maintenance():
    uid = current_user()
    if not uid or not is_admin(uid):
        return jsonify({"success": False, "message": "Forbidden"}), 403

    if request.method == "GET":
        return jsonify({"success": True, "maintenance": get_maintenance_mode()})

    data = request.json or {}
    new_state = True if data.get("maintenance", False) else False
    set_maintenance_mode(new_state)

    return jsonify({"success": True, "maintenance": get_maintenance_mode()})


# =========================================================
# ✅ PROFILE PFP UPLOAD
# =========================================================
@app.route("/api/profile/upload_pfp", methods=["POST"])
def api_upload_pfp():
    uid = current_user()
    if not uid:
        return jsonify({"success": False, "message": "Login required"})

    u = user_row(uid)
    if not u:
        session.clear()
        return jsonify({"success": False, "message": "Session expired"})

    if "pfp" not in request.files:
        return jsonify({"success": False, "message": "No file uploaded"})

    f = request.files["pfp"]
    if f.filename == "":
        return jsonify({"success": False, "message": "No file selected"})

    if not allowed_file(f.filename):
        return jsonify({"success": False, "message": "Only PNG/JPG/WEBP allowed"})

    f.seek(0, os.SEEK_END)
    size_bytes = f.tell()
    f.seek(0)
    if size_bytes > MAX_PFP_SIZE_MB * 1024 * 1024:
        return jsonify({"success": False, "message": f"Max {MAX_PFP_SIZE_MB}MB allowed"})

    old_pic = (u["profile_pic"] or "").strip()
    if old_pic:
        delete_profile_pic_file(old_pic)

    ext = f.filename.rsplit(".", 1)[1].lower()
    filename = secure_filename(f"user_{uid}_{int(time.time())}.{ext}")
    save_path = os.path.join(UPLOAD_FOLDER, filename)
    f.save(save_path)

    url = "/" + save_path.replace("\\", "/")

    with get_db() as db:
        db.execute("UPDATE users SET profile_pic=? WHERE id=?", (url, uid))

    return jsonify({"success": True, "url": url})


@app.route("/api/profile/upload_pfp_base64", methods=["POST"])
def api_upload_pfp_base64():
    uid = current_user()
    if not uid:
        return jsonify({"success": False, "message": "Login required"})

    u = user_row(uid)
    if not u:
        session.clear()
        return jsonify({"success": False, "message": "Session expired"})

    data_url = (request.json.get("data_url") or "").strip()
    if not data_url.startswith("data:image/"):
        return jsonify({"success": False, "message": "Invalid image data"})

    header, b64 = data_url.split(",", 1)

    mime = header.split(";")[0].replace("data:", "").strip().lower()
    mime_to_ext = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp"
    }
    if mime not in mime_to_ext:
        return jsonify({"success": False, "message": "Only PNG/JPG/WEBP allowed"})

    raw = base64.b64decode(b64)
    if len(raw) > MAX_PFP_SIZE_MB * 1024 * 1024:
        return jsonify({"success": False, "message": f"Max {MAX_PFP_SIZE_MB}MB allowed"})

    old_pic = (u["profile_pic"] or "").strip()
    if old_pic:
        delete_profile_pic_file(old_pic)

    ext = mime_to_ext[mime]
    filename = secure_filename(f"user_{uid}_{int(time.time())}.{ext}")
    save_path = os.path.join(UPLOAD_FOLDER, filename)

    with open(save_path, "wb") as fp:
        fp.write(raw)

    url = "/" + save_path.replace("\\", "/")

    with get_db() as db:
        db.execute("UPDATE users SET profile_pic=? WHERE id=?", (url, uid))

    return jsonify({"success": True, "url": url})


@app.route("/api/profile/me", methods=["GET"])
def api_profile_me():
    uid = current_user()
    if not uid:
        return jsonify({"success": False})

    u = user_row(uid)
    if not u:
        session.clear()
        return jsonify({"success": False})

    return jsonify({
        "success": True,
        "name": u["name"],
        "username": u["username"],
        "is_private": int(u["is_private"] or 0),
        "current_mood": u["current_mood"] or "work",
        "profile_pic": u["profile_pic"] or ""
    })


@app.route("/api/profile/update_username", methods=["POST"])
def api_update_username():
    uid = current_user()
    if not uid:
        return jsonify({"success": False, "message": "Login required"})

    new_username = safe_username(request.json.get("username", ""))
    if not new_username:
        return jsonify({"success": False, "message": "Invalid username"})

    with get_db() as db:
        existing = db.execute("SELECT id FROM users WHERE username=?", (new_username,)).fetchone()
        if existing and int(existing["id"]) != int(uid):
            return jsonify({"success": False, "message": "Username already taken"})

        db.execute("UPDATE users SET username=? WHERE id=?", (new_username, uid))

    return jsonify({"success": True, "username": new_username})


@app.route("/api/profile/toggle_privacy", methods=["POST"])
def api_toggle_privacy():
    uid = current_user()
    if not uid:
        return jsonify({"success": False, "message": "Login required"})

    new_state = 1 if request.json.get("is_private", False) else 0
    with get_db() as db:
        db.execute("UPDATE users SET is_private=? WHERE id=?", (new_state, uid))
    return jsonify({"success": True, "is_private": new_state})


# =========================================================
# ✅ DELETE ACCOUNT
# =========================================================
@app.route("/api/account/delete", methods=["POST"])
def api_account_delete():
    uid = current_user()
    if not uid:
        return jsonify({"success": False, "message": "Login required"})

    password = (request.json.get("password") or "").strip()
    if not password:
        return jsonify({"success": False, "message": "Password required"})

    u = user_row(uid)
    if not u:
        session.clear()
        return jsonify({"success": False, "message": "Session expired"})

    if not check_password_hash(u["password"], password):
        return jsonify({"success": False, "message": "Incorrect password"})

    profile_pic = (u["profile_pic"] or "").strip()
    if profile_pic:
        delete_profile_pic_file(profile_pic)

    with get_db() as db:
        db.execute("DELETE FROM favorites WHERE user_id=?", (uid,))
        db.execute("DELETE FROM follows WHERE follower_id=? OR following_id=?", (uid, uid))
        db.execute("DELETE FROM password_resets WHERE user_id=?", (uid,))
        db.execute("DELETE FROM users WHERE id=?", (uid,))

    session.clear()
    resp = make_response(jsonify({"success": True}))
    resp.delete_cookie("remember_token")
    return resp


# =========================================================
# ✅ MOOD
# =========================================================
@app.route("/api/mood/set", methods=["POST"])
def api_set_mood():
    uid = current_user()
    if not uid:
        return jsonify({"success": False})

    mood = (request.json.get("mood") or "").strip()
    if mood not in ["work", "date", "quick_bite", "budget"]:
        return jsonify({"success": False})

    with get_db() as db:
        db.execute("UPDATE users SET current_mood=? WHERE id=?", (mood, uid))

    return jsonify({"success": True})


# =========================================================
# ✅ PROFILE SEARCH
# =========================================================
@app.route("/api/users/search", methods=["GET"])
def api_users_search():
    uid = current_user()
    if not uid:
        return jsonify([])

    q = (request.args.get("q") or "").strip().lower()
    if len(q) < 2:
        return jsonify([])

    with get_db() as db:
        rows = db.execute("""
            SELECT username, name, is_private, profile_pic
            FROM users
            WHERE lower(username) LIKE ? OR lower(name) LIKE ?
            ORDER BY
              CASE WHEN lower(username)=? THEN 0 ELSE 1 END,
              CASE WHEN lower(username) LIKE ? THEN 0 ELSE 1 END,
              name ASC
            LIMIT 10
        """, (f"%{q}%", f"%{q}%", q, f"{q}%")).fetchall()

    out = []
    for r in rows:
        out.append({
            "username": r["username"],
            "name": r["name"],
            "is_private": int(r["is_private"] or 0),
            "profile_pic": r["profile_pic"] or ""
        })
    return jsonify(out)


# =========================================================
# ✅ PROFILE PAGE
# =========================================================
@app.route("/u/<username>")
def public_profile(username):
    user = user_by_username(username)
    if not user:
        abort(404)

    viewer_id = current_user()
    rel = follow_status(viewer_id, user["id"])

    is_private = int(user["is_private"] or 0)
    allowed_to_view = True
    if is_private:
        allowed_to_view = can_view_private(viewer_id, user["id"])

    places = []
    if allowed_to_view:
        with get_db() as db:
            rows = db.execute("""
                SELECT place_id, name, category, lat, lon, created_at
                FROM favorites
                WHERE user_id=?
                ORDER BY created_at DESC
            """, (user["id"],)).fetchall()
        places = [dict(r) for r in rows]

    return render_template(
        "profile.html",
        profile_name=user["name"],
        profile_username=user["username"],
        current_mood=user["current_mood"] or "work",
        is_private=is_private,
        allowed=allowed_to_view,
        relationship=rel,
        viewer_logged_in=True if viewer_id else False,
        is_owner=True if viewer_id and viewer_id == user["id"] else False,
        profile_pic=user["profile_pic"] or "",
        places=places,
        admin=True if is_admin(viewer_id) else False
    )


# =========================================================
# ✅ FOLLOW
# =========================================================
@app.route("/api/follow/request", methods=["POST"])
def api_follow_request():
    uid = current_user()
    if not uid:
        return jsonify({"success": False, "message": "Login required"})

    username = (request.json.get("username", "") or "").strip().lower()
    target = user_by_username(username)
    if not target:
        return jsonify({"success": False, "message": "User not found"})

    if int(target["id"]) == int(uid):
        return jsonify({"success": False, "message": "You cannot follow yourself"})

    status = "accepted" if int(target["is_private"] or 0) == 0 else "pending"

    with get_db() as db:
        try:
            db.execute("""
                INSERT INTO follows(follower_id, following_id, status, created_at)
                VALUES(?,?,?,?)
            """, (uid, target["id"], status, int(time.time())))
        except:
            db.execute("""
                UPDATE follows SET status=? WHERE follower_id=? AND following_id=?
            """, (status, uid, target["id"]))

    return jsonify({"success": True, "status": status})


@app.route("/api/follow/unfollow", methods=["POST"])
def api_follow_unfollow():
    uid = current_user()
    if not uid:
        return jsonify({"success": False})

    username = (request.json.get("username", "") or "").strip().lower()
    target = user_by_username(username)
    if not target:
        return jsonify({"success": False})

    with get_db() as db:
        db.execute("""
            DELETE FROM follows WHERE follower_id=? AND following_id=?
        """, (uid, target["id"]))

    return jsonify({"success": True})


@app.route("/api/follow/requests", methods=["GET"])
def api_follow_requests_list():
    uid = current_user()
    if not uid:
        return jsonify([])

    with get_db() as db:
        rows = db.execute("""
            SELECT f.id as req_id, u.username, u.name, f.created_at
            FROM follows f
            JOIN users u ON u.id = f.follower_id
            WHERE f.following_id=? AND f.status='pending'
            ORDER BY f.created_at DESC
        """, (uid,)).fetchall()

    return jsonify([dict(r) for r in rows])


@app.route("/api/follow/requests/accept", methods=["POST"])
def api_follow_requests_accept():
    uid = current_user()
    if not uid:
        return jsonify({"success": False})

    req_id = request.json.get("req_id")
    if not req_id:
        return jsonify({"success": False})

    with get_db() as db:
        db.execute("""
            UPDATE follows SET status='accepted'
            WHERE id=? AND following_id=?
        """, (req_id, uid))

    return jsonify({"success": True})


@app.route("/api/follow/requests/reject", methods=["POST"])
def api_follow_requests_reject():
    uid = current_user()
    if not uid:
        return jsonify({"success": False})

    req_id = request.json.get("req_id")
    if not req_id:
        return jsonify({"success": False})

    with get_db() as db:
        db.execute("""
            DELETE FROM follows
            WHERE id=? AND following_id=? AND status='pending'
        """, (req_id, uid))

    return jsonify({"success": True})


# =========================================================
# ✅ FOLLOWERS / FOLLOWING LISTS + STATS + REMOVE
# =========================================================

@app.route("/api/follow/stats", methods=["GET"])
def api_follow_stats():
    uid = current_user()
    if not uid:
        return jsonify({"success": False})

    username = (request.args.get("username", "") or "").strip().lower()
    target = user_by_username(username)
    if not target:
        return jsonify({"success": False, "message": "User not found"})

    with get_db() as db:
        followers = db.execute("""
            SELECT COUNT(*) as c
            FROM follows
            WHERE following_id=? AND status='accepted'
        """, (target["id"],)).fetchone()["c"]

        following = db.execute("""
            SELECT COUNT(*) as c
            FROM follows
            WHERE follower_id=? AND status='accepted'
        """, (target["id"],)).fetchone()["c"]

    return jsonify({
        "success": True,
        "followers": int(followers),
        "following": int(following)
    })


@app.route("/api/follow/followers", methods=["GET"])
def api_follow_list_followers():
    viewer_id = current_user()
    if not viewer_id:
        return jsonify({"success": False, "message": "Login required"})

    username = (request.args.get("username", "") or "").strip().lower()
    target = user_by_username(username)
    if not target:
        return jsonify({"success": False, "message": "User not found"})

    with get_db() as db:
        rows = db.execute("""
            SELECT
                u.username, u.name, u.profile_pic,
                f.created_at,
                CASE WHEN vf.id IS NULL THEN 0 ELSE 1 END AS viewer_follows_user,
                CASE WHEN uv.id IS NULL THEN 0 ELSE 1 END AS user_follows_viewer
            FROM follows f
            JOIN users u ON u.id = f.follower_id

            LEFT JOIN follows vf
              ON vf.follower_id = ? AND vf.following_id = u.id AND vf.status='accepted'

            LEFT JOIN follows uv
              ON uv.follower_id = u.id AND uv.following_id = ? AND uv.status='accepted'

            WHERE f.following_id=? AND f.status='accepted'
            ORDER BY f.created_at DESC
        """, (viewer_id, viewer_id, target["id"])).fetchall()

    out = []
    for r in rows:
        out.append({
            "username": r["username"],
            "name": r["name"],
            "profile_pic": r["profile_pic"] or "",
            "created_at": r["created_at"],
            "viewer_follows_user": int(r["viewer_follows_user"] or 0),
            "user_follows_viewer": int(r["user_follows_viewer"] or 0),
        })

    return jsonify({"success": True, "list": out})


@app.route("/api/follow/following", methods=["GET"])
def api_follow_list_following():
    viewer_id = current_user()
    if not viewer_id:
        return jsonify({"success": False, "message": "Login required"})

    username = (request.args.get("username", "") or "").strip().lower()
    target = user_by_username(username)
    if not target:
        return jsonify({"success": False, "message": "User not found"})

    with get_db() as db:
        rows = db.execute("""
            SELECT
                u.username, u.name, u.profile_pic,
                f.created_at,
                CASE WHEN vf.id IS NULL THEN 0 ELSE 1 END AS viewer_follows_user,
                CASE WHEN uv.id IS NULL THEN 0 ELSE 1 END AS user_follows_viewer
            FROM follows f
            JOIN users u ON u.id = f.following_id

            LEFT JOIN follows vf
              ON vf.follower_id = ? AND vf.following_id = u.id AND vf.status='accepted'

            LEFT JOIN follows uv
              ON uv.follower_id = u.id AND uv.following_id = ? AND uv.status='accepted'

            WHERE f.follower_id=? AND f.status='accepted'
            ORDER BY f.created_at DESC
        """, (viewer_id, viewer_id, target["id"])).fetchall()

    out = []
    for r in rows:
        out.append({
            "username": r["username"],
            "name": r["name"],
            "profile_pic": r["profile_pic"] or "",
            "created_at": r["created_at"],
            "viewer_follows_user": int(r["viewer_follows_user"] or 0),
            "user_follows_viewer": int(r["user_follows_viewer"] or 0),
        })

    return jsonify({"success": True, "list": out})


@app.route("/api/follow/remove_follower", methods=["POST"])
def api_follow_remove_follower():
    uid = current_user()
    if not uid:
        return jsonify({"success": False, "message": "Login required"})

    username = (request.json.get("username", "") or "").strip().lower()
    target = user_by_username(username)
    if not target:
        return jsonify({"success": False, "message": "User not found"})

    with get_db() as db:
        db.execute("""
            DELETE FROM follows
            WHERE follower_id=? AND following_id=? AND status='accepted'
        """, (target["id"], uid))

    return jsonify({"success": True})


# =========================================================
# ✅ FORGOT / RESET
# =========================================================
@app.route("/forgot")
def forgot_page():
    return render_template("forgot.html")


@app.route("/reset")
def reset_page():
    return render_template("reset.html")


@app.route("/api/forgot", methods=["POST"])
def api_forgot():
    email = request.json.get("email", "").strip().lower()

    with get_db() as db:
        user = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()

    if not user:
        return jsonify({"success": True, "sent": True})

    code = str(random.randint(100000, 999999))
    expires_at = int(time.time()) + 10 * 60

    with get_db() as db:
        db.execute("DELETE FROM password_resets WHERE user_id=?", (user["id"],))
        db.execute("INSERT INTO password_resets(user_id, code, expires_at) VALUES(?,?,?)",
                   (user["id"], code, expires_at))

    print("\n✅ MoodMap Reset Code:", code, "(valid for 10 minutes)\n")

    ok = send_reset_email(email, code)
    if not ok:
        return jsonify({"success": False, "message": "Email sending failed. Try again later."})

    return jsonify({"success": True, "sent": True})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    data = request.json
    email = data.get("email", "").strip().lower()
    code = data.get("code", "").strip()
    new_password = data.get("new_password", "")

    if not email or not code or not new_password or len(new_password) < 4:
        return jsonify({"success": False, "message": "Invalid request"})

    with get_db() as db:
        user = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if not user:
            return jsonify({"success": False, "message": "Invalid request"})

        row = db.execute(
            "SELECT * FROM password_resets WHERE user_id=? AND code=?",
            (user["id"], code)
        ).fetchone()

        if not row:
            return jsonify({"success": False, "message": "Invalid code"})

        if int(row["expires_at"]) < int(time.time()):
            return jsonify({"success": False, "message": "Code expired"})

        hashed = generate_password_hash(new_password)
        db.execute("UPDATE users SET password=? WHERE id=?", (hashed, user["id"]))
        db.execute("DELETE FROM password_resets WHERE user_id=?", (user["id"],))

    return jsonify({"success": True})


# =========================================================
# ✅ FAVORITES
# =========================================================
@app.route("/api/favorites", methods=["GET"])
def api_favorites_list():
    uid = current_user()
    if not uid:
        return jsonify([])

    with get_db() as db:
        rows = db.execute("""
            SELECT place_id, name, category, lat, lon, created_at
            FROM favorites
            WHERE user_id=?
            ORDER BY created_at DESC
        """, (uid,)).fetchall()

    return jsonify([dict(r) for r in rows])


@app.route("/api/favorites/add", methods=["POST"])
def api_favorites_add():
    uid = current_user()
    if not uid:
        return jsonify({"success": False})

    p = request.json
    place_id = p.get("place_id")
    name = p.get("name")
    category = p.get("category")
    lat = p.get("lat")
    lon = p.get("lon")

    if not place_id:
        return jsonify({"success": False})

    try:
        with get_db() as db:
            db.execute("""
                INSERT INTO favorites(user_id, place_id, name, category, lat, lon, created_at)
                VALUES(?,?,?,?,?,?,?)
            """, (uid, place_id, name, category, lat, lon, int(time.time())))
    except:
        pass

    return jsonify({"success": True})


@app.route("/api/favorites/remove", methods=["POST"])
def api_favorites_remove():
    uid = current_user()
    if not uid:
        return jsonify({"success": False})

    place_id = request.json.get("place_id")

    with get_db() as db:
        db.execute("DELETE FROM favorites WHERE user_id=? AND place_id=?", (uid, place_id))

    return jsonify({"success": True})


# =========================================================
# ✅ PLACES API (FIXED LOGIC)
# =========================================================
def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return 6371 * c


MOOD_TAGS = {
    "work": ["cafe"],
    "date": ["restaurant"],
    "quick_bite": ["fast_food"],
    "budget": ["restaurant", "fast_food"]
}


def _safe_str(x):
    return (x or "").strip()


def _contains_any(text: str, keywords):
    if not text:
        return False
    t = text.lower()
    return any(k in t for k in keywords)


# ✅ keyword banks for strict filtering
WORK_KEYWORDS = [
    "starbucks", "ccd", "cafe coffee day", "third wave", "thirdwave",
    "book cafe", "book café", "roastery", "coffee", "coffee house",
    "cowork", "co-work", "workspace", "study", "library", "reading",
    "iraj", "irani"
]

DATE_KEYWORDS = [
    "bistro", "lounge", "rooftop", "terrace", "garden", "aesthetic",
    "cafe", "café", "coffee", "patisserie", "bakery", "brunch"
]

DATE_BAD_KEYWORDS = [
    "dhaba", "canteen", "mess", "misal", "vada pav", "vadapav", "tapri",
    "roll", "shawarma", "momos"
]

BUDGET_KEYWORDS = [
    "misal", "vadapav", "vada pav", "poha", "upma", "chai", "tea", "tapri",
    "momos", "roll", "shawarma", "sandwich", "bhurji", "omelette",
    "chinese", "noodles", "fried rice", "thali", "mess", "bhojanalay",
    "snacks", "juice", "cold coffee", "tiffin"
]

EXPENSIVE_KEYWORDS = [
    "fine dine", "fine-dine", "luxury", "premium", "bar", "pub"
]


def _hard_filter_place(mood: str, tags: dict):
    """
    ✅ Critical filter: prevents wrong places from leaking between moods.
    """
    amenity = _safe_str(tags.get("amenity", "")).lower()
    name = _safe_str(tags.get("name", "")).lower()

    if mood == "quick_bite":
        return amenity == "fast_food"

    if mood == "budget":
        # budget should NOT include cafes
        if amenity not in ["restaurant", "fast_food", "food_court"]:
            return False
        if _contains_any(name, EXPENSIVE_KEYWORDS):
            return False
        return True

    if mood == "date":
        if amenity not in ["cafe", "restaurant"]:
            return False
        if amenity == "fast_food":
            return False
        if _contains_any(name, DATE_BAD_KEYWORDS):
            return False
        # allow date vibe if keywords or outdoor seating or wheelchair
        vibe_ok = (
            _contains_any(name, DATE_KEYWORDS)
            or _safe_str(tags.get("outdoor_seating")).lower() == "yes"
            or _safe_str(tags.get("wheelchair")).lower() == "yes"
        )
        return vibe_ok

    if mood == "work":
        # coworking
        if amenity == "coworking_space":
            return True
        if _safe_str(tags.get("office")).lower() == "coworking":
            return True

        if amenity == "cafe":
            wifi_ok = _safe_str(tags.get("internet_access")).lower() in ["yes", "wlan", "wifi"]
            kw_ok = _contains_any(name, WORK_KEYWORDS)
            return wifi_ok or kw_ok

        return False

    return True


def _score_place(mood: str, tags: dict, distance_km: float):
    """
    Ranking system:
    - distance matters
    - mood-based scoring + keyword boosts
    """
    score = 0.0

    # base distance
    if distance_km <= 0.3:
        score += 18
    elif distance_km <= 0.8:
        score += 14
    elif distance_km <= 1.5:
        score += 10
    elif distance_km <= 2.5:
        score += 6
    elif distance_km <= 4:
        score += 2
    else:
        score -= 2

    name = _safe_str(tags.get("name", "")).lower()
    amenity = _safe_str(tags.get("amenity", "")).lower()

    # boosts
    if _safe_str(tags.get("opening_hours")):
        score += 2
    if _safe_str(tags.get("website")) or _safe_str(tags.get("contact:website")):
        score += 2
    if _safe_str(tags.get("phone")) or _safe_str(tags.get("contact:phone")):
        score += 1

    if _safe_str(tags.get("internet_access")).lower() in ["yes", "wlan", "wifi"]:
        score += 5

    if mood == "work":
        if amenity == "coworking_space" or _safe_str(tags.get("office")).lower() == "coworking":
            score += 60
        if amenity == "cafe":
            score += 16
        if _contains_any(name, WORK_KEYWORDS):
            score += 14
        if amenity == "fast_food":
            score -= 40

    elif mood == "date":
        if amenity == "cafe":
            score += 18
        if amenity == "restaurant":
            score += 14
        if _safe_str(tags.get("outdoor_seating")).lower() == "yes":
            score += 14
        if _contains_any(name, DATE_KEYWORDS):
            score += 12
        if _contains_any(name, DATE_BAD_KEYWORDS):
            score -= 25
        if amenity == "fast_food":
            score -= 60

    elif mood == "quick_bite":
        if amenity == "fast_food":
            score += 30
        else:
            score -= 70

    elif mood == "budget":
        if amenity == "fast_food":
            score += 12
        if amenity == "restaurant":
            score += 10
        if amenity == "food_court":
            score += 14
        if _contains_any(name, BUDGET_KEYWORDS):
            score += 22
        if _contains_any(name, EXPENSIVE_KEYWORDS):
            score -= 18

    return score


def fetch_places_for_mood(mood, lat, lon, radius=5000):
    """
    mood-specific Overpass query building
    """
    lat = float(lat)
    lon = float(lon)

    blocks = []

    if mood == "work":
        blocks.append(f'node["amenity"="coworking_space"](around:{radius},{lat},{lon});')
        blocks.append(f'node["office"="coworking"](around:{radius},{lat},{lon});')
        blocks.append(f'node["amenity"="cafe"](around:{radius},{lat},{lon});')

    elif mood == "date":
        blocks.append(f'node["amenity"="cafe"](around:{radius},{lat},{lon});')
        blocks.append(f'node["amenity"="restaurant"](around:{radius},{lat},{lon});')
        blocks.append(f'node["amenity"="cafe"]["outdoor_seating"="yes"](around:{radius},{lat},{lon});')
        blocks.append(f'node["amenity"="restaurant"]["outdoor_seating"="yes"](around:{radius},{lat},{lon});')
        blocks.append(f'node["amenity"="cafe"]["internet_access"="yes"](around:{radius},{lat},{lon});')
        blocks.append(f'node["amenity"="restaurant"]["internet_access"="yes"](around:{radius},{lat},{lon});')

    elif mood == "quick_bite":
        blocks.append(f'node["amenity"="fast_food"](around:{radius},{lat},{lon});')

    elif mood == "budget":
        # ✅ FIX: no cafes in budget at all
        blocks.append(f'node["amenity"="restaurant"](around:{radius},{lat},{lon});')
        blocks.append(f'node["amenity"="fast_food"](around:{radius},{lat},{lon});')
        blocks.append(f'node["amenity"="food_court"](around:{radius},{lat},{lon});')

    query = f"""
    [out:json][timeout:30];
    (
      {''.join(blocks)}
    );
    out 160;
    """

    headers = {
        "User-Agent": "MoodMap/1.0 (contact: moodmap)",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Cache-Control": "no-cache"
    }

    for url in OVERPASS_URLS:
        try:
            res = requests.post(url, data=query, timeout=28, headers=headers)
            txt = (res.text or "").strip()

            if not txt or "html" in txt.lower():
                continue

            data = res.json()
            elements = data.get("elements", [])
            if elements:
                return elements

        except Exception as e:
            print("⚠️ Overpass fail:", url, "->", e)
            continue

    return []


# =========================================================
# ✅ NEW: PLACE DETAILS SYSTEM (Option 1 Hybrid Free)
# =========================================================

PLACE_DETAILS_CACHE = {}
PLACE_DETAILS_TTL_SEC = 10 * 60


def _cache_get(key: str):
    try:
        item = PLACE_DETAILS_CACHE.get(key)
        if not item:
            return None
        if int(time.time()) > int(item.get("expires_at", 0)):
            PLACE_DETAILS_CACHE.pop(key, None)
            return None
        return item.get("data")
    except:
        return None


def _cache_set(key: str, data):
    try:
        PLACE_DETAILS_CACHE[key] = {
            "expires_at": int(time.time()) + PLACE_DETAILS_TTL_SEC,
            "data": data
        }
    except:
        pass


def _safe_float(x):
    try:
        return float(x)
    except:
        return None


def _build_maps_url(lat, lon):
    try:
        return f"https://www.google.com/maps?q={lat},{lon}"
    except:
        return ""


def _clean_tag_value(v):
    try:
        if v is None:
            return ""
        v = str(v).strip()
        if len(v) > 340:
            v = v[:340] + "…"
        return v
    except:
        return ""


def _pick_category_from_tags(tags: dict):
    if not tags:
        return "place"
    for k in ["amenity", "shop", "tourism", "leisure", "office", "building"]:
        if tags.get(k):
            return str(tags.get(k))
    return "place"


def _extract_contact(tags: dict):
    if not tags:
        tags = {}
    phone = tags.get("phone") or tags.get("contact:phone") or tags.get("mobile") or tags.get("contact:mobile") or ""
    website = tags.get("website") or tags.get("contact:website") or tags.get("url") or tags.get("contact:url") or ""
    email = tags.get("email") or tags.get("contact:email") or ""
    instagram = tags.get("contact:instagram") or tags.get("instagram") or ""
    facebook = tags.get("contact:facebook") or tags.get("facebook") or ""
    return {
        "phone": _clean_tag_value(phone),
        "website": _clean_tag_value(website),
        "email": _clean_tag_value(email),
        "instagram": _clean_tag_value(instagram),
        "facebook": _clean_tag_value(facebook),
    }



def _reverse_geocode_nominatim(lat, lon):
    """
    Returns:
      { display_name, address }
    """
    try:
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {
            "format": "jsonv2",
            "lat": lat,
            "lon": lon,
            "zoom": 18,
            "addressdetails": 1
        }
        headers = {
            "User-Agent": "MoodMap/1.0 (contact: moodmap)"
        }
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        return data
    except Exception as e:
        print("⚠️ nominatim reverse error:", e)
        return None


def _fetch_overpass_element(osm_type: str, osm_id: int):
    """
    Returns an element dict with tags + center/lat/lon.
    """
    osm_type = (osm_type or "").strip().lower()
    if osm_type not in ["node", "way", "relation"]:
        return None

    # for way/relation, ask for center
    query = f"""
    [out:json][timeout:25];
    (
      {osm_type}({int(osm_id)});
    );
    out center tags;
    """

    headers = {
        "User-Agent": "MoodMap/1.0 (contact: moodmap)",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Cache-Control": "no-cache"
    }

    for url in OVERPASS_URLS:
        try:
            res = requests.post(url, data=query, timeout=22, headers=headers)
            txt = (res.text or "").strip()

            if not txt or "html" in txt.lower():
                continue

            data = res.json()
            elements = data.get("elements", [])
            if not elements:
                continue

            return elements[0]
        except Exception as e:
            print("⚠️ Overpass element fail:", url, "->", e)
            continue

    return None


def _wiki_summary_from_title(title: str):
    """
    Wikipedia REST API summary for image + short extract.
    Returns:
      { ok, title, extract, thumbnail }
    """
    try:
        if not title:
            return None

        # Wikipedia title typically comes like: en:Some_Page
        # We handle: en:Title, Title
        if ":" in title and title.split(":", 1)[0] in ["en", "hi", "mr"]:
            lang, t = title.split(":", 1)
        else:
            lang, t = "en", title

        t = t.strip().replace(" ", "_")

        url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{t}"
        headers = {
            "User-Agent": "MoodMap/1.0 (contact: moodmap)"
        }
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        thumb = ""
        try:
            thumb = (data.get("thumbnail") or {}).get("source") or ""
        except:
            thumb = ""

        return {
            "ok": True,
            "title": data.get("title") or "",
            "extract": data.get("extract") or "",
            "thumbnail": thumb
        }
    except Exception as e:
        print("⚠️ wiki summary error:", e)
        return None


def _place_image_fallback(category: str):
    """
    Professional fallback images (free, stable).
    Uses Unsplash source endpoint (no key) based on keyword.
    """
    cat = (category or "place").lower()

    # choose keyword
    key = "city"
    if "cafe" in cat:
        key = "cafe"
    elif "restaurant" in cat:
        key = "restaurant"
    elif "fast_food" in cat or "fast" in cat:
        key = "street+food"
    elif "hospital" in cat:
        key = "hospital"
    elif "school" in cat or "college" in cat or "university" in cat:
        key = "campus"
    elif "gym" in cat:
        key = "gym"
    elif "park" in cat:
        key = "park"
    elif "hotel" in cat:
        key = "hotel"
    elif "mall" in cat or "shop" in cat:
        key = "shopping"
    elif "atm" in cat or "bank" in cat:
        key = "bank"
    elif "pharmacy" in cat:
        key = "pharmacy"
    elif "cinema" in cat or "theatre" in cat:
        key = "cinema"

    return f"https://source.unsplash.com/1200x600/?{key}"


def _resolve_place_image(tags: dict, category: str):
    """
    Hybrid free:
    1) OSM tag image=
    2) wikimedia_commons
    3) wikipedia summary thumbnail
    4) fallback by category
    Returns: (image_url, gallery_list, wiki_extract)
    """
    gallery = []
    wiki_extract = ""

    try:
        if not tags:
            tags = {}

        # 1) direct image tag
        img = (tags.get("image") or "").strip()
        if img.startswith("http://") or img.startswith("https://"):
            return img, gallery, wiki_extract

        # 2) wikipedia tag
        wiki = (tags.get("wikipedia") or "").strip()
        if wiki:
            ws = _wiki_summary_from_title(wiki)
            if ws and ws.get("thumbnail"):
                wiki_extract = ws.get("extract") or ""
                return ws.get("thumbnail"), gallery, wiki_extract

        # 3) if wikidata present, try wikipedia anyway (basic way: not all Q have direct thumbnail)
        # We'll keep it simple because Wikidata API adds extra dependencies.
        # If you want, later we can expand Q-id -> sitelinks -> thumbnail.
    except:
        pass

    return _place_image_fallback(category), gallery, wiki_extract


@app.route("/api/place_details", methods=["GET"])
def api_place_details():
    uid = current_user()
    if not uid:
        return jsonify({"ok": False, "message": "Login required"}), 403

    osm_type = (request.args.get("type") or "").strip().lower()
    osm_id = request.args.get("id")

    lat = request.args.get("lat")
    lon = request.args.get("lon")

    # optional: name/category as fallback
    fallback_name = (request.args.get("name") or "").strip()
    fallback_category = (request.args.get("category") or "").strip()

    # ================= cache =================
    cache_key = ""
    if osm_type and osm_id:
        cache_key = f"{osm_type}/{osm_id}"
        cached = _cache_get(cache_key)
        if cached:
            return jsonify({"success": True, "place": cached})


    element = None
    tags = {}
    pl_lat = None
    pl_lon = None

    # ================= Overpass element =================
    if osm_type in ["node", "way", "relation"] and osm_id:
        try:
            element = _fetch_overpass_element(osm_type, int(osm_id))
        except:
            element = None

        if element:
            tags = element.get("tags", {}) or {}

            # node has lat/lon
            if element.get("lat") and element.get("lon"):
                pl_lat = element.get("lat")
                pl_lon = element.get("lon")
            else:
                # way/relation center
                c = element.get("center") or {}
                pl_lat = c.get("lat")
                pl_lon = c.get("lon")

                

    # ================= fallback coords =================
    if pl_lat is None and lat is not None:
        pl_lat = _safe_float(lat)
    if pl_lon is None and lon is not None:
        pl_lon = _safe_float(lon)

    if pl_lat is None or pl_lon is None:
        return jsonify({"ok": False, "message": "Missing coordinates"}), 400

# ================= reverse geocode (cached) =================
    addr_cache_key = f"addr:{round(pl_lat,5)},{round(pl_lon,5)}"
    cached_addr = _cache_get(addr_cache_key)
    if cached_addr:
        address = cached_addr
    else:
        rev = _reverse_geocode_nominatim(pl_lat, pl_lon)
        address = rev.get("display_name", "") if rev else ""
        _cache_set(addr_cache_key, address)

# ================= category & name (ALWAYS RUN) =================
    category = _pick_category_from_tags(tags)
    if not category and fallback_category:
        category = fallback_category
    name = (tags.get("name") or "").strip()
    if not name:
        name = fallback_name or ""
    if not name:
        name = (category or "place").replace("_", " ").title()



    # ================= image =================
    img_url, gallery, wiki_extract = _resolve_place_image(tags, category)

    contact = _extract_contact(tags)

    opening_hours = _clean_tag_value(tags.get("opening_hours") or "")
    cuisine = _clean_tag_value(tags.get("cuisine") or "")
    wheelchair = _clean_tag_value(tags.get("wheelchair") or "")
    takeaway = _clean_tag_value(tags.get("takeaway") or "")
    delivery = _clean_tag_value(tags.get("delivery") or "")
    smoking = _clean_tag_value(tags.get("smoking") or "")
    toilets = _clean_tag_value(tags.get("toilets") or "")
    # fallback: prevent undefined opening_hours
    if not opening_hours and fallback_category:
        opening_hours = ""


    place_out = {
        "id": f"{osm_type}/{osm_id}" if osm_type and osm_id else "",
        "osm_type": osm_type or "",
        "osm_id": int(osm_id) if osm_id else None,
        "name": name,
        "category": category,
        "address": address,
        "lat": float(pl_lat),
        "lon": float(pl_lon),
        "opening_hours": opening_hours,
        "cuisine": cuisine,
        "wheelchair": wheelchair,
        "takeaway": takeaway,
        "delivery": delivery,
        "smoking": smoking,
        "toilets": toilets,
        "contact": contact,
        "image": img_url,
        "gallery": gallery,
        "wiki_extract": wiki_extract,
        "maps_url": _build_maps_url(pl_lat, pl_lon),
        "phone": contact.get("phone", ""),
        "website": contact.get("website", ""),
        "email": contact.get("email", ""),
        "tags": {
            # safe subset (professional)
            "brand": _clean_tag_value(tags.get("brand") or ""),
            "operator": _clean_tag_value(tags.get("operator") or ""),
            "level": _clean_tag_value(tags.get("level") or ""),
            "addr:street": _clean_tag_value(tags.get("addr:street") or ""),
            "addr:housenumber": _clean_tag_value(tags.get("addr:housenumber") or ""),
            "addr:postcode": _clean_tag_value(tags.get("addr:postcode") or ""),
            "addr:city": _clean_tag_value(tags.get("addr:city") or ""),
            "addr:state": _clean_tag_value(tags.get("addr:state") or ""),
        }
    }

    if cache_key:
        _cache_set(cache_key, place_out)

    return jsonify({"success": True, "place": place_out})



@app.route("/api/recommend", methods=["POST"])
def recommend():
    if not current_user():
        return jsonify([])

    data = request.json
    mood = (data.get("mood") or "").strip()
    user_lat = data.get("latitude")
    user_lon = data.get("longitude")

    if mood not in ["work", "date", "quick_bite", "budget"]:
        return jsonify([])

    if not user_lat or not user_lon:
        return jsonify([])

    radius = 4500
    if mood == "work":
        radius = 6000
    if mood == "quick_bite":
        radius = 4000
    if mood == "budget":
        radius = 6500

    raw = fetch_places_for_mood(mood, user_lat, user_lon, radius)

    if not raw:
        time.sleep(0.7)
        raw = fetch_places_for_mood(mood, user_lat, user_lon, radius)

    places = []
    seen = set()

    for i, p in enumerate(raw):
        t = p.get("tags", {})
        lat = p.get("lat")
        lon = p.get("lon")
        if not lat or not lon:
            continue

        # ✅ CRITICAL FIX: strict mood filter
        if not _hard_filter_place(mood, t):
            continue

        osm_id = p.get("id", i)
        pid = f"{p.get('type','node')}/{osm_id}"

        if pid in seen:
            continue
        seen.add(pid)

        distance = round(haversine(user_lat, user_lon, lat, lon), 2)
        category = t.get("amenity") or t.get("leisure") or t.get("office") or "place"
        name = t.get("name", (category or "place").replace("_", " ").title())

        if not name or name.strip().lower() in ["cafe", "restaurant", "fast food", "place"]:
            if distance > 1.2:
                continue

        score = _score_place(mood, t, float(distance))

        places.append({
            "place_id": pid,
            "name": name,
            "category": category,
            "distance": distance,
            "lat": lat,
            "lon": lon,
            "opening_hours": t.get("opening_hours", None),
            "phone": t.get("phone", t.get("contact:phone", None)),
            "website": t.get("website", t.get("contact:website", None)),
            "_score": score,

            # ✅ NEW: needed for place details system
            "osm_type": p.get("type", "node"),
            "osm_id": osm_id
        })

    places.sort(key=lambda x: (-x["_score"], x["distance"]))

    for p in places:
        p.pop("_score", None)

    return jsonify(places[:30])


# =========================================================
# ✅ DEPLOY RUN (Render/Cloud compatible)
# =========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

