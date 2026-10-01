from datetime import datetime, timezone


def utc_now():
    """UTC sem tzinfo para manter a convenção dos DateTime SQLite existentes."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
