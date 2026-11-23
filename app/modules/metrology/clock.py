from datetime import datetime, timezone

from app.errors import InspectionError


def parse_instant(value, code='BAD_TIMESTAMP'):
    if not isinstance(value, str) or not value.strip():
        raise InspectionError(code, status=422)
    text = value.strip()
    try:
        instant = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        raise InspectionError(code, status=422) from None
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise InspectionError('TIMEZONE_REQUIRED', status=422)
    return instant.astimezone(timezone.utc)


def stamp(instant):
    if isinstance(instant, str):
        return stamp(parse_instant(instant))
    return instant.astimezone(timezone.utc).isoformat(timespec='seconds')
