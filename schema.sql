CREATE TABLE IF NOT EXISTS staff (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  pin TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS package_tiers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  minutes INTEGER NOT NULL CHECK (minutes > 0),
  price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
  tagline TEXT,
  display_order INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clients (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  first_name TEXT NOT NULL,
  last_name TEXT NOT NULL,
  phone TEXT,
  email TEXT,
  birthdate TEXT,
  notes TEXT,
  consent_signed_at TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS packages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  staff_id INTEGER REFERENCES staff(id),
  package_name TEXT NOT NULL,
  minutes INTEGER NOT NULL,
  price_cents INTEGER NOT NULL,
  purchased_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS beds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  tube_lifetime_hours INTEGER NOT NULL DEFAULT 700 CHECK (tube_lifetime_hours > 0),
  tube_hours_baseline REAL NOT NULL DEFAULT 0 CHECK (tube_hours_baseline >= 0),
  tubes_installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  display_order INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  staff_id INTEGER REFERENCES staff(id),
  bed TEXT NOT NULL,
  bed_id INTEGER REFERENCES beds(id),
  minutes INTEGER NOT NULL,
  session_at TEXT NOT NULL,
  recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tube_replacements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bed_id INTEGER NOT NULL REFERENCES beds(id),
  staff_id INTEGER REFERENCES staff(id),
  hours_at_replacement REAL NOT NULL,
  lifetime_hours_at_replacement INTEGER NOT NULL,
  notes TEXT,
  replaced_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tube_replacements_bed ON tube_replacements(bed_id, replaced_at);
CREATE INDEX IF NOT EXISTS idx_sessions_client ON sessions(client_id);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(session_at);
CREATE INDEX IF NOT EXISTS idx_sessions_bed ON sessions(bed_id, session_at);
CREATE INDEX IF NOT EXISTS idx_packages_client ON packages(client_id);
