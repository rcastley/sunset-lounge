# Sunset Lounge

Small Flask app for a family-run tanning salon. It has a customer kiosk for client registration and a staff portal for logging sessions, selling packages, and managing staff/package records.

## Requirements

- Python 3.10 or newer
- A local network shared by the computer running the app and the iPads

## First Setup

Create and activate a virtual environment:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a strong app secret:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

On the first run of a new database, set the initial staff user and PIN:

```sh
export SUNSET_SECRET="paste-the-generated-secret-here"
export SUNSET_INITIAL_STAFF_NAME="Owner"
export SUNSET_INITIAL_STAFF_PIN="choose-a-4-to-6-digit-pin"
python app.py
```

The app creates `sunset_lounge.db` automatically in this folder.

## Normal Local Use

After the first run, only `SUNSET_SECRET` is required:

```sh
source .venv/bin/activate
export SUNSET_SECRET="paste-the-same-secret-here"
python app.py
```

Open the app on the computer at:

```text
http://127.0.0.1:5800
```

## iPad Setup

To make the app reachable from iPads on the same Wi-Fi network, start it on all network interfaces:

```sh
source .venv/bin/activate
export SUNSET_SECRET="paste-the-same-secret-here"
export HOST="0.0.0.0"
python app.py
```

Find the computer's local IP address in macOS System Settings or run:

```sh
ipconfig getifaddr en0
```

Then open these URLs on the iPads, replacing `<computer-ip>` with that address:

```text
Customer registration iPad: http://<computer-ip>:5050/
Staff iPad:                 http://<computer-ip>:5050/staff
```

Use iPad Guided Access or a kiosk browser to keep each iPad on its intended page.

## Staff Setup

After signing in, go to `/staff/team` to add real staff accounts and set unique 4-6 digit PINs.

If this project already has the demo database, change or deactivate the demo staff PINs before using it with real customers.

## Backups

Back up this file regularly:

```text
sunset_lounge.db
```

It contains client registration details, package sales, and session history.

## Development Mode

Only use debug mode while developing locally:

```sh
export FLASK_DEBUG="1"
export SUNSET_SECRET="dev-only-secret"
python app.py
```

Do not run debug mode on the salon network.
