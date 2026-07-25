"""Field declarations — the whitelist that makes parsing safe.

Nothing reaches MongoDB unless it was declared here first. That is the whole security model:
an undeclared field is an error, never a pass-through.
"""

from __future__ import annotations

from datetime import date, datetime

from .errors import UnknownField

try:  # pragma: no cover - exercised only when pymongo is installed
    from bson import ObjectId
except ImportError:  # pragma: no cover
    ObjectId = None

COMPARISON_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "in", "nin", "exists"})
EQUALITY_OPS = frozenset({"eq", "ne", "in", "nin", "exists"})
# Substring matching is offered as three escaped, anchored operators rather than raw regex.
TEXT_MATCH_OPS = frozenset({"contains", "startswith", "endswith"})
# "regex" hands the pattern straight to Mongo, so it stays opt-in per field.
TEXT_OPS = EQUALITY_OPS | TEXT_MATCH_OPS
ALL_OPS = COMPARISON_OPS | TEXT_MATCH_OPS | {"regex"}

_DEFAULT_OPS: dict[type, frozenset[str]] = {
    str: TEXT_OPS,
    int: COMPARISON_OPS,
    float: COMPARISON_OPS,
    bool: frozenset({"eq", "ne", "exists"}),
    datetime: COMPARISON_OPS,
    date: COMPARISON_OPS,
}


class Field:
    """One queryable field.

    Args:
        type_: python type used to coerce incoming strings (str, int, float, bool, datetime, date, ObjectId).
        ops: operators this field accepts. Defaults to a sensible set for the type.
        sortable: whether the field may appear in the sort parameter.
        multi: when True, a repeated key (``tag=a&tag=b``) collapses to ``$in`` instead of erroring.
        alias: the document field name, when it differs from the name exposed in the API.
        projectable: whether the field may be requested in the ``fields`` parameter.
        case_sensitive: applies to contains/startswith/endswith. Case-insensitive matching cannot
            use an ordinary index, so a case-sensitive ``startswith`` is the only substring
            operator that stays fast on a large collection.

    A field declared with ``ops=set()`` is not queryable at all — useful when you only want it to
    be selectable via ``fields``.
    """

    __slots__ = ("type", "ops", "sortable", "multi", "alias", "projectable", "case_sensitive")

    def __init__(
        self,
        type_: type,
        *,
        ops: set[str] | frozenset[str] | None = None,
        sortable: bool = True,
        multi: bool = False,
        alias: str | None = None,
        projectable: bool = True,
        case_sensitive: bool = False,
    ):
        if ops is None:
            ops = _DEFAULT_OPS.get(type_)
            if ops is None:
                ops = EQUALITY_OPS  # ObjectId and anything custom
        unknown = set(ops) - ALL_OPS
        if unknown:
            raise ValueError(f"unknown operator(s) for field: {sorted(unknown)}")
        if type_ is not str and (set(ops) & (TEXT_MATCH_OPS | {"regex"})):
            raise ValueError(f"text operators require a str field, got {type_.__name__}")
        self.type = type_
        self.ops = frozenset(ops)
        self.sortable = sortable
        self.multi = multi
        self.alias = alias
        self.projectable = projectable
        self.case_sensitive = case_sensitive

    def __repr__(self) -> str:
        return f"Field({self.type.__name__}, ops={sorted(self.ops)}, multi={self.multi})"


class Schema:
    """A whitelist of queryable fields.

    >>> Schema(name=Field(str), age=Field(int))
    """

    __slots__ = ("fields",)

    def __init__(self, **fields: Field):
        for name, field in fields.items():
            if not isinstance(field, Field):
                raise TypeError(f"{name!r} must be a Field, got {type(field).__name__}")
        self.fields = fields

    def get(self, name: str) -> Field:
        try:
            return self.fields[name]
        except KeyError:
            raise UnknownField(f"unknown field {name!r}", param=name) from None

    def column(self, name: str) -> str:
        """Document field name for an API-facing field name."""
        return self.get(name).alias or name

    def __contains__(self, name: object) -> bool:
        return name in self.fields

    def __repr__(self) -> str:
        return f"Schema({', '.join(self.fields)})"
