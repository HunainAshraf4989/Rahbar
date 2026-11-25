"""Apply additive account/WhatsApp migrations to the configured development database."""

from contextlib import closing
from pathlib import Path

import psycopg2
from rahbar.config import load_supabase_settings


def migrate(root: Path) -> None:
    dsn = load_supabase_settings(root).database_url
    if not dsn:
        raise SystemExit("Configure the development database before applying migrations")
    with closing(psycopg2.connect(dsn, connect_timeout=10, sslmode="require")) as connection:
        for name in (
            "001_account_profiles.sql",
            "002_whatsapp_runtime.sql",
            "003_whatsapp_registration_notices.sql",
        ):
            try:
                with connection.cursor() as cursor:
                    cursor.execute((root / "migrations" / name).read_text(encoding="utf-8"))
                connection.commit()
            except Exception:
                connection.rollback()
                raise SystemExit(
                    f"Migration {name} failed; database details were not logged"
                ) from None
            print("Applied", name)


if __name__ == "__main__":
    migrate(Path.cwd())
