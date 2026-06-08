import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, date
from pathlib import Path

from flask import (Flask, abort, flash, g, make_response, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

PIN_RE = re.compile(r"^\d{4,6}$")

APP_DIR = Path(__file__).parent
DB_PATH = Path(os.environ.get("SUNSET_DB_PATH") or APP_DIR / "sunset_lounge.db")

LOGIN_LOCK_SECONDS = 120
LOGIN_MAX_FAILURES = 5
LOGIN_FAILURES = {}


def load_active_packages():
    return get_db().execute(
        "SELECT * FROM package_tiers WHERE active=1 ORDER BY display_order, id"
    ).fetchall()


def load_active_beds():
    return get_db().execute(
        "SELECT * FROM beds WHERE active=1 ORDER BY display_order, id"
    ).fetchall()


def bed_hours_used(bed_id, name=None, baseline=None, installed_at=None):
    """Total tube hours since the current set was installed.

    If bed metadata is already in hand, pass it in to avoid an extra query.
    Sessions are counted by FK; falls back to matching the historical text
    snapshot for any sessions created before the bed_id column existed.
    """
    db = get_db()
    if name is None or baseline is None or installed_at is None:
        row = db.execute(
            "SELECT name, tube_hours_baseline, tubes_installed_at FROM beds WHERE id=?",
            (bed_id,)).fetchone()
        if not row:
            return 0.0
        name = row["name"]
        baseline = row["tube_hours_baseline"]
        installed_at = row["tubes_installed_at"]
    used_minutes = db.execute(
        """SELECT COALESCE(SUM(minutes), 0) FROM sessions
           WHERE (bed_id = ? OR (bed_id IS NULL AND bed = ?))
             AND session_at >= ?""",
        (bed_id, name, installed_at),
    ).fetchone()[0]
    return float(baseline or 0) + (used_minutes or 0) / 60.0

app = Flask(__name__)
secret_key = os.environ.get("SUNSET_SECRET")
if not secret_key:
    if os.environ.get("FLASK_DEBUG") == "1":
        secret_key = "local-dev-only-change-me"
    else:
        raise RuntimeError("Set SUNSET_SECRET before starting Sunset Lounge.")
app.secret_key = secret_key
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SUNSET_COOKIE_SECURE") == "1",
)


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.before_request
def validate_csrf():
    if request.method != "POST":
        return None
    expected = session.get("csrf_token")
    submitted = request.form.get("csrf_token", "")
    if not expected or not secrets.compare_digest(expected, submitted):
        abort(400)
    return None


# ---------- DB helpers ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    with open(APP_DIR / "schema.sql") as f:
        db.executescript(f.read())
    db.commit()
    db.close()


def migrate_db():
    """Add columns / indexes that aren't in the original schema. Idempotent."""
    db = sqlite3.connect(DB_PATH)
    staff_cols = {r[1] for r in db.execute("PRAGMA table_info(staff)").fetchall()}
    if "active" not in staff_cols:
        db.execute("ALTER TABLE staff ADD COLUMN active INTEGER NOT NULL DEFAULT 1")

    session_cols = {r[1] for r in db.execute("PRAGMA table_info(sessions)").fetchall()}
    if "bed_id" not in session_cols:
        db.execute("ALTER TABLE sessions ADD COLUMN bed_id INTEGER REFERENCES beds(id)")

    # Backfill legacy sessions whose bed_id is null by matching the text name.
    db.execute("""
        UPDATE sessions
        SET bed_id = (SELECT id FROM beds WHERE name = sessions.bed)
        WHERE bed_id IS NULL
          AND EXISTS (SELECT 1 FROM beds WHERE name = sessions.bed)
    """)
    db.commit()
    db.close()


def ensure_db():
    """Run schema + migrations + seed every startup. All idempotent."""
    init_db()
    migrate_db()
    from seed import seed
    seed(DB_PATH)


ensure_db()


# ---------- Computed helpers ----------
def client_balance(client_id):
    db = get_db()
    bought = db.execute(
        "SELECT COALESCE(SUM(minutes),0) FROM packages WHERE client_id=?",
        (client_id,)).fetchone()[0]
    used = db.execute(
        "SELECT COALESCE(SUM(minutes),0) FROM sessions WHERE client_id=?",
        (client_id,)).fetchone()[0]
    return bought - used


def current_staff():
    sid = session.get("staff_id")
    if not sid:
        return None
    return get_db().execute(
        "SELECT id, name FROM staff WHERE id=? AND active=1", (sid,)).fetchone()


def require_staff():
    s = current_staff()
    if s:
        return s
    if request.headers.get("HX-Request"):
        r = make_response("", 401)
        r.headers["HX-Redirect"] = url_for("staff_login")
        return r
    return redirect(url_for("staff_login"))


def _login_key():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()


def _login_is_locked():
    record = LOGIN_FAILURES.get(_login_key())
    if not record:
        return False
    count, first_failed_at = record
    if time.monotonic() - first_failed_at > LOGIN_LOCK_SECONDS:
        LOGIN_FAILURES.pop(_login_key(), None)
        return False
    return count >= LOGIN_MAX_FAILURES


def _record_login_failure():
    key = _login_key()
    now = time.monotonic()
    count, first_failed_at = LOGIN_FAILURES.get(key, (0, now))
    if now - first_failed_at > LOGIN_LOCK_SECONDS:
        count, first_failed_at = 0, now
    LOGIN_FAILURES[key] = (count + 1, first_failed_at)


def _clear_login_failures():
    LOGIN_FAILURES.pop(_login_key(), None)


@app.context_processor
def inject_globals():
    return {
        "PACKAGES": load_active_packages(),
        "BEDS": load_active_beds(),
        "csrf_token": csrf_token,
        "now": datetime.now,
    }


# ---------- Routes ----------
# Public: customer-facing kiosk lives at /. Staff find their portal by typing
# /staff directly — there is no link from any customer-visible page.
@app.route("/")
def index():
    return render_template("kiosk/welcome.html")


@app.route("/kiosk/register", methods=["GET", "POST"])
def kiosk_register():
    if request.method == "POST":
        f = request.form
        first = f.get("first_name", "").strip()
        last = f.get("last_name", "").strip()
        phone = f.get("phone", "").strip()
        email = f.get("email", "").strip()
        birthdate = f.get("birthdate", "").strip() or None
        notes = f.get("notes", "").strip()
        consent = f.get("consent") == "on"

        errors = []
        if not first:
            errors.append("First name is required.")
        if not last:
            errors.append("Last name is required.")
        if not phone and not email:
            errors.append("Please share a phone number or email so we can reach you.")
        if not consent:
            errors.append("Please confirm the consent statement.")

        if errors:
            return render_template("kiosk/register.html", errors=errors, form=f)

        db = get_db()
        db.execute(
            "INSERT INTO clients (first_name, last_name, phone, email, birthdate, notes, consent_signed_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (first, last, phone, email, birthdate, notes, datetime.utcnow().isoformat()),
        )
        db.commit()
        return redirect(url_for("kiosk_thanks", name=first))

    return render_template("kiosk/register.html", errors=None, form={})


@app.route("/kiosk/thanks")
def kiosk_thanks():
    return render_template("kiosk/thanks.html", name=request.args.get("name", "friend"))


# Staff auth
@app.route("/staff/login", methods=["GET", "POST"])
def staff_login():
    error = None
    if request.method == "POST":
        if _login_is_locked():
            error = "Too many attempts. Wait two minutes and try again."
        else:
            pin = request.form.get("pin", "")
            if PIN_RE.match(pin):
                rows = get_db().execute(
                    "SELECT id, name, pin FROM staff WHERE active = 1").fetchall()
                match = next((r for r in rows if check_password_hash(r["pin"], pin)), None)
            else:
                match = None
            if match:
                session.clear()
                csrf_token()
                session["staff_id"] = match["id"]
                _clear_login_failures()
                return redirect(url_for("staff_dashboard"))
            _record_login_failure()
            error = "That PIN didn't match. Try again."
    return render_template("staff/login.html", error=error)


@app.route("/staff/logout")
def staff_logout():
    session.pop("staff_id", None)
    return redirect(url_for("index"))


# Staff: dashboard
@app.route("/staff")
def staff_dashboard():
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s

    db = get_db()
    today = date.today().isoformat()
    sessions_today = db.execute(
        "SELECT COUNT(*) FROM sessions WHERE substr(session_at,1,10)=?",
        (today,)).fetchone()[0]
    new_clients_week = db.execute(
        "SELECT COUNT(*) FROM clients WHERE date(created_at) >= date('now','-7 days')"
    ).fetchone()[0]
    recent_rows = db.execute(
        """SELECT c.id, c.first_name, c.last_name,
                  (SELECT MAX(session_at) FROM sessions WHERE client_id=c.id) AS last_visit
           FROM clients c
           ORDER BY COALESCE(
             (SELECT MAX(session_at) FROM sessions WHERE client_id=c.id),
             c.created_at) DESC
           LIMIT 6"""
    ).fetchall()
    recent = [{**dict(c), "balance": client_balance(c["id"])} for c in recent_rows]

    return render_template(
        "staff/dashboard.html",
        staff=s,
        sessions_today=sessions_today,
        new_clients_week=new_clients_week,
        recent=recent,
    )


# Staff: client list + HTMX search
def _query_clients(q=None, limit=200):
    """One query that returns clients with computed balance — kills N+1."""
    db = get_db()
    base = """
        SELECT c.id, c.first_name, c.last_name, c.phone,
          COALESCE((SELECT SUM(minutes) FROM packages WHERE client_id=c.id), 0)
          - COALESCE((SELECT SUM(minutes) FROM sessions WHERE client_id=c.id), 0)
          AS balance
        FROM clients c
    """
    if q:
        like = f"%{q}%"
        rows = db.execute(
            base + """ WHERE c.first_name LIKE ? OR c.last_name LIKE ?
                          OR c.phone LIKE ? OR (c.first_name||' '||c.last_name) LIKE ?
                       ORDER BY c.last_name, c.first_name LIMIT ?""",
            (like, like, like, like, limit),
        ).fetchall()
    else:
        rows = db.execute(
            base + " ORDER BY c.last_name, c.first_name LIMIT ?",
            (limit,),
        ).fetchall()
    return rows


@app.route("/staff/clients")
def staff_clients():
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    q = request.args.get("q", "").strip()
    clients = _query_clients(q=q or None)
    # HTMX live-filter swap returns just the list partial
    if request.headers.get("HX-Request"):
        return render_template("partials/_client_list.html", clients=clients, q=q)
    return render_template("staff/clients.html", staff=s, clients=clients, q=q)


@app.route("/staff/clients/search")
def staff_clients_search():
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    q = request.args.get("q", "").strip()
    rows = []
    if q:
        like = f"%{q}%"
        rows = get_db().execute(
            """SELECT id, first_name, last_name, phone FROM clients
               WHERE first_name LIKE ? OR last_name LIKE ? OR phone LIKE ?
                  OR (first_name||' '||last_name) LIKE ?
               ORDER BY last_name, first_name LIMIT 20""",
            (like, like, like, like),
        ).fetchall()
    results = [{**dict(r), "balance": client_balance(r["id"])} for r in rows]
    return render_template("partials/_search_results.html", results=results, q=q)


# Staff: client edit
@app.route("/staff/clients/<int:cid>/edit", methods=["POST"])
def staff_client_edit(cid):
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    db = get_db()
    if not db.execute("SELECT 1 FROM clients WHERE id=?", (cid,)).fetchone():
        abort(404)

    f = request.form
    first = f.get("first_name", "").strip()
    last = f.get("last_name", "").strip()
    phone = f.get("phone", "").strip()
    email = f.get("email", "").strip()
    birthdate = f.get("birthdate", "").strip() or None
    notes = f.get("notes", "").strip() or None

    if not first:
        flash("First name is required.", "error")
    elif not last:
        flash("Last name is required.", "error")
    elif not phone and not email:
        flash("Keep at least a phone number or email on file.", "error")
    else:
        db.execute(
            "UPDATE clients SET first_name=?, last_name=?, phone=?, email=?, "
            "birthdate=?, notes=? WHERE id=?",
            (first, last, phone, email, birthdate, notes, cid),
        )
        db.commit()
        flash(f"{first}'s details updated.", "ok")
    return redirect(url_for("staff_client_detail", cid=cid))


# Staff: client detail
@app.route("/staff/clients/<int:cid>")
def staff_client_detail(cid):
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    if not client:
        abort(404)
    sessions_rows = db.execute(
        "SELECT * FROM sessions WHERE client_id=? ORDER BY session_at DESC LIMIT 25",
        (cid,)).fetchall()
    packages_rows = db.execute(
        "SELECT * FROM packages WHERE client_id=? ORDER BY purchased_at DESC LIMIT 25",
        (cid,)).fetchall()

    balance = client_balance(cid)
    latest_pkg = packages_rows[0]["minutes"] if packages_rows else 0
    pct = min(100.0, balance / latest_pkg * 100) if latest_pkg else 0.0

    return render_template(
        "staff/client_detail.html",
        staff=s, client=client, balance=balance, pct=pct,
        sessions=sessions_rows, packages=packages_rows,
    )


# Staff: sell package
@app.route("/staff/clients/<int:cid>/sell", methods=["GET", "POST"])
def staff_sell(cid):
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    if not client:
        abort(404)

    error = None
    if request.method == "POST":
        kind = request.form.get("kind", "")
        minutes = 0
        price_cents = 0
        pname = "Custom"

        if kind == "tier":
            tier_id = request.form.get("package_id", "")
            tier = None
            if tier_id.isdigit():
                tier = db.execute(
                    "SELECT * FROM package_tiers WHERE id=? AND active=1",
                    (int(tier_id),)).fetchone()
            if not tier:
                error = "Pick a package."
            else:
                minutes = tier["minutes"]
                price_cents = tier["price_cents"]
                pname = tier["name"]
        else:
            try:
                minutes = int(request.form.get("minutes", "0"))
                price = float(request.form.get("price", "0") or 0)
                price_cents = int(round(price * 100))
            except ValueError:
                error = "Minutes and price must be numbers."
            if not error and minutes <= 0:
                error = "Minutes must be greater than zero."

        if not error:
            db.execute(
                "INSERT INTO packages (client_id, staff_id, package_name, minutes, price_cents) "
                "VALUES (?,?,?,?,?)",
                (cid, s["id"], pname, minutes, price_cents),
            )
            db.commit()
            return redirect(url_for("staff_client_detail", cid=cid) + "?sold=1")

    return render_template(
        "staff/sell.html",
        staff=s, client=client, balance=client_balance(cid), error=error,
    )


# Staff: check-in / log session
@app.route("/staff/clients/<int:cid>/checkin", methods=["GET", "POST"])
def staff_checkin(cid):
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    db = get_db()
    client = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    if not client:
        abort(404)
    balance = client_balance(cid)
    error = None

    if request.method == "POST":
        bed_id_raw = request.form.get("bed_id", "")
        chosen = None
        if bed_id_raw.isdigit():
            chosen = db.execute(
                "SELECT id, name FROM beds WHERE id=? AND active=1",
                (int(bed_id_raw),)).fetchone()

        try:
            minutes = int(request.form.get("minutes", "0"))
        except ValueError:
            minutes = 0
        session_at = (request.form.get("session_at")
                      or datetime.now().strftime("%Y-%m-%dT%H:%M"))

        if not chosen:
            error = "Pick a bed."
        elif minutes <= 0:
            error = "Enter how many minutes were used."
        elif minutes > balance:
            error = (f"{client['first_name']} only has {balance} minutes available. "
                     "Sell a package first.")

        if not error:
            db.execute("BEGIN IMMEDIATE")
            locked_balance = client_balance(cid)
            if minutes > locked_balance:
                db.rollback()
                balance = locked_balance
                error = (f"{client['first_name']} only has {locked_balance} minutes available. "
                         "Sell a package first.")
            else:
                db.execute(
                    "INSERT INTO sessions (client_id, staff_id, bed, bed_id, minutes, session_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (cid, s["id"], chosen["name"], chosen["id"], minutes, session_at),
                )
                db.commit()
                return redirect(url_for("staff_client_detail", cid=cid) + "?logged=1")

    beds_view = []
    for b in load_active_beds():
        hours = bed_hours_used(b["id"], b["name"], b["tube_hours_baseline"],
                               b["tubes_installed_at"])
        pct = (hours / b["tube_lifetime_hours"] * 100) if b["tube_lifetime_hours"] else 0
        beds_view.append({**dict(b), "hours_used": hours, "pct": pct})

    return render_template(
        "staff/checkin.html",
        staff=s, client=client, balance=balance, error=error,
        beds=beds_view,
        default_dt=datetime.now().strftime("%Y-%m-%dT%H:%M"),
    )


# Staff: team management
def _pin_is_taken(pin, exclude_id=None):
    db = get_db()
    if exclude_id is not None:
        rows = db.execute(
            "SELECT pin FROM staff WHERE active = 1 AND id != ?",
            (exclude_id,)).fetchall()
    else:
        rows = db.execute("SELECT pin FROM staff WHERE active = 1").fetchall()
    return any(check_password_hash(r["pin"], pin) for r in rows)


def _active_staff_count():
    return get_db().execute(
        "SELECT COUNT(*) FROM staff WHERE active = 1").fetchone()[0]


@app.route("/staff/team")
def staff_team():
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    rows = get_db().execute(
        """SELECT s.id, s.name, s.active, s.created_at,
             (SELECT COUNT(*) FROM sessions WHERE staff_id=s.id) AS sessions_count,
             (SELECT COUNT(*) FROM packages WHERE staff_id=s.id) AS packages_count
           FROM staff s
           ORDER BY s.active DESC, s.name"""
    ).fetchall()
    return render_template("staff/team.html", staff=s, team=rows)


@app.route("/staff/team/new", methods=["POST"])
def staff_team_new():
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    name = request.form.get("name", "").strip()
    pin = request.form.get("pin", "").strip()

    if not name:
        flash("Name is required.", "error")
    elif not PIN_RE.match(pin):
        flash("PIN must be 4 to 6 digits.", "error")
    elif _pin_is_taken(pin):
        flash("That PIN is already in use — pick another.", "error")
    else:
        db = get_db()
        db.execute(
            "INSERT INTO staff (name, pin) VALUES (?, ?)",
            (name, generate_password_hash(pin)),
        )
        db.commit()
        flash(f"Welcome to the team, {name}.", "ok")
    return redirect(url_for("staff_team"))


@app.route("/staff/team/<int:tid>/edit", methods=["POST"])
def staff_team_edit(tid):
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    db = get_db()
    if not db.execute("SELECT 1 FROM staff WHERE id=?", (tid,)).fetchone():
        abort(404)

    name = request.form.get("name", "").strip()
    pin = request.form.get("pin", "").strip()

    if not name:
        flash("Name can't be empty.", "error")
        return redirect(url_for("staff_team"))

    if pin:
        if not PIN_RE.match(pin):
            flash("PIN must be 4 to 6 digits.", "error")
            return redirect(url_for("staff_team"))
        if _pin_is_taken(pin, exclude_id=tid):
            flash("That PIN is already in use — pick another.", "error")
            return redirect(url_for("staff_team"))
        db.execute("UPDATE staff SET name=?, pin=? WHERE id=?",
                   (name, generate_password_hash(pin), tid))
    else:
        db.execute("UPDATE staff SET name=? WHERE id=?", (name, tid))
    db.commit()
    flash(f"{name} updated.", "ok")
    return redirect(url_for("staff_team"))


@app.route("/staff/team/<int:tid>/deactivate", methods=["POST"])
def staff_team_deactivate(tid):
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    if tid == s["id"]:
        flash("You can't deactivate your own account.", "error")
        return redirect(url_for("staff_team"))
    if _active_staff_count() <= 1:
        flash("Can't deactivate the last active staff member.", "error")
        return redirect(url_for("staff_team"))

    db = get_db()
    target = db.execute(
        "SELECT name FROM staff WHERE id=? AND active=1", (tid,)).fetchone()
    if not target:
        flash("That teammate is already inactive.", "error")
        return redirect(url_for("staff_team"))
    db.execute("UPDATE staff SET active=0 WHERE id=?", (tid,))
    db.commit()
    flash(f"{target['name']} marked inactive. History preserved.", "ok")
    return redirect(url_for("staff_team"))


@app.route("/staff/team/<int:tid>/reactivate", methods=["POST"])
def staff_team_reactivate(tid):
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    db = get_db()
    target = db.execute(
        "SELECT id, name FROM staff WHERE id=? AND active=0", (tid,)).fetchone()
    if not target:
        flash("Already on the team.", "error")
        return redirect(url_for("staff_team"))

    # Force a fresh PIN at reactivation. The stored hash for an inactive
    # member can't be compared against active hashes (salted), so a new PIN
    # is the safe way to guarantee no clash with anyone now on the team.
    pin = request.form.get("pin", "").strip()
    if not PIN_RE.match(pin):
        flash("Reactivation needs a new 4–6 digit PIN.", "error")
        return redirect(url_for("staff_team"))
    if _pin_is_taken(pin):
        flash("That PIN is already in use by an active teammate — pick another.", "error")
        return redirect(url_for("staff_team"))

    db.execute("UPDATE staff SET active=1, pin=? WHERE id=?",
               (generate_password_hash(pin), tid))
    db.commit()
    flash(f"{target['name']} is back on the team.", "ok")
    return redirect(url_for("staff_team"))


# Staff: package catalog
def _parse_tier_form(form):
    """Returns (fields_dict, error_or_None)."""
    name = form.get("name", "").strip()
    tagline = form.get("tagline", "").strip()
    if not name:
        return None, "Name is required."
    if len(name) > 50:
        return None, "Name is too long (max 50 characters)."
    if len(tagline) > 120:
        return None, "Tagline is too long (max 120 characters)."
    try:
        minutes = int(form.get("minutes", "0"))
    except ValueError:
        return None, "Minutes must be a whole number."
    if minutes <= 0:
        return None, "Minutes must be greater than zero."
    try:
        price = float(form.get("price", "0") or 0)
    except ValueError:
        return None, "Price must be a number."
    if price < 0:
        return None, "Price can't be negative."
    try:
        display_order = int(form.get("display_order", "0") or 0)
    except ValueError:
        return None, "Display order must be a whole number."
    return {
        "name": name,
        "minutes": minutes,
        "price_cents": int(round(price * 100)),
        "tagline": tagline or None,
        "display_order": display_order,
    }, None


def _tier_name_taken(name, exclude_id=None):
    db = get_db()
    if exclude_id is not None:
        return db.execute(
            "SELECT 1 FROM package_tiers WHERE active=1 AND lower(name)=lower(?) AND id!=?",
            (name, exclude_id)).fetchone() is not None
    return db.execute(
        "SELECT 1 FROM package_tiers WHERE active=1 AND lower(name)=lower(?)",
        (name,)).fetchone() is not None


@app.route("/staff/packages")
def staff_packages():
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    rows = get_db().execute(
        """SELECT t.*,
             (SELECT COUNT(*) FROM packages WHERE package_name = t.name) AS times_sold
           FROM package_tiers t
           ORDER BY t.active DESC, t.display_order, t.id"""
    ).fetchall()
    next_order = (max((r["display_order"] for r in rows if r["active"]), default=0)
                  + 10) if rows else 10
    return render_template("staff/packages.html",
                           staff=s, tiers=rows, next_order=next_order)


@app.route("/staff/packages/new", methods=["POST"])
def staff_packages_new():
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    fields, err = _parse_tier_form(request.form)
    if err:
        flash(err, "error")
        return redirect(url_for("staff_packages"))
    if _tier_name_taken(fields["name"]):
        flash("Another active package already uses that name.", "error")
        return redirect(url_for("staff_packages"))
    db = get_db()
    db.execute(
        "INSERT INTO package_tiers (name, minutes, price_cents, tagline, display_order) "
        "VALUES (?,?,?,?,?)",
        (fields["name"], fields["minutes"], fields["price_cents"],
         fields["tagline"], fields["display_order"]),
    )
    db.commit()
    flash(f"Added {fields['name']} to the catalog.", "ok")
    return redirect(url_for("staff_packages"))


@app.route("/staff/packages/<int:tid>/edit", methods=["POST"])
def staff_packages_edit(tid):
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    db = get_db()
    if not db.execute("SELECT 1 FROM package_tiers WHERE id=?", (tid,)).fetchone():
        abort(404)
    fields, err = _parse_tier_form(request.form)
    if err:
        flash(err, "error")
        return redirect(url_for("staff_packages"))
    if _tier_name_taken(fields["name"], exclude_id=tid):
        flash("Another active package already uses that name.", "error")
        return redirect(url_for("staff_packages"))
    db.execute(
        "UPDATE package_tiers SET name=?, minutes=?, price_cents=?, tagline=?, display_order=? "
        "WHERE id=?",
        (fields["name"], fields["minutes"], fields["price_cents"],
         fields["tagline"], fields["display_order"], tid),
    )
    db.commit()
    flash(f"{fields['name']} updated.", "ok")
    return redirect(url_for("staff_packages"))


@app.route("/staff/packages/<int:tid>/deactivate", methods=["POST"])
def staff_packages_deactivate(tid):
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    db = get_db()
    tier = db.execute(
        "SELECT name FROM package_tiers WHERE id=? AND active=1", (tid,)).fetchone()
    if not tier:
        flash("That package is already inactive.", "error")
        return redirect(url_for("staff_packages"))
    db.execute("UPDATE package_tiers SET active=0 WHERE id=?", (tid,))
    db.commit()
    flash(f"{tier['name']} retired. Past sales are unaffected.", "ok")
    return redirect(url_for("staff_packages"))


@app.route("/staff/packages/<int:tid>/reactivate", methods=["POST"])
def staff_packages_reactivate(tid):
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    db = get_db()
    tier = db.execute(
        "SELECT id, name FROM package_tiers WHERE id=? AND active=0",
        (tid,)).fetchone()
    if not tier:
        flash("Already active.", "error")
        return redirect(url_for("staff_packages"))
    if _tier_name_taken(tier["name"], exclude_id=tid):
        flash(f"Can't reactivate — another active package already uses the name '{tier['name']}'. "
              "Rename one of them first.", "error")
        return redirect(url_for("staff_packages"))
    db.execute("UPDATE package_tiers SET active=1 WHERE id=?", (tid,))
    db.commit()
    flash(f"{tier['name']} is back in the catalog.", "ok")
    return redirect(url_for("staff_packages"))


# Staff: beds catalog + tube tracking
def _parse_bed_form(form):
    name = form.get("name", "").strip()
    if not name:
        return None, "Bed name is required."
    if len(name) > 50:
        return None, "Bed name is too long (max 50 characters)."
    try:
        lifetime = int(form.get("tube_lifetime_hours", "0"))
    except ValueError:
        return None, "Tube lifetime must be a whole number of hours."
    if lifetime <= 0:
        return None, "Tube lifetime must be greater than zero."
    try:
        baseline = float(form.get("tube_hours_baseline", "0") or 0)
    except ValueError:
        return None, "Baseline hours must be a number."
    if baseline < 0:
        return None, "Baseline hours can't be negative."
    try:
        display_order = int(form.get("display_order", "0") or 0)
    except ValueError:
        return None, "Display order must be a whole number."
    return {
        "name": name,
        "tube_lifetime_hours": lifetime,
        "tube_hours_baseline": baseline,
        "display_order": display_order,
    }, None


def _bed_name_taken(name, exclude_id=None):
    db = get_db()
    if exclude_id is not None:
        return db.execute(
            "SELECT 1 FROM beds WHERE lower(name)=lower(?) AND id!=?",
            (name, exclude_id)).fetchone() is not None
    return db.execute(
        "SELECT 1 FROM beds WHERE lower(name)=lower(?)",
        (name,)).fetchone() is not None


@app.route("/staff/beds")
def staff_beds():
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    rows = get_db().execute(
        "SELECT * FROM beds ORDER BY active DESC, display_order, id"
    ).fetchall()
    beds = []
    for b in rows:
        hours = bed_hours_used(b["id"], b["name"], b["tube_hours_baseline"],
                               b["tubes_installed_at"])
        pct = (hours / b["tube_lifetime_hours"] * 100) if b["tube_lifetime_hours"] else 0
        beds.append({**dict(b), "hours_used": hours, "pct": pct})
    next_order = (max((b["display_order"] for b in beds if b["active"]), default=0)
                  + 10) if beds else 10
    return render_template("staff/beds.html",
                           staff=s, beds=beds, next_order=next_order)


@app.route("/staff/beds/new", methods=["POST"])
def staff_beds_new():
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    fields, err = _parse_bed_form(request.form)
    if err:
        flash(err, "error")
        return redirect(url_for("staff_beds"))
    if _bed_name_taken(fields["name"]):
        flash("Another bed already uses that name.", "error")
        return redirect(url_for("staff_beds"))
    db = get_db()
    db.execute(
        "INSERT INTO beds (name, tube_lifetime_hours, tube_hours_baseline, display_order) "
        "VALUES (?,?,?,?)",
        (fields["name"], fields["tube_lifetime_hours"],
         fields["tube_hours_baseline"], fields["display_order"]),
    )
    db.commit()
    flash(f"Added {fields['name']}.", "ok")
    return redirect(url_for("staff_beds"))


@app.route("/staff/beds/<int:bid>/edit", methods=["POST"])
def staff_beds_edit(bid):
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    db = get_db()
    if not db.execute("SELECT 1 FROM beds WHERE id=?", (bid,)).fetchone():
        abort(404)
    fields, err = _parse_bed_form(request.form)
    if err:
        flash(err, "error")
        return redirect(url_for("staff_beds"))
    if _bed_name_taken(fields["name"], exclude_id=bid):
        flash("Another bed already uses that name.", "error")
        return redirect(url_for("staff_beds"))
    db.execute(
        "UPDATE beds SET name=?, tube_lifetime_hours=?, tube_hours_baseline=?, "
        "display_order=? WHERE id=?",
        (fields["name"], fields["tube_lifetime_hours"],
         fields["tube_hours_baseline"], fields["display_order"], bid),
    )
    db.commit()
    flash(f"{fields['name']} updated.", "ok")
    return redirect(url_for("staff_beds"))


@app.route("/staff/beds/<int:bid>/replace-tubes", methods=["POST"])
def staff_beds_replace_tubes(bid):
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    db = get_db()
    bed = db.execute("SELECT name FROM beds WHERE id=?", (bid,)).fetchone()
    if not bed:
        abort(404)
    db.execute(
        "UPDATE beds SET tube_hours_baseline=0, tubes_installed_at=? WHERE id=?",
        (datetime.utcnow().isoformat(timespec="seconds"), bid),
    )
    db.commit()
    flash(f"{bed['name']}: new tubes logged. Counter reset to zero.", "ok")
    return redirect(url_for("staff_beds"))


@app.route("/staff/beds/<int:bid>/deactivate", methods=["POST"])
def staff_beds_deactivate(bid):
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    db = get_db()
    active_count = db.execute("SELECT COUNT(*) FROM beds WHERE active=1").fetchone()[0]
    if active_count <= 1:
        flash("Can't retire the last active bed — at least one must remain.", "error")
        return redirect(url_for("staff_beds"))
    bed = db.execute("SELECT name FROM beds WHERE id=? AND active=1", (bid,)).fetchone()
    if not bed:
        flash("That bed is already retired.", "error")
        return redirect(url_for("staff_beds"))
    db.execute("UPDATE beds SET active=0 WHERE id=?", (bid,))
    db.commit()
    flash(f"{bed['name']} retired. Past sessions stay on the books.", "ok")
    return redirect(url_for("staff_beds"))


@app.route("/staff/beds/<int:bid>/reactivate", methods=["POST"])
def staff_beds_reactivate(bid):
    s = require_staff()
    if not isinstance(s, sqlite3.Row):
        return s
    db = get_db()
    bed = db.execute(
        "SELECT id, name FROM beds WHERE id=? AND active=0", (bid,)).fetchone()
    if not bed:
        flash("Already active.", "error")
        return redirect(url_for("staff_beds"))
    if _bed_name_taken(bed["name"], exclude_id=bid):
        flash(f"Can't reactivate — another bed uses the name '{bed['name']}'. "
              "Rename one of them first.", "error")
        return redirect(url_for("staff_beds"))
    db.execute("UPDATE beds SET active=1 WHERE id=?", (bid,))
    db.commit()
    flash(f"{bed['name']} is back in service.", "ok")
    return redirect(url_for("staff_beds"))


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", 5800)),
            debug=debug)
