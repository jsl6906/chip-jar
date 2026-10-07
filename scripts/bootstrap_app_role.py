"""One-time bootstrap: set a random password on personal_tracking_app and write .env.

Connects as the Entra admin using an `az` access token, so no admin password is needed
and the generated app password is never printed.

Usage:
    uv run scripts/bootstrap_app_role.py [--force]
"""

import argparse
import secrets
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import psycopg
from psycopg import sql

HOST = "jsl6906.postgres.database.azure.com"
DBNAME = "personal_storage"
APP_ROLE = "personal_tracking_app"
# Entra admin as registered in Postgres (guest-user UPN, not the plain gmail address)
ADMIN_USER = "jsl6906_gmail.com#EXT#@jsl6906gmail.onmicrosoft.com"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
AAD_RESOURCE = "https://ossrdbms-aad.database.windows.net"


def az(*args: str) -> str:
    return subprocess.run(
        ["az", *args, "-o", "tsv"], check=True, capture_output=True, text=True, shell=sys.platform == "win32"
    ).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="Overwrite an existing .env")
    ap.add_argument("--admin-user", default=ADMIN_USER, help="Entra admin principal name as known to Postgres")
    args = ap.parse_args()

    if ENV_PATH.exists() and not args.force:
        print(f"{ENV_PATH} already exists; re-run with --force to rotate the password.", file=sys.stderr)
        return 1

    admin_user = args.admin_user
    token = az("account", "get-access-token", "--resource", AAD_RESOURCE, "--query", "accessToken")

    new_password = secrets.token_urlsafe(32)  # URL-safe, so no percent-encoding needed in the DSN

    with psycopg.connect(
        host=HOST, dbname=DBNAME, user=admin_user, password=token, sslmode="require", autocommit=True
    ) as admin:
        admin.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(APP_ROLE), sql.Literal(new_password))
        )
    print(f"Password set on role {APP_ROLE} (as {admin_user}).")

    dsn = f"postgresql://{APP_ROLE}:{quote(new_password, safe='')}@{HOST}:5432/{DBNAME}?sslmode=require"
    with psycopg.connect(dsn, application_name="chip-jar-bootstrap") as app:
        who, path = app.execute("SELECT current_user, current_setting('search_path')").fetchone()
    print(f"Verified login as {who}; search_path={path}")

    ENV_PATH.write_text(f"DATABASE_URL={dsn}\n", encoding="utf-8")
    print(f"Wrote {ENV_PATH} (git-ignored). Password not displayed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
