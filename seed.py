import os
import re
import sqlite3
import random
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

PIN_RE = re.compile(r"^\d{4,6}$")


INITIAL_TIERS = [
    # name,       minutes, price_cents, tagline,                                       display_order
    ("Starter",        60,  4500, "Three to four short sessions to ease in.",       10),
    ("Standard",      150,  9900, "Our most loved package.",                        20),
    ("Premium",       300, 17900, "Glow for the whole season.",                     30),
]


INITIAL_BEDS = [
    # name,    lifetime_hours, display_order
    ("Bed 1",     700, 10),
    ("Bed 2",     700, 20),
    ("Bed 3",     700, 30),
]


def seed(db_path):
    db = sqlite3.connect(db_path)

    # Package catalog
    if not db.execute("SELECT 1 FROM package_tiers LIMIT 1").fetchone():
        db.executemany(
            "INSERT INTO package_tiers (name, minutes, price_cents, tagline, display_order) "
            "VALUES (?,?,?,?,?)",
            INITIAL_TIERS,
        )
        db.commit()

    # Beds
    if not db.execute("SELECT 1 FROM beds LIMIT 1").fetchone():
        db.executemany(
            "INSERT INTO beds (name, tube_lifetime_hours, display_order) VALUES (?,?,?)",
            INITIAL_BEDS,
        )
        db.commit()

    # Staff
    if not db.execute("SELECT 1 FROM staff LIMIT 1").fetchone():
        name = os.environ.get("SUNSET_INITIAL_STAFF_NAME", "Owner").strip() or "Owner"
        pin = os.environ.get("SUNSET_INITIAL_STAFF_PIN", "").strip()
        if not PIN_RE.match(pin):
            db.close()
            raise RuntimeError(
                "Set SUNSET_INITIAL_STAFF_PIN to a 4-6 digit PIN for first startup."
            )
        db.execute("INSERT INTO staff (name, pin) VALUES (?, ?)",
                   (name, generate_password_hash(pin)))
        db.commit()

    # Demo clients are opt-in only.
    if os.environ.get("SUNSET_SEED_DEMO") != "1":
        db.close()
        return
    if db.execute("SELECT 1 FROM clients LIMIT 1").fetchone():
        db.close()
        return

    sample = [
        ("Aria",   "Mendoza",  "07700 900102", "aria@example.co.uk",   "Fair skin — keep under 10 min."),
        ("Tomás",  "Bellini",  "07700 900144", "tomas@example.co.uk",  ""),
        ("Niamh",  "O'Connor", "07700 900177", "niamh@example.co.uk",  "Easily burns. Olive base."),
        ("Marcus", "Hayes",    "07700 900191", "marcus@example.co.uk", ""),
        ("Yuki",   "Tanaka",   "07700 900123", "yuki@example.co.uk",   "Prefers Bed 2."),
        ("Elena",  "Vasquez",  "07700 900158", "elena@example.co.uk",  ""),
        ("Kenji",  "Park",     "07700 900166", "kenji@example.co.uk",  "New client — recommended Starter."),
    ]
    tiers = [(t[0], t[1], t[2]) for t in INITIAL_TIERS]

    rng = random.Random(7)
    for fn, ln, ph, em, notes in sample:
        cur = db.execute(
            "INSERT INTO clients (first_name, last_name, phone, email, notes, consent_signed_at) "
            "VALUES (?,?,?,?,?,?)",
            (fn, ln, ph, em, notes, datetime.utcnow().isoformat()),
        )
        cid = cur.lastrowid

        bought = 0
        for _ in range(rng.randint(1, 2)):
            tier = rng.choice(tiers)
            when = (datetime.now() - timedelta(days=rng.randint(20, 80))).strftime("%Y-%m-%dT%H:%M")
            db.execute(
                "INSERT INTO packages (client_id, staff_id, package_name, minutes, price_cents, purchased_at) "
                "VALUES (?,?,?,?,?,?)",
                (cid, 1, tier[0], tier[1], tier[2], when),
            )
            bought += tier[1]

        # Sessions can never overdraw the package balance.
        bed_lookup = {r[0]: r[1] for r in
                      db.execute("SELECT name, id FROM beds").fetchall()}
        used = 0
        for _ in range(rng.randint(2, 6)):
            mins = rng.choice([8, 10, 12, 15])
            if used + mins > bought:
                break
            when = (datetime.now() - timedelta(days=rng.randint(1, 40),
                                               hours=rng.randint(0, 8))).strftime("%Y-%m-%dT%H:%M")
            bed = rng.choice(list(bed_lookup.keys()))
            db.execute(
                "INSERT INTO sessions (client_id, staff_id, bed, bed_id, minutes, session_at) "
                "VALUES (?,?,?,?,?,?)",
                (cid, rng.choice([1]), bed, bed_lookup[bed], mins, when),
            )
            used += mins

    db.commit()
    db.close()


if __name__ == "__main__":
    seed("sunset_lounge.db")
    print("Seeded sunset_lounge.db")
