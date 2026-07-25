"""Keyset (cursor) pagination.

``skip``/``limit`` degrades linearly: to serve page 2000 the server walks 100,000 documents it will
never return. Keyset pagination replaces the skip with a range clause built from the last document
of the previous page, so every page costs the same as the first — provided the sort is backed by an
index.

The cursor is opaque to clients and, when a secret is supplied, HMAC-signed so it cannot be forged
into a filter of the client's choosing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime
from typing import Any

from .errors import InvalidCursor

try:  # pragma: no cover
    from bson import ObjectId
except ImportError:  # pragma: no cover
    ObjectId = None

_MISSING = object()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except Exception:
        raise InvalidCursor("cursor is not valid base64", param="after") from None


def _pack(value: Any) -> list[Any]:
    """Tag a value with its type so decoding does not need the schema."""
    if isinstance(value, bool):
        return ["b", value]
    if isinstance(value, datetime):
        return ["d", value.isoformat()]
    if ObjectId is not None and isinstance(value, ObjectId):
        return ["o", str(value)]
    if isinstance(value, (int, float, str)) or value is None:
        return ["j", value]
    raise InvalidCursor(f"cannot put {type(value).__name__} in a cursor", param="after")


def _unpack(item: Any) -> Any:
    if not isinstance(item, list) or len(item) != 2:
        raise InvalidCursor("malformed cursor value", param="after")
    tag, value = item
    if tag == "j" or tag == "b":
        return value
    if tag == "d":
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            raise InvalidCursor("malformed datetime in cursor", param="after") from None
    if tag == "o":
        if ObjectId is None:  # pragma: no cover
            raise InvalidCursor("cursor contains an ObjectId but pymongo is not installed", param="after")
        try:
            return ObjectId(value)
        except Exception:
            raise InvalidCursor("malformed ObjectId in cursor", param="after") from None
    raise InvalidCursor(f"unknown cursor value type {tag!r}", param="after")


def _dig(document: dict, path: str) -> Any:
    """Read a possibly dotted path out of a document."""
    current: Any = document
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


class Cursors:
    """Encodes and decodes pagination cursors.

    Args:
        secret: signing key. Strongly recommended — without it a client can hand-craft a cursor
            and page from an arbitrary position. Signing does not hide the contents; it only
            proves the server issued them.
        id_field: the tiebreaker field appended to every sort so the order is total. Two documents
            sharing a ``created_at`` would otherwise be able to straddle a page boundary, and one
            of them would be skipped or repeated.
    """

    __slots__ = ("_secret", "id_field")

    def __init__(self, secret: bytes | str | None = None, *, id_field: str = "_id"):
        if isinstance(secret, str):
            secret = secret.encode()
        self._secret = secret
        self.id_field = id_field

    @property
    def signed(self) -> bool:
        return self._secret is not None

    def _signature(self, payload: bytes) -> str:
        return _b64encode(hmac.new(self._secret, payload, hashlib.sha256).digest())

    def encode(self, document: dict, sort: list[tuple[str, int]]) -> str:
        """Build the cursor that resumes *after* ``document``, given the sort used to fetch it."""
        values = []
        for column, _ in sort:
            value = _dig(document, column)
            if value is _MISSING:
                raise InvalidCursor(f"document has no {column!r} to build a cursor from", param="after")
            values.append(_pack(value))
        payload = json.dumps(values, separators=(",", ":"), sort_keys=True).encode()
        token = _b64encode(payload)
        if self._secret is None:
            return token
        return f"{token}.{self._signature(payload)}"

    def decode(self, token: str) -> list[Any]:
        signature = None
        if "." in token:
            token, _, signature = token.partition(".")
        if self._secret is None:
            if signature is not None:
                raise InvalidCursor("cursor is signed but no secret is configured", param="after")
        else:
            if signature is None:
                raise InvalidCursor("cursor is not signed", param="after")
            if not hmac.compare_digest(self._signature(_b64decode(token)), signature):
                raise InvalidCursor("cursor signature does not match", param="after")
        try:
            values = json.loads(_b64decode(token))
        except ValueError:
            raise InvalidCursor("cursor is not valid JSON", param="after") from None
        if not isinstance(values, list):
            raise InvalidCursor("cursor payload must be a list", param="after")
        return [_unpack(item) for item in values]

    def __repr__(self) -> str:  # never leak the secret into a log line
        return f"Cursors(signed={self.signed}, id_field={self.id_field!r})"


def keyset_filter(sort: list[tuple[str, int]], values: list[Any]) -> dict[str, Any]:
    """Range clause selecting everything strictly after ``values`` in ``sort`` order.

    For ``sort=[("created_at", -1), ("_id", 1)]`` this produces the lexicographic comparison::

        {"$or": [{"created_at": {"$lt": t}},
                 {"created_at": t, "_id": {"$gt": i}}]}
    """
    if len(values) != len(sort):
        raise InvalidCursor(
            f"cursor has {len(values)} value(s) but the sort has {len(sort)} field(s); "
            "the sort must match the one that produced the cursor",
            param="after",
        )
    branches: list[dict[str, Any]] = []
    for index, (column, direction) in enumerate(sort):
        clause: dict[str, Any] = {sort[j][0]: values[j] for j in range(index)}
        clause[column] = {"$gt" if direction == 1 else "$lt": values[index]}
        branches.append(clause)
    return branches[0] if len(branches) == 1 else {"$or": branches}
