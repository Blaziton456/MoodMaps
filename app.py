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

from flask_mail import Mail, Message

app = Flask(__name__)

# =========================================================
# ✅ SECRET KEY (safe for deploy)
# =========================================================
app.secret_key = os.environ.get("SECRET_KEY", "moodmaps_super_secret_key_123")

# =========================================================
# ✅ OVERPASS URL
# =========================================================
OVERPASS_URL = os.environ.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")

# =========================================================
# ✅ UPLOAD CONFIG
# =========================================================
UPLOAD_FOLDER = os.path.join("static", "uploads", "pfp")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}
MAX_PFP_SIZE_MB = 4

# =========================================================
# ✅ EMAIL CONFIG (ENV based for production)
# =========================================================
app.config["MAIL_SERVER"] = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
app.config["MAIL_PORT"] = int(os.environ.get("MAIL_PORT", "587"))
app.config["MAIL_USE_TLS"] = os.environ.get("MAIL_USE_TLS", "1") == "1"
app.config["MAIL_USE_SSL"] = os.environ.get("MAIL_USE_SSL", "0") == "1"

# ✅ IMPORTANT: DO NOT hardcode these in GitHub
app.config["MAIL_USERNAME"] = os.environ.get("MAIL_USERNAME", "")
app.config["MAIL_PASSWORD"] = os.environ.get("MAIL_PASSWORD", "")
app.config["MAIL_DEFAULT_SENDER"] = os.environ.get("MAIL_DEFAULT_SENDER", app.config["MAIL_USERNAME"])

mail = Mail(app)

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


def send_reset_email(to_email, code):
    # ✅ if no env config, just fail gracefully
    if not app.config.get("MAIL_USERNAME") or not app.config.get("MAIL_PASSWORD"):
        print("⚠️ Mail ENV not configured, cannot send email.")
        return False

    try:
        msg = Message(
            subject="MoodMap Password Reset Code",
            recipients=[to_email]
        )
        msg.body = f"Your MoodMap reset code is {code}. Valid for 10 minutes."
        msg.html = build_reset_email_html(code)
        mail.send(msg)
        return True
    except Exception as e:
        print("❌ Email sending failed:", e)
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
        places=places
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
        return jsonify({"success": True, "sent": False})

    return jsonify({"success": True, "sent": True})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    data = request.json
    email = data.get("email", "").strip().lower()
    code = data.get("code", "").strip()
    new_password = data.get("new_password", "")

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
# ✅ PLACES API
# =========================================================
def haversine(lat1, lon1, lat2, lon2):
    # ✅ FIXED BUG HERE
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


def fetch_places(tags, lat, lon, radius=5000):
    blocks = []
    for tag in tags:
        blocks.append(f'node["amenity"="{tag}"](around:{radius},{lat},{lon});')

    query = f"""
    [out:json][timeout:25];
    (
      {''.join(blocks)}
    );
    out body 40;
    """

    try:
        res = requests.post(OVERPASS_URL, data=query, timeout=25)
    except:
        return []

    if not res.text.strip() or "html" in res.text.lower():
        return []

    try:
        return res.json().get("elements", [])
    except:
        return []


@app.route("/api/recommend", methods=["POST"])
def recommend():
    if not current_user():
        return jsonify([])

    data = request.json
    mood = data.get("mood")
    user_lat = data.get("latitude")
    user_lon = data.get("longitude")

    if not user_lat or not user_lon:
        return jsonify([])

    tags = MOOD_TAGS.get(mood, [])
    if not tags:
        return jsonify([])

    raw = fetch_places(tags, user_lat, user_lon, 5000)

    places = []
    for i, p in enumerate(raw):
        t = p.get("tags", {})
        lat = p.get("lat")
        lon = p.get("lon")
        if not lat or not lon:
            continue

        distance = round(haversine(user_lat, user_lon, lat, lon), 2)
        category = t.get("amenity") or t.get("leisure") or "place"
        name = t.get("name", category.title())

        places.append({
            "place_id": f"osm_{p.get('id', i)}",
            "name": name,
            "category": category,
            "distance": distance,
            "lat": lat,
            "lon": lon,
            "opening_hours": t.get("opening_hours", None),
            "phone": t.get("phone", t.get("contact:phone", None)),
            "website": t.get("website", t.get("contact:website", None)),
        })

    places.sort(key=lambda x: x["distance"])
    return jsonify(places[:30])


# =========================================================
# ✅ DEPLOY RUN (Render/Cloud compatible)
# =========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
