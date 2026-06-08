# Sunset Lounge

Small Flask + HTMX app for a family-run tanning salon. It has a customer kiosk for client registration and a staff portal for logging sessions, selling packages, and managing staff, package catalog, and beds (with tube-lifetime tracking).

The customer kiosk lives at `/`. The staff portal lives at `/staff` and is reachable only by typing the URL directly — no customer-visible page links to it.

## Run with Docker Compose (recommended)

You need Docker Desktop (Mac / Windows) or Docker Engine + Compose plugin (Linux).

1. Copy the example env file and fill it in:

   ```sh
   cp .env.example .env
   ```

   In `.env`, set:

   - `SUNSET_SECRET` — generate with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
   - `SUNSET_INITIAL_STAFF_NAME` — the first staff name shown in the team list
   - `SUNSET_INITIAL_STAFF_PIN` — a 4-6 digit PIN you'll use to sign in for the first time

2. Build and start:

   ```sh
   docker compose up -d --build
   ```

3. Open the app at <http://localhost:5800>.

   - Customer kiosk: <http://localhost:5800/>
   - Staff portal:   <http://localhost:5800/staff>

After the first sign-in, you can remove `SUNSET_INITIAL_STAFF_NAME` and `SUNSET_INITIAL_STAFF_PIN` from `.env` — they're only consulted when the staff table is empty.

The SQLite database lives in a named Docker volume (`sunset-data`) and survives container restarts, rebuilds, and image upgrades.

### iPad on the same network

Find the host computer's local IP:

```sh
ipconfig getifaddr en0   # macOS
hostname -I              # Linux
```

Then on each iPad open `http://<computer-ip>:5800/` for the customer kiosk or `http://<computer-ip>:5800/staff` for staff. Use iPad Guided Access or a kiosk browser to lock each iPad on the intended page.

### Behind a reverse proxy (Nginx Proxy Manager, Caddy, Traefik)

The app trusts one hop of `X-Forwarded-Proto`, `X-Forwarded-Host`, `X-Forwarded-For`, and `X-Forwarded-Prefix` by default so Flask sees the original HTTPS request even though the container only receives plain HTTP from the proxy. This prevents the "too many redirects" loop you get otherwise, especially when `SUNSET_COOKIE_SECURE=1` is on.

If you're not behind a proxy, or you want to disable that trust for any reason, set `SUNSET_TRUST_PROXY=0` in `.env`.

When terminating TLS on the proxy, also set this in `.env`:

```
SUNSET_COOKIE_SECURE=1
```

so the session cookie is only ever sent over HTTPS.

### Common commands

```sh
docker compose logs -f          # tail logs
docker compose ps               # status + healthcheck
docker compose restart          # restart the app
docker compose down             # stop (keeps the data volume)
docker compose down -v          # stop AND wipe the database
```

### Demo data on a fresh install

To populate sample clients, packages, and sessions so you can click around right away, add this to `.env` before the very first `docker compose up`:

```
SUNSET_SEED_DEMO=1
```

Demo data is only inserted when the clients table is empty, so it's safe to leave this enabled.

### Backups

The SQLite file is `/data/sunset_lounge.db` inside the container, stored on the `sunset-data` Docker volume. To copy a backup to the host:

```sh
docker compose cp app:/data/sunset_lounge.db ./backup-$(date +%Y%m%d).db
```

To restore one, stop the stack, copy the file back, and start it again.

## Run locally without Docker (development)

For frontend or template hacking it's often easier to skip Docker. You need Python 3.10+.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export SUNSET_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export SUNSET_INITIAL_STAFF_NAME="Owner"
export SUNSET_INITIAL_STAFF_PIN="1234"
export SUNSET_SEED_DEMO=1
export FLASK_DEBUG=1

python app.py
```

The app starts the Flask dev server on <http://127.0.0.1:5800>. Templates auto-reload when you save.

The database file is created next to `app.py` (`sunset_lounge.db`) unless you set `SUNSET_DB_PATH`. The `.gitignore` excludes it.

Never run `FLASK_DEBUG=1` against real client data — it exposes the Werkzeug interactive debugger.

## Architecture notes

- **Stack**: Flask + Jinja + HTMX + Tailwind (Play CDN), SQLite for storage.
- **Auth**: PIN-based, hashed with werkzeug. Rate-limited per IP (5 failed attempts → 2-minute lockout). All POST forms carry a CSRF token.
- **Soft delete**: Staff, packages, and beds can be retired (not hard-deleted) so historical sessions and sales keep their attribution.
- **Tube tracking**: Each bed has a tube lifetime in hours and an installed date; usage is computed from sessions logged since the last install. "Tubes replaced" resets the counter.
- **Logo**: bronze/gold script on hard white. `mix-blend-mode: multiply` drops the white over cream/sunset surfaces; on dark surfaces (thanks, login) the logo sits inside a cream "seal" card.
