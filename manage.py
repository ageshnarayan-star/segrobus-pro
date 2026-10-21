"""Admin CLI for Segro Bus. Works on SQLite locally, or on Neon/Postgres when DATABASE_URL is set.

  python manage.py demo                   # load the prototype example data (organisations, buses, riders, history)
  python manage.py demo --reset           # wipe and rebuild the example data (refreshes "today")
  python manage.py init
  python manage.py add-org "My College" college            # prints the scanner key; types: college | private | government
  python manage.py show-key SATHYA                         # scanner key for an organisation code
  python manage.py add-user SATHYA transport "Transport Office" Strong#Pass1 admin     # roles: rider | staff | admin
  python manage.py add-bus SATHYA 12 50
  python manage.py import-routes SATHYA data/routes_full.private.csv
  python manage.py card SATHYA SIST26001 04A1B2C3          # link a card UID to a rider
"""
import sys

from werkzeug.security import generate_password_hash

import app as A


def org_row(db, code):
    r = db.one("SELECT * FROM organisations WHERE code=?", (code.upper(),))
    if not r:
        sys.exit(f"no such organisation: {code}")
    return r


def main(a):
    if not a:
        print(__doc__)
        return
    A.init_db()
    db = A.DB()
    c = a[0]
    try:
        if c == "init":
            print("database ready:", "postgres" if A.IS_PG else A.SQLITE_PATH)
        elif c == "demo":
            import demo_data
            r = demo_data.seed(db, reset="--reset" in a)
            print(r.get("message") or "")
            for o in r.get("organisations", []):
                print(f"  {o['name']}: {o['riders']} riders, {o['boardings']} boardings" + (f", join code {o['join_code']}" if o.get("join_code") else ""))
            if r.get("logins"):
                print("\nSign in (password for all demo accounts:", demo_data.PASSWORD + ")")
                print("  Admin:    admin     (use 'Admin sign-in' on the login page)")
                print("  Staff:    staff     (Admin sign-in)")
                print("  Student:  student   (main sign-in)")
        elif c == "add-org":
            org = A.new_org(db, a[1], a[2])
            print("organisation code:", org["code"], "\nscanner device key (put it in the ESP32 sketch):", org["device_key"])
        elif c == "show-key":
            print(org_row(db, a[1])["device_key"])
        elif c == "add-user":
            o = org_row(db, a[1])
            role = a[5] if len(a) > 5 else "rider"
            if role not in ("rider", "staff", "admin"):
                sys.exit("role must be rider, staff or admin")
            key = A.login_key(a[2])
            if db.one("SELECT 1 AS x FROM users WHERE login_key=?", (key,)):
                db.q("UPDATE users SET name=?, password_hash=?, role=?, org_id=? WHERE login_key=?", (a[3], generate_password_hash(a[4]), role, o["id"], key))
            else:
                db.q("INSERT INTO users(org_id,login_id,login_key,name,password_hash,role) VALUES(?,?,?,?,?,?)", (o["id"], a[2], key, a[3], generate_password_hash(a[4]), role))
        elif c == "add-bus":
            o = org_row(db, a[1])
            A.upsert_buses(db, o["id"], f"route_no,capacity\n{a[2]},{int(a[3])}\n")
        elif c == "import-routes":
            o = org_row(db, a[1])
            with open(a[2], newline="", encoding="utf-8") as f:
                added, updated, errors = A.upsert_buses(db, o["id"], f.read())
            print(f"{added} buses added, {updated} updated")
            for e in errors:
                print("line", e["line"], e["error"])
        elif c == "card":
            o = org_row(db, a[1])
            r = db.q("UPDATE users SET uid_hash=? WHERE org_id=? AND login_key=?", (A.hash_uid(a[3]), o["id"], A.login_key(a[2])))
            if not r.rowcount:
                sys.exit("no such rider")
        else:
            print(__doc__)
            return
    except IndexError:
        sys.exit("missing arguments\n" + __doc__)
    db.close()
    print("ok")


if __name__ == "__main__":
    main(sys.argv[1:])
