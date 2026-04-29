"""Run Nimbus Postgres schema migrations."""

from __future__ import annotations

from nimbus_runtime.postgres import migrate


def main() -> int:
    """Apply all current database migrations."""
    migrate()
    print("Nimbus Postgres schema is up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
