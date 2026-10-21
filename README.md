# Segro Bus — One student. One bus. One seat.

Seat-aware boarding for college and organisation buses. Riders sign in once, choose their college or organisation, pick their bus, and tap an ID card on an ESP32 + RFID scanner. The bus is never over capacity, and administrators see real demand per route.

**Prototype.** All people, rides and organisations except the route list are invented sample data. The government option is a sandbox, not an integration with any transport agency.

## Demo accounts (password for all: `Segro@2026`)
| Role | ID | Sign in on |
|---|---|---|
| Administrator (Sathyabama demo) | `admin` | "Admin sign-in" link on the login page |
| Door staff | `staff` | Admin sign-in |
| Student on route 9 | `student` | Main sign-in |
| Private organisation admin | `acme.admin` | Admin sign-in |
| Public-transport sandbox admin | `depot.admin` | Admin sign-in |

The login page also has one-click buttons for admin, staff and student while demo mode is on. Create a brand new account to show the "choose your college or organisation" flow; the private demo organisation asks for the join code `ACME-2026`.

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py demo          # loads the example data (python manage.py demo --reset to refresh "today")
python app.py                  # http://localhost:5000
python tests/test_app.py       # 74 checks
```

## What the demo contains
- **Sathyabama (demo):** the 78 real bus routes from the Transport Department list of 03/09/2026, about 420 invented students spread across routes (a few routes near capacity, most light), two weeks of weekday AM/PM ride history, today's boardings so far, three notices, and 70% of riders with a card already linked.
- **Acme Tech Park Shuttles (demo):** 3 shuttles, 70 riders, join code required.
- **City Bus Depot (sample data):** open ridership, 5 buses, 90 pass holders.
- Seat counts are not in the route list, so each route is set to 50 seats (editable in Admin → Buses).
- Driver names and phones are in `data/routes_full.private.csv` (git-ignored). If that file is present the demo uses it, and only staff and admins ever see driver details. Never upload it to a public repo.

## Using the app
- **Everyone:** one sign-in (email or ID plus password). Admins and door staff can use the same form or the Admin sign-in link.
- **Rider:** choose or add an organisation, choose a bus, see seats left, a live seat map, notices and ride history.
- **Add your organisation:** a rider whose college or company is not listed can add it and becomes its administrator.
- **Door staff:** live bus cards, scanner status, latest taps, manual boarding.
- **Admin:** also Buses (import routes, seats), Riders (add, import, assign bus, link or replace a card, reset password), Notices (to everyone or one route), Insights (daily boardings, how full each bus runs) and Setup (organisation name, join code, scanner key, wiring and build guide). In demo mode, "Simulate 8 taps" boards random riders for the live view.

## Deploy: GitHub → Vercel → Neon
1. Push this folder's contents to GitHub (`app.py` at the repo root). Do **not** upload `data/routes_full.private.csv`.
2. vercel.com → Add New → Project → import the repo; Vercel detects Flask from `app.py`.
3. Project → **Storage → Neon (Postgres)** → connect. This adds `DATABASE_URL`.
4. **Settings → Environment Variables** (Production):
   - `SEGRO_SECRET_KEY` = long random string, `SEGRO_UID_SALT` = another (never change it later)
   - `SEGRO_DEMO` = `1` (shows demo accounts on the login page; remove after the hackathon)
   - `SETUP_TOKEN` = a password you choose
5. Redeploy, then check `https://<project>.vercel.app/api/health` says `"database":"postgres"`.
6. Load the example data (takes a few seconds):
```bash
curl -X POST https://<project>.vercel.app/api/setup/demo -H "X-Setup-Token: <SETUP_TOKEN>" -H "Content-Type: application/json" -d '{}'
```
   Run it again with `-d '{"reset": true}'` shortly before presenting to refresh today's boardings. Then remove `SETUP_TOKEN` (and `SEGRO_DEMO` when done) and redeploy.
7. To create a real organisation instead: `POST /api/setup` with `X-Setup-Token` and `{"name","org_type","admin_id","admin_password"}`; the reply has the scanner key.

## Build the scanner
Parts: ESP32 DevKit, MFRC522, green and red LEDs with 220 Ω resistors, active buzzer, jumper wires, 13.56 MHz MIFARE/NFC cards (125 kHz EM4100 cards will not work).

| MFRC522 | ESP32 |
|---|---|
| 3.3V | 3V3 (**not 5V**) |
| GND | GND |
| SDA/SS | GPIO 5 |
| RST | GPIO 22 |
| SCK | GPIO 18 |
| MISO | GPIO 19 |
| MOSI | GPIO 23 |

Green LED: GPIO 25 → 220 Ω → LED long leg, short leg → GND. Red LED: GPIO 26 likewise. Buzzer + → GPIO 27, − → GND.

1. Arduino IDE: ESP32 board support; libraries **MFRC522** and **ArduinoJson** v7.
2. Upload `firmware/step1_read_uid`; the Serial Monitor (115200) should show `0x91` or `0x92` and print UIDs.
3. In `firmware/segro_node/` copy `secrets.h.example` to `secrets.h`; set Wi-Fi, your Vercel hostname, the scanner key (Admin → Setup) and the bus number (the route number, e.g. `9`).
4. Upload `segro_node`. On start-up one green flash and a beep means it reached the site.
5. Admin → Riders → **Link card** for a rider (try `student`), then tap your card on the scanner: two beeps means linked. Tap again to board; the seat map and live view update within seconds.

Signals: OK green+1 beep · ENROLLED green+2 · INVALID/UNKNOWN_BUS red+1 · WRONG_BUS/DUPLICATE red+2 · FULL red+3 · network or key problem long red.

## Boarding rules
Valid rider of this organisation → bus exists → rider's chosen bus (skipped for public-transport sandbox) → not already boarded this trip → seat free → recorded. Trip = local date + AM/PM (or `SHIFT`). The last seat is protected by a row lock (Postgres) or `BEGIN IMMEDIATE` (SQLite). Each organisation has its own scanner key; cards never work across organisations. Card UIDs are stored only as salted SHA-256 hashes. Insights are aggregated, with no names.

## Limits to state honestly
- Firmware uses `setInsecure()` (encrypted, server certificate not verified). Card UIDs can be cloned; production needs secured cards or a second factor.
- Joining an organisation is open unless the admin sets a join code. A real rollout should verify students against the college's own roster.
- Login lockout is per account; add IP rate limiting for production. Live views poll every few seconds.
- Not yet tested: a real Vercel deployment, real hardware, your cards. Verified on SQLite only; the Postgres code is written but was not run.
