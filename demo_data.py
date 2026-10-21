"""Prototype example data. Everything here is invented for demonstration: names, IDs and ride history.
The bus route list for the college is the real Transport Department list (data/routes_*.csv).
Run: python manage.py demo      (or POST /api/setup/demo with SETUP_TOKEN on a deployed site)
"""
import os
import random
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

import app as A

DEMO_CODES = ("SATHYA", "ACMEPARK", "CITYDEPOT")
PASSWORD = A.DEMO_PASSWORD
FIRST = ["Aarav", "Aditya", "Akash", "Ananya", "Anjali", "Arjun", "Bharath", "Deepak", "Divya", "Gokul", "Harini", "Ishaan",
         "Janani", "Karthik", "Kavya", "Keerthana", "Lakshmi", "Mahesh", "Meena", "Naveen", "Nithya", "Pranav", "Priya",
         "Rahul", "Ramya", "Rohit", "Sanjay", "Shreya", "Sneha", "Surya", "Swathi", "Tarun", "Varun", "Vignesh", "Vishnu", "Yamini"]
LAST = ["Anand", "Balaji", "Chandran", "Dinesh", "Ganesh", "Hari", "Iyer", "Jayaraman", "Krishnan", "Kumar", "Murali", "Nair",
        "Pillai", "Raman", "Rao", "Sekar", "Selvam", "Subramani", "Sundar", "Venkat"]
POPULAR = {"9": 46, "9A": 40, "8": 36, "10": 22, "5": 22, "3": 20, "2F": 20, "36": 20, "35": 20, "30": 18, "8C": 18, "10D": 18, "5A": 16}


def _routes_csv():
    d = os.path.join(A.BASE, "data")
    for name in ("routes_full.private.csv", "routes_public.csv"):  # driver details only if the private file is present
        p = os.path.join(d, name)
        if os.path.exists(p):
            return open(p, newline="", encoding="utf-8").read()
    return "route_no,vehicle_no,route,capacity\n9,Z4110,Besant Nagar-(via)-Vannathurai-Thiruvanmiyur,50\n"


def wipe(db):
    for code in DEMO_CODES:
        o = db.one("SELECT id FROM organisations WHERE code=?", (code,))
        if not o:
            continue
        oid = o["id"]
        db.q("DELETE FROM boardings WHERE bus_id IN (SELECT id FROM buses WHERE org_id=?)", (oid,))
        db.q("DELETE FROM enrol_requests WHERE org_id=?", (oid,))
        db.q("UPDATE users SET bus_id=NULL WHERE org_id=?", (oid,))
        db.q("DELETE FROM users WHERE org_id=?", (oid,))
        db.q("DELETE FROM announcements WHERE org_id=?", (oid,))
        db.q("DELETE FROM buses WHERE org_id=?", (oid,))
        db.q("DELETE FROM organisations WHERE id=?", (oid,))


def _make_org(db, code, name, typ, join_code=None):
    A.new_org(db, name, typ, join_code=join_code, code=code)
    return db.one("SELECT * FROM organisations WHERE code=?", (code,))


def _people(db, org, rng, n, prefix, pw_hash, weights, caps):
    """Creates n riders, assigns buses within capacity, returns [(user_id, bus_id_or_None)]."""
    buses = db.all("SELECT id, bus_number, capacity FROM buses WHERE org_id=?", (org["id"],))
    left = {b["id"]: caps.get(b["bus_number"], b["capacity"]) for b in buses}
    w = [(weights or {}).get(b["bus_number"], 1) for b in buses]
    rows, assign = [], {}
    names = set()
    for i in range(n):
        while True:
            nm = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if nm not in names or len(names) > 600:
                names.add(nm)
                break
        lid = f"{prefix}{i + 1:03d}"
        bus = None
        if weights is not None:
            for _ in range(20):
                b = rng.choices(buses, w)[0]
                if left[b["id"]] > 0:
                    bus = b["id"]
                    left[bus] -= 1
                    break
        card = A.hash_uid(f"DEMO-{lid}") if rng.random() < 0.7 else None  # most demo riders already have a card linked
        rows.append((org["id"], lid, A.login_key(lid), nm, pw_hash, "rider", bus, card))
        assign[A.login_key(lid)] = bus
    A.bulk_insert(db, "users", ["org_id", "login_id", "login_key", "name", "password_hash", "role", "bus_id", "uid_hash"], rows)
    ids = {r["login_key"]: r["id"] for r in db.all("SELECT id, login_key FROM users WHERE org_id=?", (org["id"],))}
    return [(ids[k], b) for k, b in assign.items()]


def _history(db, riders, rng, pw=0.8, days=14, gov_buses=None, today=True):
    """Weekday AM/PM boardings for the past days, plus today's trips so far."""
    n = A.now()
    rows = []

    def add(uid, bus, day, label, hour, minute):
        t = day.replace(hour=hour, minute=minute, second=rng.randrange(60), microsecond=0)
        if t > n:
            t = n - timedelta(minutes=rng.randrange(1, 40))
        rows.append((uid, bus, f"{day:%Y-%m-%d}-{label}", t.isoformat(timespec="seconds")))

    for back in range(0 if today else 1, days + 1):
        day = n - timedelta(days=back)
        if day.weekday() >= 5:
            continue
        for label, h0 in (("AM", 7), ("PM", 16)):
            if back == 0 and ((label == "AM" and n.hour < 7) or (label == "PM" and n.hour < 16)):
                continue
            for uid, bus in riders:
                if rng.random() < (pw if back else pw - 0.1):
                    b = rng.choice(gov_buses) if gov_buses else bus
                    if b:
                        add(uid, b, day, label, h0, rng.randrange(5, 55))
    A.bulk_insert(db, "boardings", ["user_id", "bus_id", "trip", "boarded_at"], rows, chunk=200)
    return len(rows)


def seed(db, reset=False):
    A.ensure_schema(db)
    if db.one("SELECT 1 AS x FROM organisations WHERE code='SATHYA'"):
        if not reset:
            return {"ok": True, "message": "Demo data already loaded. Send {\"reset\": true} to rebuild it."}
        wipe(db)
    for lid in ("admin", "staff", "student", "acme.admin", "depot.admin"):  # free the demo login IDs
        db.q("DELETE FROM users WHERE login_key=?", (lid,))
    rng = random.Random(2026)
    pw = generate_password_hash(PASSWORD)  # hashed once and reused: hashing 400 passwords would take far too long
    out = {"ok": True, "organisations": [], "logins": {}}

    # 1. College: the real route list
    col = _make_org(db, "SATHYA", "Sathyabama Institute of Science and Technology (demo)", "college")
    A.upsert_buses(db, col["id"], _routes_csv())
    for lid, nm, role in (("admin", "Transport Office", "admin"), ("staff", "Gate Staff", "staff")):
        db.q("INSERT INTO users(org_id,login_id,login_key,name,password_hash,role) VALUES(?,?,?,?,?,?)", (col["id"], lid, lid, nm, pw, role))
    riders = _people(db, col, rng, 420, "SIST26", pw, POPULAR, {})
    bus9 = db.one("SELECT id FROM buses WHERE org_id=? AND bus_number='9'", (col["id"],))
    if bus9:
        db.q("INSERT INTO users(org_id,login_id,login_key,name,password_hash,role,bus_id) VALUES(?,?,?,?,?,'rider',?)",
             (col["id"], "student", "student", "Demo Student", pw, bus9["id"]))
        sid = db.one("SELECT id FROM users WHERE login_key='student'")["id"]
        riders.append((sid, bus9["id"]))
    boards = _history(db, riders, rng)
    for b, txt in ((None, "Freshers' Day buses run on the 3 September route list. Campus in time: 3.30 PM."),
                   ("9", "Route 9 is running about 10 minutes late this evening."),
                   ("1", "Route 1 will wait at Kandigai until 5.15 PM today.")):
        db.q("INSERT INTO announcements(org_id,bus_number,body,created_at) VALUES(?,?,?,?)",
             (col["id"], b, txt, A.now().isoformat(timespec="seconds")))
    out["organisations"].append({"code": col["code"], "name": col["name"], "riders": len(riders), "boardings": boards})

    # 2. Private organisation: needs a join code
    acme = _make_org(db, "ACMEPARK", "Acme Tech Park Shuttles (demo)", "private", join_code="ACME-2026")
    for num, cap, route in (("A1", 30, "Tech Park Gate-(via)-Main Road-Metro Station"), ("A2", 30, "Tech Park Gate-(via)-Lake View-Bus Terminus"),
                            ("A3", 40, "Tech Park Gate-(via)-Ring Road-Railway Station")):
        db.q("INSERT INTO buses(org_id,bus_number,capacity,route,vehicle_no) VALUES(?,?,?,?,?)", (acme["id"], num, cap, route, f"DEMO-{num}"))
    db.q("INSERT INTO users(org_id,login_id,login_key,name,password_hash,role) VALUES(?,?,?,?,?,'admin')", (acme["id"], "acme.admin", "acme.admin", "Facilities Desk", pw))
    ar = _people(db, acme, rng, 70, "ACME", pw, {"A1": 5, "A2": 4, "A3": 6}, {})
    ab = _history(db, ar, rng, pw=0.85, days=10)
    out["organisations"].append({"code": acme["code"], "name": acme["name"], "riders": len(ar), "boardings": ab, "join_code": "ACME-2026"})

    # 3. Public transport sandbox: open ridership
    gov = _make_org(db, "CITYDEPOT", "City Bus Depot (sample data)", "government")
    gbus = []
    for num, cap in (("D1", 55), ("D2", 55), ("D3", 45), ("D4", 45), ("D5", 60)):
        db.q("INSERT INTO buses(org_id,bus_number,capacity,route,vehicle_no) VALUES(?,?,?,?,?)", (gov["id"], num, cap, f"Sample route {num}-(via)-Central-Market", f"SAMPLE-{num}"))
    gbus = [r["id"] for r in db.all("SELECT id FROM buses WHERE org_id=?", (gov["id"],))]
    db.q("INSERT INTO users(org_id,login_id,login_key,name,password_hash,role) VALUES(?,?,?,?,?,'admin')", (gov["id"], "depot.admin", "depot.admin", "Depot Manager", pw))
    gr = _people(db, gov, rng, 90, "PASS", pw, None, {})
    gb = _history(db, gr, rng, pw=0.7, days=10, gov_buses=gbus)
    out["organisations"].append({"code": gov["code"], "name": gov["name"], "riders": len(gr), "boardings": gb})

    out["logins"] = {"admin (Sathyabama demo)": "admin", "staff": "staff", "student": "student", "private admin": "acme.admin",
                     "depot admin": "depot.admin", "password for all": PASSWORD}
    return out
