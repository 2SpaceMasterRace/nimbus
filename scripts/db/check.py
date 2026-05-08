"""Check Nimbus Postgres readiness."""

from __future__ import annotations

from nimbus_runtime.postgres import check_ready


def main() -> int:
    """Verify the database is reachable and migrated."""
    check_ready()
    print("Nimbus Postgres schema is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
