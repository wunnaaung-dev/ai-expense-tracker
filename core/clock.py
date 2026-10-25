from datetime import date, datetime, timedelta, timezone

# Asia/Bangkok has no DST, so a fixed offset is exact and needs no tzdata.
BANGKOK = timezone(timedelta(hours=7))


def today() -> date:
    """The user's "today": dates default to Bangkok time, not the server's UTC."""
    return datetime.now(BANGKOK).date()
