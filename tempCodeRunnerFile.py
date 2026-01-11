from flask import Flask, request, jsonify, render_template, redirect, session, make_response
import sqlite3
import uuid
import time
import requests
import random
from math import radians, cos, sin, asin, sqrt
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "moodmaps_super_secret_key_123"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"


# ================= DB =================
def get_db():
    db = sqlite3.connect("users.db")
    db.row_factory = sqlite3.Row
    return db


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


# ================= AUTH HELPERS =================
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


# ================= ROUTES =================
@app.route("/")
def home():
    uid = current_user()
    if not uid:
        return redirect("/login")

    user = user_row(uid)
    return render_template("index.html", username=user["name"])


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
        resp = make_response(jsonify({"success": True, "name": user["name"]}))

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
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")

        if not name or not email or not password:
            return jsonify({"success": False, "message": "All fields are required"})

        hashed = generate_password_hash(password)

        try:
            with get_db() as db:
                db.execute("INSERT INTO users(name,email,password) VALUES(?,?,?)",
                           (name, email, hashed))
            return jsonify({"success": True})
        except:
            return jsonify({"success": False, "message": "Email already exists"})

    return render_template("signup.html")


@app.route("/logout")
def logout():
    session.clear()
    resp = redirect("/login")
    resp.delete_cookie("remember_token")
    return resp


# ================= FORGOT PASSWORD =================
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
        return jsonify({"success": True})

    code = str(random.randint(100000, 999999))
    expires_at = int(time.time()) + 10 * 60  # 10 mins

    with get_db() as db:
        db.execute("DELETE FROM password_resets WHERE user_id=?", (user["id"],))
        db.execute("INSERT INTO password_resets(user_id, code, expires_at) VALUES(?,?,?)",
                   (user["id"], code, expires_at))

    print("\n✅ MoodMap Reset Code:", code, "(valid for 10 minutes)\n")
    return jsonify({"success": True})


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


# ================= FAVORITES (PER USER) =================
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


# ================= PLACES API =================
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


def fetch_places(tags, lat, lon, radius=5000):
    blocks = []
    for tag in tags:
        blocks.append(f'node["amenity"="{tag}"](around:{radius},{lat},{lon});')

    query = f"""
    [out:json];
    (
      {''.join(blocks)}
    );
    out body;
    """

    res = requests.post(OVERPASS_URL, data=query, timeout=35)
    if not res.text.strip() or "html" in res.text.lower():
        return []
    return res.json().get("elements", [])


@app.route("/api/recommend", methods=["POST"])
def recommend():
    if not current_user():
        return jsonify([])

    data = request.json
    mood = data.get("mood")
    user_lat = data.get("latitude")
    user_lon = data.get("longitude")

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


if __name__ == "__main__":
    app.run(debug=True)
