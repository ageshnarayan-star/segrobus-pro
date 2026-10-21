"""Segro Bus: seat assignment and boarding validation (prototype).

One sign-in for everyone. After signing in, a rider picks the college or organisation they belong to
(or adds it), chooses a bus, and taps an ID card on the bus scanner. Admins and door staff use the
same account system with a separate admin sign-in entry. Each organisation has its own buses,
riders, announcements and scanner key.

Runs locally with SQLite (no setup) and on Vercel with Postgres (set DATABASE_URL).
"""
import csv
import hashlib
import io
import os
import random
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Flask, g, jsonify, request, send_from_directory, session
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # local SQLite use does not need it
    psycopg = None

BASE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(BASE, "public")
DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""
IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
SQLITE_PATH = os.environ.get("SEGRO_DB", os.path.join(BASE, "segro-data.db"))
ON_VERCEL = bool(os.environ.get("VERCEL"))
ORG_TYPES = ("college", "private", "government")
# Demo mode shows the example accounts on the sign-in page and enables "simulate taps".
# On by default for local runs; on Vercel set SEGRO_DEMO=1 for the hackathon and remove it afterwards.
DEMO = os.environ.get("SEGRO_DEMO", "0" if ON_VERCEL else "1") == "1"

if IS_PG and psycopg is None:
    raise RuntimeError("DATABASE_URL is set but psycopg is not installed (pip install -r requirements.txt)")
if ON_VERCEL and not (IS_PG and os.environ.get("SEGRO_SECRET_KEY") and os.environ.get("SEGRO_UID_SALT")):
    raise RuntimeError("On Vercel set DATABASE_URL, SEGRO_SECRET_KEY and SEGRO_UID_SALT (see README)")

SECRET_KEY = os.environ.get("SEGRO_SECRET_KEY", "dev-secret-change-me")
UID_SALT = os.environ.get("SEGRO_UID_SALT", "dev-salt-change-me")
TZ_NAME = os.environ.get("SEGRO_TZ", "Asia/Kolkata")
try:
    TZ = ZoneInfo(TZ_NAME)
except Exception:  # tzdata missing: fall back to India time
    TZ = timezone(timedelta(hours=5, minutes=30))

app = Flask(__name__, static_folder=None)
app.secret_key = SECRET_KEY
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=ON_VERCEL, PERMANENT_SESSION_LIFETIME=timedelta(hours=12))

INTEGRITY = (sqlite3.IntegrityError,) + ((psycopg.IntegrityError,) if psycopg else ())
BUS_EXTRA = ("route", "vehicle_no", "driver_name", "driver_phone")


# ------------------------------------------------------------------ database
def schema_statements(pg: bool):
    pk = "SERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY"
    return [
        f"""CREATE TABLE IF NOT EXISTS organisations(
            id {pk}, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
            org_type TEXT NOT NULL CHECK(org_type IN ('college','private','government')),
            device_key TEXT UNIQUE NOT NULL, join_code TEXT, last_seen TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS buses(
            id {pk}, org_id INTEGER NOT NULL REFERENCES organisations(id),
            bus_number TEXT NOT NULL, capacity INTEGER NOT NULL, route TEXT, vehicle_no TEXT,
            driver_name TEXT, driver_phone TEXT, UNIQUE(org_id, bus_number))""",
        f"""CREATE TABLE IF NOT EXISTS users(
            id {pk}, org_id INTEGER REFERENCES organisations(id),
            login_id TEXT NOT NULL, login_key TEXT UNIQUE NOT NULL, name TEXT NOT NULL, password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'rider' CHECK(role IN ('rider','staff','admin')),
            uid_hash TEXT UNIQUE, bus_id INTEGER REFERENCES buses(id),
            failed_logins INTEGER NOT NULL DEFAULT 0, locked_until TEXT)""",
        f"""CREATE TABLE IF NOT EXISTS boardings(
            id {pk}, user_id INTEGER NOT NULL REFERENCES users(id),
            bus_id INTEGER NOT NULL REFERENCES buses(id), trip TEXT NOT NULL,
            boarded_at TEXT NOT NULL, UNIQUE(user_id, trip))""",
        """CREATE TABLE IF NOT EXISTS enrol_requests(
            org_id INTEGER PRIMARY KEY REFERENCES organisations(id),
            user_id INTEGER NOT NULL REFERENCES users(id), expires_at TEXT NOT NULL)""",
        f"""CREATE TABLE IF NOT EXISTS announcements(
            id {pk}, org_id INTEGER NOT NULL REFERENCES organisations(id),
            bus_number TEXT, body TEXT NOT NULL, created_at TEXT NOT NULL)""",
    ]


_schema_ready = False


def ensure_schema(db):
    for st in schema_statements(IS_PG):
        db.q(st)
    for col in BUS_EXTRA:  # older databases get the newer bus columns
        try:
            db.q(f"ALTER TABLE buses ADD COLUMN {'IF NOT EXISTS ' if IS_PG else ''}{col} TEXT")
        except Exception as e:
            if "duplicate column" not in str(e).lower():
                raise


class DB:
    """Tiny wrapper so the same SQL (with ? placeholders) runs on SQLite and Postgres."""

    def __init__(self):
        if IS_PG:
            self.c = psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row, prepare_threshold=None)
        else:
            self.c = sqlite3.connect(SQLITE_PATH, isolation_level=None)
            self.c.row_factory = sqlite3.Row

    def q(self, sql, params=()):
        return self.c.execute(sql.replace("?", "%s") if IS_PG else sql, tuple(params))

    def one(self, sql, params=()):
        r = self.q(sql, params).fetchone()
        return dict(r) if r else None

    def all(self, sql, params=()):
        return [dict(r) for r in self.q(sql, params).fetchall()]

    def begin(self):
        self.q("BEGIN" if IS_PG else "BEGIN IMMEDIATE")

    def commit(self):
        self.q("COMMIT")

    def rollback(self):
        self.q("ROLLBACK")

    def close(self):
        self.c.close()


def get_db():
    global _schema_ready
    if "db" not in g:
        g.db = DB()
        if not _schema_ready:  # tables are created automatically on the first request
            ensure_schema(g.db)
            _schema_ready = True
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    db = DB()
    ensure_schema(db)
    db.close()


def bulk_insert(db, table, cols, rows, chunk=150):
    """Insert many rows with few round trips (matters on a hosted database)."""
    one = "(" + ",".join("?" * len(cols)) + ")"
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        flat = [v for r in part for v in r]
        db.q(f"INSERT INTO {table}({','.join(cols)}) VALUES {','.join([one] * len(part))}", flat)


# ------------------------------------------------------------------ helpers
def now():
    return datetime.now(TZ)


def hash_uid(uid: str) -> str:
    return hashlib.sha256((UID_SALT + uid.strip().upper()).encode()).hexdigest()


def login_key(login_id: str) -> str:
    return (login_id or "").strip().lower()


def current_trip(shift=None) -> str:
    n = now()
    return f"{n:%Y-%m-%d}-{shift if shift else ('AM' if n.hour < 12 else 'PM')}"


def is_future(iso):
    return bool(iso) and datetime.fromisoformat(iso) > now()


def reply(result, status, message, **extra):
    return jsonify({"result": result, "message": message, **extra}), status


def natural_key(s):
    return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", s) if t]


def roles_required(*roles):
    """Signed in (and, if roles are given, one of them). Loads g.user fresh from the database."""
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if "uid" not in session:
                return reply("AUTH_REQUIRED", 401, "Sign in first")
            u = get_db().one("SELECT * FROM users WHERE id=?", (session["uid"],))
            if not u:
                session.clear()
                return reply("AUTH_REQUIRED", 401, "Sign in first")
            if roles and u["role"] not in roles:
                return reply("FORBIDDEN", 403, "Not allowed for your role")
            g.user = u
            return f(*a, **k)
        return w
    return deco


def need_org(f):
    """Endpoints that only make sense once the user belongs to an organisation."""
    @wraps(f)
    def w(*a, **k):
        if not g.user["org_id"]:
            return reply("NO_ORG", 409, "Choose your college or organisation first")
        return f(*a, **k)
    return w


def user_org():
    return get_db().one("SELECT * FROM organisations WHERE id=?", (g.user["org_id"],)) if g.user["org_id"] else None


def device_org():
    key = request.headers.get("X-Device-Key", "")
    if not key:
        return None
    org = get_db().one("SELECT * FROM organisations WHERE device_key=?", (key,))
    if org:
        get_db().q("UPDATE organisations SET last_seen=? WHERE id=?", (now().isoformat(timespec="seconds"), org["id"]))
    return org


def new_org(db, name, typ, join_code=None, code=None):
    base = re.sub(r"[^A-Z0-9]", "", name.upper())[:8] or "ORG"
    for _ in range(8):
        c = code or f"{base}-{secrets.token_hex(2).upper()}"
        key = secrets.token_hex(16)
        try:
            db.q("INSERT INTO organisations(code,name,org_type,device_key,join_code) VALUES(?,?,?,?,?)",
                 (c, name, typ, key, join_code or None))
        except INTEGRITY:
            if code:
                raise
            continue
        return db.one("SELECT * FROM organisations WHERE code=?", (c,))
    raise RuntimeError("could not create organisation code")


def upsert_buses(db, org_id, csv_text):
    """CSV header: route_no (or bus_number), capacity, and optionally vehicle_no, route, driver_name, driver_phone.
    Existing buses are updated; columns missing from the CSV are left as they were."""
    added = updated = 0
    errors = []
    for n, row in enumerate(csv.DictReader(io.StringIO(csv_text)), start=2):
        if n > 501:
            errors.append({"line": n, "error": "limit is 500 rows per import"})
            break
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        num = row.get("route_no") or row.get("bus_number") or ""
        try:
            cap = int(row.get("capacity") or 0)
        except ValueError:
            cap = 0
        if not num or len(num) > 20 or not 1 <= cap <= 200:
            errors.append({"line": n, "error": "route_no and a capacity of 1-200 are required"})
            continue
        vals = {c: (row[c][:400] if row.get(c) else None) for c in BUS_EXTRA if c in row}
        if db.one("SELECT 1 AS x FROM buses WHERE org_id=? AND bus_number=?", (org_id, num)):
            sets = ", ".join(f"{c}=?" for c in vals)
            db.q(f"UPDATE buses SET capacity=?{', ' + sets if sets else ''} WHERE org_id=? AND bus_number=?",
                 (cap, *vals.values(), org_id, num))
            updated += 1
        else:
            cols = ["org_id", "bus_number", "capacity", *vals]
            db.q(f"INSERT INTO buses({','.join(cols)}) VALUES({','.join('?' * len(cols))})", (org_id, num, cap, *vals.values()))
            added += 1
    return added, updated, errors


# ------------------------------------------------------------------ boarding rules
def board_core(db, org, user, bus_number, shift=None):
    """valid rider -> bus exists -> assigned bus (not government) -> duplicate -> capacity -> record.
    Returns (result, http_status, message, extra)."""
    if user is None or user["org_id"] != org["id"] or user["role"] != "rider":
        return "INVALID_ID", 404, "ID not found", {}
    if shift is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,10}", shift):
        return "BAD_REQUEST", 400, "shift must be 1-10 letters, digits, - or _", {}
    bus = db.one("SELECT * FROM buses WHERE org_id=? AND bus_number=?", (org["id"], bus_number))
    if bus is None:
        return "UNKNOWN_BUS", 400, "This bus is not registered", {}
    if org["org_type"] != "government" and user["bus_id"] != bus["id"]:
        assigned = db.one("SELECT bus_number FROM buses WHERE id=?", (user["bus_id"],)) if user["bus_id"] else None
        return "WRONG_BUS", 409, "Rider is assigned to another bus", {"assigned_bus": assigned["bus_number"] if assigned else None}
    trip = current_trip(shift)
    db.begin()
    try:
        if IS_PG:  # serialise scans for this bus so two taps cannot both take the last seat
            db.q("SELECT id FROM buses WHERE id=? FOR UPDATE", (bus["id"],))
        if db.one("SELECT 1 AS x FROM boardings WHERE user_id=? AND trip=?", (user["id"], trip)):
            db.rollback()
            return "DUPLICATE", 409, "Already boarded for this trip", {}
        used = db.one("SELECT COUNT(*) AS c FROM boardings WHERE bus_id=? AND trip=?", (bus["id"], trip))["c"]
        if used >= bus["capacity"]:
            db.rollback()
            return "FULL", 409, "Bus is full", {"seats_left": 0}
        db.q("INSERT INTO boardings(user_id,bus_id,trip,boarded_at) VALUES(?,?,?,?)",
             (user["id"], bus["id"], trip, now().isoformat(timespec="seconds")))
        db.commit()
    except INTEGRITY:
        db.rollback()
        return "DUPLICATE", 409, "Already boarded for this trip", {}
    except Exception:
        db.rollback()
        raise
    return "OK", 200, f"Welcome, {user['name']}", {"seats_left": bus["capacity"] - used - 1}


def try_board(db, org, user, bus_number, shift=None):
    result, status, message, extra = board_core(db, org, user, bus_number, shift)
    return reply(result, status, message, **extra)


# ------------------------------------------------------------------ pages and health
@app.get("/")
def home():
    return send_from_directory(PUBLIC, "index.html")  # on Vercel public/ is served by the CDN


@app.get("/<path:name>")
def public_file(name):  # local dev only; on Vercel public/ is served by the CDN
    if name.startswith("api/"):
        return reply("NOT_FOUND", 404, "Not found")
    return send_from_directory(PUBLIC, name)


@app.get("/api/health")
def health():
    get_db().one("SELECT 1 AS ok")
    return jsonify({"ok": True, "database": "postgres" if IS_PG else "sqlite", "timezone": TZ_NAME})


@app.get("/api/orgs")
def list_orgs():
    """Public list used when a rider picks their organisation. Never exposes keys or join codes."""
    q = f"%{(request.args.get('q') or '').strip().lower()}%"
    rows = get_db().all("""SELECT o.code, o.name, o.org_type, o.join_code,
        (SELECT COUNT(*) FROM buses b WHERE b.org_id=o.id) AS buses FROM organisations o
        WHERE lower(o.name) LIKE ? ORDER BY o.name LIMIT 60""", (q,))
    return jsonify([{"code": r["code"], "name": r["name"], "org_type": r["org_type"],
                     "needs_code": bool(r["join_code"]), "buses": r["buses"]} for r in rows])


@app.get("/api/demo")
def demo_info():
    if not DEMO:
        return jsonify({"enabled": False})
    db = get_db()
    if not db.one("SELECT 1 AS x FROM users WHERE login_key='admin'"):
        return jsonify({"enabled": False})
    return jsonify({"enabled": True, "password": DEMO_PASSWORD, "accounts": [
        {"label": "Administrator", "login_id": "admin", "portal": "admin", "note": "Sathyabama demo: all buses, riders, insights"},
        {"label": "Door staff", "login_id": "staff", "portal": "admin", "note": "Live boarding and manual boarding"},
        {"label": "Student", "login_id": "student", "portal": "member", "note": "Route 9, with ride history"},
    ]})


DEMO_PASSWORD = "Segro@2026"


# ------------------------------------------------------------------ accounts
@app.post("/api/register")
def register():
    d = request.get_json(silent=True) or {}
    name, lid, pw = (d.get("name") or "").strip(), (d.get("login_id") or "").strip(), d.get("password") or ""
    if not 2 <= len(name) <= 80 or not 3 <= len(lid) <= 60 or len(pw) < 8:
        return reply("BAD_REQUEST", 400, "Enter your name, an email or ID (3+ characters) and a password of 8+ characters")
    db = get_db()
    try:
        db.q("INSERT INTO users(login_id,login_key,name,password_hash,role) VALUES(?,?,?,?,'rider')",
             (lid, login_key(lid), name, generate_password_hash(pw)))
    except INTEGRITY:
        return reply("EXISTS", 409, "That email or ID is already registered. Try signing in.")
    u = db.one("SELECT id FROM users WHERE login_key=?", (login_key(lid),))
    session.clear()
    session.permanent = True
    session["uid"] = u["id"]
    return reply("OK", 201, "Account created")


@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    db = get_db()
    u = db.one("SELECT * FROM users WHERE login_key=?", (login_key(d.get("login_id")),))
    if u and is_future(u["locked_until"]):
        return reply("LOCKED", 429, "Too many failed attempts. Try again in a few minutes.")
    if not u or not check_password_hash(u["password_hash"], d.get("password") or ""):
        if u:
            n = u["failed_logins"] + 1
            if n >= 5:
                db.q("UPDATE users SET failed_logins=0, locked_until=? WHERE id=?", ((now() + timedelta(minutes=5)).isoformat(), u["id"]))
            else:
                db.q("UPDATE users SET failed_logins=? WHERE id=?", (n, u["id"]))
        return reply("BAD_LOGIN", 401, "Wrong ID or password")
    if d.get("portal") == "admin" and u["role"] not in ("staff", "admin"):
        return reply("WRONG_PORTAL", 403, "This is a rider account. Use the main sign-in.")
    db.q("UPDATE users SET failed_logins=0, locked_until=NULL WHERE id=?", (u["id"],))
    session.clear()
    session.permanent = True
    session["uid"] = u["id"]
    return reply("OK", 200, "Signed in", role=u["role"])


@app.post("/api/logout")
def logout():
    session.clear()
    return reply("OK", 200, "Signed out")


@app.get("/api/me")
@roles_required()
def me():
    db, u = get_db(), g.user
    org = user_org()
    out = {"name": u["name"], "login_id": u["login_id"], "role": u["role"], "card_linked": bool(u["uid_hash"]),
           "org": {"name": org["name"], "type": org["org_type"], "code": org["code"]} if org else None, "bus": None}
    if org and u["role"] == "rider":
        if org["org_type"] == "government":
            out["boardings_total"] = db.one("SELECT COUNT(*) AS c FROM boardings WHERE user_id=?", (u["id"],))["c"]
        elif u["bus_id"]:
            bus = db.one("SELECT * FROM buses WHERE id=?", (u["bus_id"],))
            trip = current_trip()
            used = db.one("SELECT COUNT(*) AS c FROM boardings WHERE bus_id=? AND trip=?", (bus["id"], trip))["c"]
            boarded = db.one("SELECT 1 AS x FROM boardings WHERE user_id=? AND trip=?", (u["id"], trip)) is not None
            out["bus"] = {"bus_number": bus["bus_number"], "capacity": bus["capacity"], "route": bus["route"],
                          "vehicle_no": bus["vehicle_no"], "seats_left": bus["capacity"] - used, "boarded_this_trip": boarded}
        out["rides_total"] = db.one("SELECT COUNT(*) AS c FROM boardings WHERE user_id=?", (u["id"],))["c"]
    return jsonify(out)


@app.get("/api/me/history")
@roles_required("rider")
def my_history():
    return jsonify(get_db().all("""SELECT x.trip, x.boarded_at, b.bus_number FROM boardings x
        JOIN buses b ON b.id=x.bus_id WHERE x.user_id=? ORDER BY x.id DESC LIMIT 20""", (g.user["id"],)))


# ------------------------------------------------------------------ joining an organisation
@app.post("/api/me/org/join")
@roles_required("rider")
def join_org():
    d = request.get_json(silent=True) or {}
    db = get_db()
    if g.user["org_id"]:
        return reply("HAS_ORG", 409, "Leave your current organisation first")
    org = db.one("SELECT * FROM organisations WHERE code=?", ((d.get("org_code") or "").strip(),))
    if not org:
        return reply("NOT_FOUND", 404, "Organisation not found")
    if org["join_code"] and not secrets.compare_digest(str(d.get("join_code") or "").strip().upper(), org["join_code"].upper()):
        return reply("BAD_JOIN_CODE", 403, "That join code is not right. Ask your transport office for it.")
    db.q("UPDATE users SET org_id=?, bus_id=NULL WHERE id=?", (org["id"], g.user["id"]))
    return reply("OK", 200, f"You joined {org['name']}")


@app.post("/api/me/org/create")
@roles_required("rider")
def create_org():
    """A rider whose college or organisation is not listed can add it. They become its first administrator."""
    d = request.get_json(silent=True) or {}
    name, typ = (d.get("name") or "").strip(), d.get("org_type")
    if g.user["org_id"]:
        return reply("HAS_ORG", 409, "Leave your current organisation first")
    if not 3 <= len(name) <= 80 or typ not in ORG_TYPES:
        return reply("BAD_REQUEST", 400, "Enter the organisation name (3-80 characters) and choose its type")
    db = get_db()
    if db.one("SELECT 1 AS x FROM organisations WHERE lower(name)=?", (name.lower(),)):
        return reply("EXISTS", 409, "An organisation with that name already exists. Search for it and join instead.")
    org = new_org(db, name, typ)
    db.q("UPDATE users SET org_id=?, role='admin', bus_id=NULL WHERE id=?", (org["id"], g.user["id"]))
    return reply("OK", 201, f"{name} was added and you are its administrator", org_code=org["code"])


@app.post("/api/me/org/leave")
@roles_required("rider")
@need_org
def leave_org():
    get_db().q("UPDATE users SET org_id=NULL, bus_id=NULL WHERE id=?", (g.user["id"],))
    return reply("OK", 200, "You left the organisation")


@app.get("/api/org/buses")
@roles_required()
@need_org
def org_buses():
    org = user_org()
    rows = get_db().all("""SELECT b.bus_number, b.capacity, b.route, b.vehicle_no,
        (SELECT COUNT(*) FROM boardings x WHERE x.bus_id=b.id AND x.trip=?) AS boarded
        FROM buses b WHERE b.org_id=?""", (current_trip(), org["id"]))
    rows.sort(key=lambda r: natural_key(r["bus_number"]))
    return jsonify([{"bus_number": r["bus_number"], "capacity": r["capacity"], "route": r["route"],
                     "vehicle_no": r["vehicle_no"], "seats_left": r["capacity"] - r["boarded"]} for r in rows])


@app.post("/api/me/bus")
@roles_required("rider")
@need_org
def choose_bus():
    org = user_org()
    if org["org_type"] == "government":
        return reply("OPEN", 400, "Public buses use open ridership. There is no fixed bus to choose.")
    db = get_db()
    b = db.one("SELECT id, bus_number FROM buses WHERE org_id=? AND bus_number=?", (org["id"], str((request.get_json(silent=True) or {}).get("bus") or "").strip()))
    if not b:
        return reply("UNKNOWN_BUS", 404, "That bus is not available")
    db.q("UPDATE users SET bus_id=? WHERE id=?", (b["id"], g.user["id"]))
    return reply("OK", 200, f"Your bus is now {b['bus_number']}")


@app.get("/api/announcements")
@roles_required()
@need_org
def announcements():
    db, u = get_db(), g.user
    if u["role"] == "rider":
        mine = db.one("SELECT bus_number FROM buses WHERE id=?", (u["bus_id"],)) if u["bus_id"] else None
        rows = db.all("""SELECT id, bus_number, body, created_at FROM announcements WHERE org_id=?
            AND (bus_number IS NULL OR bus_number=?) ORDER BY id DESC LIMIT 5""", (u["org_id"], mine["bus_number"] if mine else ""))
    else:
        rows = db.all("SELECT id, bus_number, body, created_at FROM announcements WHERE org_id=? ORDER BY id DESC LIMIT 30", (u["org_id"],))
    return jsonify(rows)


# ------------------------------------------------------------------ scanner (ESP32)
@app.get("/api/ping")
def ping():
    org = device_org()
    if org is None:
        return reply("BAD_DEVICE_KEY", 401, "Unknown device")
    return reply("OK", 200, f"Scanner connected to {org['name']}", org_type=org["org_type"])


@app.post("/api/scan")
def scan():
    org = device_org()
    if org is None:
        return reply("BAD_DEVICE_KEY", 401, "Unknown device")
    d = request.get_json(silent=True) or {}
    uid, bus_number, shift = (d.get("uid") or "").strip(), (d.get("bus") or "").strip(), (d.get("shift") or None)
    if not uid or not bus_number:
        return reply("BAD_REQUEST", 400, "uid and bus are required")
    db = get_db()
    h = hash_uid(uid)
    user = db.one("SELECT * FROM users WHERE uid_hash=? AND org_id=?", (h, org["id"]))
    if user is None:  # an unknown card during an open enrolment window links to the chosen rider
        req = db.one("SELECT * FROM enrol_requests WHERE org_id=?", (org["id"],))
        if req and is_future(req["expires_at"]):
            target = db.one("SELECT * FROM users WHERE id=?", (req["user_id"],))
            try:
                db.q("UPDATE users SET uid_hash=? WHERE id=?", (h, target["id"]))
            except INTEGRITY:
                return reply("CARD_IN_USE", 409, "This card is already linked to someone")
            db.q("DELETE FROM enrol_requests WHERE org_id=?", (org["id"],))
            return reply("ENROLLED", 200, f"Card linked to {target['name']}")
    return try_board(db, org, user, bus_number, shift)


@app.post("/api/staff/board")  # manual fallback at the door
@roles_required("staff", "admin")
@need_org
def staff_board():
    d = request.get_json(silent=True) or {}
    db, org = get_db(), user_org()
    user = db.one("SELECT * FROM users WHERE org_id=? AND login_key=?", (org["id"], login_key(d.get("login_id"))))
    return try_board(db, org, user, (d.get("bus") or "").strip(), d.get("shift") or None)


@app.get("/api/dashboard")
@roles_required("staff", "admin")
@need_org
def dashboard():
    db, org = get_db(), user_org()
    trip = current_trip(request.args.get("shift") or None)
    rows = db.all("""SELECT b.bus_number, b.capacity, b.route, b.vehicle_no, b.driver_name, b.driver_phone,
        (SELECT COUNT(*) FROM boardings x WHERE x.bus_id=b.id AND x.trip=?) AS boarded
        FROM buses b WHERE b.org_id=?""", (trip, org["id"]))
    rows.sort(key=lambda r: natural_key(r["bus_number"]))
    return jsonify({"trip": trip, "scanner_last_seen": org["last_seen"], "buses": [
        {"bus_number": r["bus_number"], "capacity": r["capacity"], "boarded": r["boarded"],
         "seats_left": r["capacity"] - r["boarded"], "route": r["route"], "vehicle_no": r["vehicle_no"],
         "driver_name": r["driver_name"], "driver_phone": r["driver_phone"]} for r in rows]})


@app.get("/api/feed")
@roles_required("staff", "admin")
@need_org
def feed():
    """Latest scans for this organisation (staff see names, as they do at the door)."""
    return jsonify(get_db().all("""SELECT u.name, u.login_id, b.bus_number, x.trip, x.boarded_at
        FROM boardings x JOIN users u ON u.id=x.user_id JOIN buses b ON b.id=x.bus_id
        WHERE b.org_id=? ORDER BY x.boarded_at DESC, x.id DESC LIMIT 12""", (g.user["org_id"],)))


# ------------------------------------------------------------------ administrator


def admin_only(f):
    return roles_required("admin")(need_org(f))


@app.get("/api/admin/org")
@admin_only
def org_settings():
    o = user_org()
    return jsonify({"name": o["name"], "code": o["code"], "org_type": o["org_type"], "join_code": o["join_code"],
                    "device_key": o["device_key"], "last_seen": o["last_seen"]})


@app.post("/api/admin/org")
@admin_only
def update_org():
    d = request.get_json(silent=True) or {}
    db, o = get_db(), user_org()
    if "name" in d:
        name = (d.get("name") or "").strip()
        if not 3 <= len(name) <= 80:
            return reply("BAD_REQUEST", 400, "Name must be 3-80 characters")
        db.q("UPDATE organisations SET name=? WHERE id=?", (name, o["id"]))
    if "join_code" in d:
        jc = (d.get("join_code") or "").strip().upper()
        if jc and not re.fullmatch(r"[A-Z0-9-]{4,20}", jc):
            return reply("BAD_REQUEST", 400, "Join code: 4-20 letters, digits or dashes. Leave it empty to let anyone join.")
        db.q("UPDATE organisations SET join_code=? WHERE id=?", (jc or None, o["id"]))
    return reply("OK", 200, "Settings saved")


@app.post("/api/admin/device/rotate")
@admin_only
def rotate_key():
    key = secrets.token_hex(16)
    get_db().q("UPDATE organisations SET device_key=? WHERE id=?", (key, g.user["org_id"]))
    return reply("OK", 200, "New scanner key created. Put it in secrets.h and re-flash the scanner.", device_key=key)


@app.post("/api/admin/buses")
@admin_only
def add_bus():
    d = request.get_json(silent=True) or {}
    num = str(d.get("bus_number") or "").strip()
    try:
        cap = int(d.get("capacity"))
    except (TypeError, ValueError):
        cap = 0
    if not num or len(num) > 20 or not 1 <= cap <= 200:
        return reply("BAD_REQUEST", 400, "bus_number (max 20 chars) and capacity 1-200 are required")
    db, oid = get_db(), g.user["org_id"]
    if db.one("SELECT 1 AS x FROM buses WHERE org_id=? AND bus_number=?", (oid, num)):
        db.q("UPDATE buses SET capacity=? WHERE org_id=? AND bus_number=?", (cap, oid, num))
        return reply("OK", 200, f"Bus {num} capacity updated")
    db.q("INSERT INTO buses(org_id,bus_number,capacity) VALUES(?,?,?)", (oid, num, cap))
    return reply("OK", 201, f"Bus {num} added")


@app.post("/api/admin/buses/capacity-all")
@admin_only
def capacity_all():
    try:
        cap = int((request.get_json(silent=True) or {}).get("capacity"))
    except (TypeError, ValueError):
        cap = 0
    if not 1 <= cap <= 200:
        return reply("BAD_REQUEST", 400, "capacity must be 1-200")
    get_db().q("UPDATE buses SET capacity=? WHERE org_id=?", (cap, g.user["org_id"]))
    return reply("OK", 200, f"Every bus now has {cap} seats")


@app.post("/api/admin/buses/import")
@admin_only
def import_buses():
    added, updated, errors = upsert_buses(get_db(), g.user["org_id"], (request.get_json(silent=True) or {}).get("csv") or "")
    return jsonify({"added": added, "updated": updated, "errors": errors})


@app.get("/api/admin/riders")
@admin_only
def list_riders():
    rows = get_db().all("""SELECT u.login_id, u.name, b.bus_number, u.uid_hash
        FROM users u LEFT JOIN buses b ON b.id=u.bus_id
        WHERE u.org_id=? AND u.role='rider' ORDER BY u.name LIMIT 1000""", (g.user["org_id"],))
    return jsonify([{"login_id": r["login_id"], "name": r["name"], "bus": r["bus_number"], "card_linked": bool(r["uid_hash"])} for r in rows])


def org_rider(db, login_id):
    return db.one("SELECT id, name FROM users WHERE org_id=? AND login_key=? AND role='rider'", (g.user["org_id"], login_key(login_id)))


@app.post("/api/admin/riders/assign")
@admin_only
def assign_rider():
    d = request.get_json(silent=True) or {}
    db, oid = get_db(), g.user["org_id"]
    u = org_rider(db, d.get("login_id"))
    if not u:
        return reply("NOT_FOUND", 404, "No such rider")
    bus_no = (d.get("bus") or "").strip()
    bus_id = None
    if bus_no:
        b = db.one("SELECT id FROM buses WHERE org_id=? AND bus_number=?", (oid, bus_no))
        if not b:
            return reply("UNKNOWN_BUS", 400, "This bus is not registered")
        bus_id = b["id"]
    db.q("UPDATE users SET bus_id=? WHERE id=?", (bus_id, u["id"]))
    return reply("OK", 200, "Bus assigned" if bus_id else "Bus cleared")


@app.post("/api/admin/riders/enrol")
@admin_only
def start_enrol():
    """Next unknown card tapped on this organisation's scanner within 2 minutes is linked to this rider."""
    db, oid = get_db(), g.user["org_id"]
    u = org_rider(db, (request.get_json(silent=True) or {}).get("login_id"))
    if not u:
        return reply("NOT_FOUND", 404, "No such rider")
    db.q("DELETE FROM enrol_requests WHERE org_id=?", (oid,))
    db.q("INSERT INTO enrol_requests(org_id,user_id,expires_at) VALUES(?,?,?)", (oid, u["id"], (now() + timedelta(minutes=2)).isoformat()))
    return reply("OK", 200, f"Tap a new card on the scanner within 2 minutes to link it to {u['name']}")


@app.post("/api/admin/riders/unlink")
@admin_only
def unlink_card():
    db = get_db()
    u = org_rider(db, (request.get_json(silent=True) or {}).get("login_id"))
    if not u:
        return reply("NOT_FOUND", 404, "No such rider")
    db.q("UPDATE users SET uid_hash=NULL WHERE id=?", (u["id"],))
    return reply("OK", 200, "Card unlinked")


@app.post("/api/admin/riders/reset")
@admin_only
def reset_password():
    db = get_db()
    u = org_rider(db, (request.get_json(silent=True) or {}).get("login_id"))
    if not u:
        return reply("NOT_FOUND", 404, "No such rider")
    temp = secrets.token_urlsafe(8)
    db.q("UPDATE users SET password_hash=?, failed_logins=0, locked_until=NULL WHERE id=?", (generate_password_hash(temp), u["id"]))
    return reply("OK", 200, "Temporary password created (shown once)", temp_password=temp)


@app.post("/api/admin/riders/import")
@admin_only
def import_riders():
    """CSV header: login_id,name,bus (bus optional). Returns one-time temporary passwords."""
    text = (request.get_json(silent=True) or {}).get("csv") or ""
    db, oid = get_db(), g.user["org_id"]
    created, errors = [], []
    for n, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        if n > 201:  # password hashing is slow on purpose; keep one request short
            errors.append({"line": n, "error": "limit is 200 rows per import"})
            break
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        lid, name, bus_no = row.get("login_id", ""), row.get("name", ""), row.get("bus", "")
        if not 3 <= len(lid) <= 60 or not 2 <= len(name) <= 80:
            errors.append({"line": n, "error": "login_id (3+ characters) and name are required"})
            continue
        bus_id = None
        if bus_no:
            b = db.one("SELECT id FROM buses WHERE org_id=? AND bus_number=?", (oid, bus_no))
            if not b:
                errors.append({"line": n, "error": f"unknown bus {bus_no}"})
                continue
            bus_id = b["id"]
        temp = secrets.token_urlsafe(8)
        try:
            db.q("INSERT INTO users(org_id,login_id,login_key,name,password_hash,role,bus_id) VALUES(?,?,?,?,?,'rider',?)",
                 (oid, lid, login_key(lid), name, generate_password_hash(temp), bus_id))
        except INTEGRITY:
            errors.append({"line": n, "error": f"{lid} is already registered"})
            continue
        created.append({"login_id": lid, "temp_password": temp})
    return jsonify({"created": created, "errors": errors})


@app.post("/api/admin/announcements")
@admin_only
def post_announcement():
    d = request.get_json(silent=True) or {}
    body, bus = (d.get("body") or "").strip(), (d.get("bus") or "").strip() or None
    if not 3 <= len(body) <= 280:
        return reply("BAD_REQUEST", 400, "Write a notice of 3-280 characters")
    db, oid = get_db(), g.user["org_id"]
    if bus and not db.one("SELECT 1 AS x FROM buses WHERE org_id=? AND bus_number=?", (oid, bus)):
        return reply("UNKNOWN_BUS", 400, "This bus is not registered")
    db.q("INSERT INTO announcements(org_id,bus_number,body,created_at) VALUES(?,?,?,?)", (oid, bus, body, now().isoformat(timespec="seconds")))
    return reply("OK", 201, "Notice posted")


@app.post("/api/admin/announcements/delete")
@admin_only
def delete_announcement():
    get_db().q("DELETE FROM announcements WHERE id=? AND org_id=?", ((request.get_json(silent=True) or {}).get("id"), g.user["org_id"]))
    return reply("OK", 200, "Notice removed")


@app.get("/api/admin/analytics")
@admin_only
def analytics():
    """Aggregated only: no names or IDs."""
    rows = get_db().all("""SELECT b.bus_number, b.capacity, COUNT(x.id) AS total_boardings,
        COUNT(DISTINCT x.trip) AS trips FROM buses b LEFT JOIN boardings x ON x.bus_id=b.id
        WHERE b.org_id=? GROUP BY b.id, b.bus_number, b.capacity""", (g.user["org_id"],))
    rows.sort(key=lambda r: natural_key(r["bus_number"]))
    out = []
    for r in rows:
        avg = round(r["total_boardings"] / r["trips"], 1) if r["trips"] else 0
        out.append({"bus_number": r["bus_number"], "capacity": r["capacity"], "trips": r["trips"],
                    "total_boardings": r["total_boardings"], "avg_per_trip": avg,
                    "avg_fill_percent": round(100 * avg / r["capacity"], 1) if r["capacity"] else 0})
    return jsonify({"buses": out})


@app.get("/api/admin/daily")
@admin_only
def daily():
    rows = get_db().all("""SELECT substr(x.trip,1,10) AS day, COUNT(*) AS boardings
        FROM boardings x JOIN buses b ON b.id=x.bus_id WHERE b.org_id=?
        GROUP BY substr(x.trip,1,10) ORDER BY day DESC LIMIT 14""", (g.user["org_id"],))
    return jsonify(list(reversed(rows)))


@app.get("/api/admin/summary")
@admin_only
def summary():
    db, oid, trip = get_db(), g.user["org_id"], current_trip()
    riders = db.one("SELECT COUNT(*) AS c FROM users WHERE org_id=? AND role='rider'", (oid,))["c"]
    assigned = db.one("SELECT COUNT(*) AS c FROM users WHERE org_id=? AND role='rider' AND bus_id IS NOT NULL", (oid,))["c"]
    carded = db.one("SELECT COUNT(*) AS c FROM users WHERE org_id=? AND role='rider' AND uid_hash IS NOT NULL", (oid,))["c"]
    boarded = db.one("SELECT COUNT(*) AS c FROM boardings x JOIN buses b ON b.id=x.bus_id WHERE b.org_id=? AND x.trip=?", (oid, trip))["c"]
    buses = db.one("SELECT COUNT(*) AS c, COALESCE(SUM(capacity),0) AS seats FROM buses WHERE org_id=?", (oid,))
    return jsonify({"trip": trip, "riders": riders, "assigned": assigned, "cards": carded, "boarded": boarded,
                    "buses": buses["c"], "seats": buses["seats"]})


@app.post("/api/demo/simulate")
@admin_only
def simulate():
    """Demo helper: boards a few random riders as if they tapped their cards."""
    if not DEMO:
        return reply("FORBIDDEN", 403, "Demo mode is off")
    try:
        count = max(1, min(40, int((request.get_json(silent=True) or {}).get("count") or 8)))
    except (TypeError, ValueError):
        count = 8
    db, org = get_db(), user_org()
    trip = current_trip()
    riders = db.all("""SELECT u.* FROM users u WHERE u.org_id=? AND u.role='rider' AND (u.bus_id IS NOT NULL OR ?)
        AND u.id NOT IN (SELECT user_id FROM boardings WHERE trip=?)""", (org["id"], org["org_type"] == "government", trip))
    random.shuffle(riders)
    buses = [b["bus_number"] for b in db.all("SELECT bus_number FROM buses WHERE org_id=?", (org["id"],))]
    done = 0
    for r in riders:
        if done >= count:
            break
        num = random.choice(buses) if org["org_type"] == "government" else db.one("SELECT bus_number FROM buses WHERE id=?", (r["bus_id"],))["bus_number"]
        if board_core(db, org, r, num)[0] == "OK":
            done += 1
    return reply("OK", 200, f"{done} riders tapped in" if done else "Nobody left to board on this trip", boarded=done)


# ------------------------------------------------------------------ first-run setup (no CLI needed)
def setup_allowed():
    token = os.environ.get("SETUP_TOKEN", "")
    return bool(token) and secrets.compare_digest(request.headers.get("X-Setup-Token", ""), token)


@app.post("/api/setup")
def setup():
    """Creates an organisation and its first admin. Needs the SETUP_TOKEN env var (header X-Setup-Token)."""
    if not setup_allowed():
        return reply("FORBIDDEN", 403, "Setup is disabled or the token is wrong")
    d = request.get_json(silent=True) or {}
    name, typ = (d.get("name") or "").strip(), d.get("org_type")
    lid, pw = (d.get("admin_id") or "").strip(), d.get("admin_password") or ""
    if not name or typ not in ORG_TYPES or len(lid) < 3 or len(pw) < 8:
        return reply("BAD_REQUEST", 400, "name, org_type, admin_id (3+) and admin_password (8+) are required")
    db = get_db()
    try:
        org = new_org(db, name, typ, code=(d.get("code") or "").strip().upper() or None)
        db.q("INSERT INTO users(org_id,login_id,login_key,name,password_hash,role) VALUES(?,?,?,?,?,'admin')",
             (org["id"], lid, login_key(lid), "Administrator", generate_password_hash(pw)))
    except INTEGRITY:
        return reply("EXISTS", 409, "That organisation code or admin ID already exists")
    return reply("OK", 201, "Organisation and admin created. Save the device key now.", device_key=org["device_key"], org_code=org["code"])


@app.post("/api/setup/demo")
def setup_demo():
    """Loads (or reloads) the prototype example data. Needs SETUP_TOKEN. Send {"reset": true} to rebuild it."""
    if not setup_allowed():
        return reply("FORBIDDEN", 403, "Setup is disabled or the token is wrong")
    import demo_data
    return jsonify(demo_data.seed(get_db(), reset=bool((request.get_json(silent=True) or {}).get("reset"))))


# ------------------------------------------------------------------ errors
@app.errorhandler(Exception)
def on_error(e):
    if isinstance(e, HTTPException):
        return reply("HTTP_ERROR", e.code, e.name)
    app.logger.exception("unhandled error")
    return reply("SERVER_ERROR", 500, "Something went wrong")


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
