"""String -> typed value conversion.

Everything arriving from a URL is a string. Mongo, however, compares by type: a document with
``{"age": 21}`` will never match ``{"age": "21"}``. Silent type mismatch is the single most common
cause of "the filter returns nothing and no one knows why", so coercion is strict and loud.
"""

from __future__ import annotations

from datetime import date, datetime

from .errors import InvalidValue

try:  # pragma: no cover
    from bson import ObjectId
except ImportError:  # pragma: no cover
    ObjectId = None

TRUE_VALUES = frozenset({"true", "1", "yes", "on"})
FALSE_VALUES = frozenset({"false", "0", "no", "off"})


def _to_bool(raw: str, param: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    raise InvalidValue(f"{param}: expected a boolean, got {raw!r}", param=param)


def _to_datetime(raw: str, param: str) -> datetime:
    text = raw.strip()
    # datetime.fromisoformat only learned to accept a trailing "Z" in 3.11; normalise for 3.10.
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise InvalidValue(f"{param}: expected an ISO-8601 datetime, got {raw!r}", param=param) from None


def _to_date(raw: str, param: str) -> datetime:
    try:
        parsed = date.fromisoformat(raw.strip())
    except ValueError:
        raise InvalidValue(f"{param}: expected an ISO-8601 date, got {raw!r}", param=param) from None
    # Mongo has no date type: store dates as midnight datetimes so comparisons behave.
    return datetime(parsed.year, parsed.month, parsed.day)


def _to_object_id(raw: str, param: str):
    if ObjectId is None:  # pragma: no cover
        raise InvalidValue(f"{param}: ObjectId fields require pymongo to be installed", param=param)
    try:
        return ObjectId(raw.strip())
    except Exception:
        raise InvalidValue(f"{param}: expected a 24-character ObjectId, got {raw!r}", param=param) from None


def coerce(type_: type, raw: str, param: str):
    """Convert one raw query-string value to the field's declared type."""
    if type_ is str:
        return raw
    if type_ is bool:
        return _to_bool(raw, param)
    if type_ is datetime:
        return _to_datetime(raw, param)
    if type_ is date:
        return _to_date(raw, param)
    if ObjectId is not None and type_ is ObjectId:
        return _to_object_id(raw, param)
    if type_ in (int, float):
        try:
            return type_(raw.strip())
        except ValueError:
            raise InvalidValue(f"{param}: expected {type_.__name__}, got {raw!r}", param=param) from None
    raise InvalidValue(f"{param}: no coercion registered for {type_.__name__}", param=param)
