"""Run: python tests/test_app.py   (uses a throwaway SQLite file)"""
import os, sys, tempfile
os.environ.pop("DATABASE_URL", None); os.environ.pop("POSTGRES_URL", None)
os.environ["SEGRO_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["SEGRO_DEMO"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app as A, demo_data

n = 0
def check(name, cond):
    global n; n += 1
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: sys.exit(1)

A.init_db()
db = A.DB(); res = demo_data.seed(db); db.close()
check("demo seeds three organisations", len(res["organisations"]) == 3)
db = A.DB(); check("demo seed is idempotent", "already" in demo_data.seed(db)["message"]); db.close()

def client(): return A.app.test_client()
def login(c, lid, pw="Segro@2026", portal="member"): return c.post("/api/login", json={"login_id": lid, "password": pw, "portal": portal})
def api(c, path, body=None): return c.post(path, json=body or {}) if body is not None else c.get(path)

# ---- one sign-in for everyone
c = client()
check("demo info lists accounts", len(c.get("/api/demo").get_json()["accounts"]) == 3)
check("student signs in on main sign-in", login(c, "student").status_code == 200)
me = c.get("/api/me").get_json()
check("student has org, route 9 and history", me["org"]["code"] == "SATHYA" and me["bus"]["bus_number"] == "9" and me["rides_total"] > 5)
check("history", len(c.get("/api/me/history").get_json()) > 5)
check("student cannot use admin APIs", c.get("/api/admin/riders").status_code == 403)
check("student sees announcements", len(c.get("/api/announcements").get_json()) >= 2)
a = client()
check("rider blocked at admin sign-in", login(client(), "student", portal="admin").status_code == 403)
check("admin signs in via admin portal", login(a, "admin", portal="admin").status_code == 200)
check("admin also fine on main sign-in", login(client(), "ADMIN").status_code == 200)  # case-insensitive ID
check("wrong password", login(client(), "student", "nope").status_code == 401)
check("admin summary", a.get("/api/admin/summary").get_json()["riders"] > 400)

# ---- new rider: sign up, pick organisation, pick bus
n1 = client()
r = n1.post("/api/register", json={"name": "New Rider", "login_id": "new.rider@example.com", "password": "password123"})
check("register signs in", r.status_code == 201 and n1.get("/api/me").status_code == 200)
check("duplicate signup", client().post("/api/register", json={"name": "X Y", "login_id": "NEW.rider@example.com", "password": "password123"}).status_code == 409)
check("short password", client().post("/api/register", json={"name": "X Y", "login_id": "abc@x.com", "password": "short"}).status_code == 400)
check("new rider has no organisation", n1.get("/api/me").get_json()["org"] is None)
check("org-only endpoint blocked without org", n1.get("/api/org/buses").status_code == 409)
orgs = n1.get("/api/orgs").get_json()
check("org search lists demo orgs, hides join codes", len(orgs) == 3 and "join_code" not in str(orgs) and any(o["needs_code"] for o in orgs))
check("org search filters", [o["code"] for o in n1.get("/api/orgs?q=sathya").get_json()] == ["SATHYA"])
check("join needs code", n1.post("/api/me/org/join", json={"org_code": "ACMEPARK"}).status_code == 403)
check("wrong join code", n1.post("/api/me/org/join", json={"org_code": "ACMEPARK", "join_code": "nope"}).status_code == 403)
check("join with code", n1.post("/api/me/org/join", json={"org_code": "ACMEPARK", "join_code": "acme-2026"}).status_code == 200)
check("cannot join twice", n1.post("/api/me/org/join", json={"org_code": "SATHYA"}).status_code == 409)
check("leave", n1.post("/api/me/org/leave").status_code == 200)
check("join open org", n1.post("/api/me/org/join", json={"org_code": "SATHYA"}).status_code == 200)
buses = n1.get("/api/org/buses").get_json()
check("bus list sorted naturally", [b["bus_number"] for b in buses[:4]] == ["1", "1A", "1E", "2"] and len(buses) == 78)
check("choose bus", n1.post("/api/me/bus", json={"bus": "9C"}).status_code == 200 and n1.get("/api/me").get_json()["bus"]["bus_number"] == "9C")
check("unknown bus", n1.post("/api/me/bus", json={"bus": "ZZ"}).status_code == 404)

# ---- adding an organisation
n2 = client(); n2.post("/api/register", json={"name": "Owner One", "login_id": "owner@x.com", "password": "password123"})
check("create needs valid type", n2.post("/api/me/org/create", json={"name": "Valid Name", "org_type": "zoo"}).status_code == 400)
check("duplicate org name rejected", n2.post("/api/me/org/create", json={"name": "acme tech park shuttles (demo)", "org_type": "private"}).status_code == 409)
r = n2.post("/api/me/org/create", json={"name": "Greenfield Engineering College", "org_type": "college"})
check("create organisation", r.status_code == 201)
me2 = n2.get("/api/me").get_json()
check("creator becomes admin of it", me2["role"] == "admin" and me2["org"]["name"] == "Greenfield Engineering College")
check("new org starts empty", n2.get("/api/dashboard").get_json()["buses"] == [])
check("new admin adds bus", n2.post("/api/admin/buses", json={"bus_number": "G1", "capacity": 3}).status_code == 201)
check("new org visible to others", len(client().get("/api/orgs?q=greenfield").get_json()) == 1)
check("join code setting", n2.post("/api/admin/org", json={"join_code": "GREEN-1"}).status_code == 200)
check("bad join code format", n2.post("/api/admin/org", json={"join_code": "x"}).status_code == 400)
check("isolation: new admin sees none of Sathyabama", "SIST26001" not in str(n2.get("/api/admin/riders").get_json()))

# ---- scanner and boarding rules
dv = lambda code: A.DB().one("SELECT device_key FROM organisations WHERE code=?", (code,))["device_key"]
K = {c_: dv(c_) for c_ in ("SATHYA", "ACMEPARK", "CITYDEPOT")}
H = lambda o: {"X-Device-Key": K[o]}
scan = lambda o, uid, bus, **k: client().post("/api/scan", json={"uid": uid, "bus": bus, **k}, headers=H(o)).get_json()["result"]
check("ping", client().get("/api/ping", headers=H("SATHYA")).status_code == 200)
check("ping bad key", client().get("/api/ping", headers={"X-Device-Key": "x"}).status_code == 401)
check("link card via enrol", a.post("/api/admin/riders/enrol", json={"login_id": "student"}).status_code == 200 and scan("SATHYA", "CARD0001", "9") == "ENROLLED")
board = client()
check("boarding ok", scan("SATHYA", "CARD0001", "9") == "OK" or scan("SATHYA", "CARD0001", "9") == "DUPLICATE")
check("duplicate", scan("SATHYA", "CARD0001", "9") == "DUPLICATE")
check("wrong bus", scan("SATHYA", "CARD0001", "1") == "WRONG_BUS")
check("unknown card", scan("SATHYA", "NOPE", "9") == "INVALID_ID")
check("card from another org rejected", scan("ACMEPARK", "CARD0001", "A1") == "INVALID_ID")
check("gov open ridership", scan("CITYDEPOT", "NOPE", "D1") == "INVALID_ID")
st = client(); login(st, "staff", portal="admin")
check("staff sees live data", len(st.get("/api/dashboard").get_json()["buses"]) == 78 and st.get("/api/feed").status_code == 200)
check("staff cannot manage", st.get("/api/admin/riders").status_code == 403 and st.post("/api/admin/buses", json={"bus_number": "x", "capacity": 4}).status_code == 403)
check("manual board", st.post("/api/staff/board", json={"login_id": "SIST26001", "bus": "x"}).get_json()["result"] in ("UNKNOWN_BUS", "WRONG_BUS", "OK", "DUPLICATE"))

# last seat / full
db = A.DB(); org = db.one("SELECT * FROM organisations WHERE code='SATHYA'")
db.q("INSERT INTO buses(org_id,bus_number,capacity) VALUES(?,?,?)", (org["id"], "T1", 1)); db.close()
check("rider chooses a 1-seat bus", n1.post("/api/me/bus", json={"bus": "T1"}).status_code == 200)
rid = A.DB().one("SELECT id FROM users WHERE login_key='new.rider@example.com'")["id"]
db = A.DB(); db.q("UPDATE users SET uid_hash=? WHERE id=?", (A.hash_uid("NEWRIDERCARD"), rid))
db.q("INSERT INTO users(org_id,login_id,login_key,name,password_hash,role,bus_id,uid_hash) SELECT org_id,'second','second','Second',password_hash,'rider',bus_id,? FROM users WHERE id=?", (A.hash_uid("SECONDCARD"), rid)); db.close()
check("first on last seat", scan("SATHYA", "NEWRIDERCARD", "T1") == "OK")
check("bus full", scan("SATHYA", "SECONDCARD", "T1") == "FULL")
check("rider sees full bus", n1.get("/api/me").get_json()["bus"]["seats_left"] == 0)

# ---- admin tools
n1.post("/api/me/bus", json={"bus": "9C"})
check("announce", a.post("/api/admin/announcements", json={"body": "Route 9C delayed", "bus": "9C"}).status_code == 201)
check("announce unknown bus", a.post("/api/admin/announcements", json={"body": "Hello there", "bus": "ZZ"}).status_code == 400)
check("rider on 9C sees it", any("9C" in x["body"] for x in n1.get("/api/announcements").get_json()))
check("rider on other route does not", not any("9C" in x["body"] for x in c.get("/api/announcements").get_json()))
check("simulate taps", a.post("/api/demo/simulate", json={"count": 5}).get_json()["boarded"] == 5)
check("simulate forbidden for rider", c.post("/api/demo/simulate", json={}).status_code == 403)
r = a.post("/api/admin/riders/import", json={"csv": "login_id,name,bus\nimp001,Imported One,1\nimp002,Bad Bus,999\nstudent,Dup,1\n"}).get_json()
check("rider import", len(r["created"]) == 1 and len(r["errors"]) == 2)
check("reset password", a.post("/api/admin/riders/reset", json={"login_id": "imp001"}).get_json()["temp_password"])
check("analytics aggregated", "login_id" not in str(a.get("/api/admin/analytics").get_json()) and len(a.get("/api/admin/daily").get_json()) >= 8)
check("bus import", a.post("/api/admin/buses/import", json={"csv": "route_no,capacity\n9C,50\n"}).get_json()["updated"] == 1)
check("capacity-all", a.post("/api/admin/buses/capacity-all", json={"capacity": 50}).status_code == 200)
check("rotate key", a.post("/api/admin/device/rotate", json={}).status_code == 200)
check("settings", a.get("/api/admin/org").get_json()["code"] == "SATHYA")

# ---- security
lk = client()
for _ in range(5): login(lk, "second", "wrong")
check("lockout after 5 failures", login(lk, "second", "Segro@2026").status_code == 429)
check("anonymous blocked", client().get("/api/me").status_code == 401)
check("setup disabled without token", client().post("/api/setup", json={}).status_code == 403)
os.environ["SETUP_TOKEN"] = "tok"
r = client().post("/api/setup", json={"name": "Setup Org", "org_type": "private", "admin_id": "boss1", "admin_password": "bosspass12"}, headers={"X-Setup-Token": "tok"})
check("setup creates org and admin", r.status_code == 201 and login(client(), "boss1", "bosspass12", "admin").status_code == 200)
check("setup/demo reset", client().post("/api/setup/demo", json={"reset": True}, headers={"X-Setup-Token": "tok"}).get_json()["ok"])
check("home page served", client().get("/").status_code == 200)
print(f"\nALL {n} CHECKS PASSED")
