"""Query string -> :class:`~qsmongo.query.Query`.

Grammar::

    field=value               equality
    field__<op>=value         ne gt gte lt lte in nin exists contains startswith endswith regex
    field__in=a,b,c           comma separated list, "\\," escapes a literal comma
    fields=name,price         projection (or fields=-secret to exclude)
    sort=-created_at,name     "-" prefix means descending
    page=2&per_page=50        offset paging, clamped to max_per_page
    after=<cursor>&per_page=50  keyset paging, mutually exclusive with page

Field names are matched against a :class:`~qsmongo.schema.Schema`; anything undeclared raises.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl

from .coerce import coerce
from .cursor import Cursors, keyset_filter
from .errors import (
    InvalidCursor,
    InvalidPagination,
    InvalidProjection,
    InvalidValue,
    UnsupportedOperator,
)
from .query import Query
from .schema import Schema

OP_SUFFIXES = {
    "ne": "$ne",
    "gt": "$gt",
    "gte": "$gte",
    "lt": "$lt",
    "lte": "$lte",
    "in": "$in",
    "nin": "$nin",
    "regex": "$regex",
    "exists": "$exists",
    "contains": "$regex",
    "startswith": "$regex",
    "endswith": "$regex",
}
LIST_OPS = frozenset({"in", "nin"})
MAX_REGEX_LENGTH = 200


def _split_list(raw: str) -> list[str]:
    """Split on commas, honouring a backslash escape so values may contain a literal comma."""
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for char in raw:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ",":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped:  # trailing backslash is a literal backslash
        current.append("\\")
    parts.append("".join(current))
    return [p for p in parts if p != ""]


def _split_key(key: str) -> tuple[str, str]:
    """``"age__gte"`` -> ``("age", "gte")``. A field with no known suffix is an equality match."""
    if "__" in key:
        field, _, suffix = key.rpartition("__")
        if field and suffix in OP_SUFFIXES:
            return field, suffix
    return key, "eq"


def _positive_int(raw: str, param: str) -> int:
    try:
        value = int(raw.strip())
    except ValueError:
        raise InvalidPagination(f"{param}: expected a positive integer, got {raw!r}", param=param) from None
    if value < 1:
        raise InvalidPagination(f"{param}: must be 1 or greater, got {value}", param=param)
    return value


def _parse_sort(raw: str, schema: Schema) -> list[tuple[str, int]]:
    order: list[tuple[str, int]] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        direction = 1
        if token.startswith("-"):
            direction = -1
            token = token[1:]
        field_def = schema.get(token)  # raises UnknownField
        if not field_def.sortable:
            raise UnsupportedOperator(f"field {token!r} is not sortable", param=token)
        order.append((schema.column(token), direction))
    return order


def _parse_projection(raw: str, schema: Schema) -> dict[str, int]:
    """``"name,price"`` -> ``{"name": 1, "price": 1}``; ``"-secret"`` -> ``{"secret": 0}``."""
    include: dict[str, int] = {}
    exclude: dict[str, int] = {}
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        excluded = token.startswith("-")
        if excluded:
            token = token[1:]
        field_def = schema.get(token)  # raises UnknownField
        if not field_def.projectable:
            raise UnsupportedOperator(f"field {token!r} cannot be selected", param=token)
        (exclude if excluded else include)[schema.column(token)] = 0 if excluded else 1
    if include and exclude:
        # Mongo rejects mixed projections (only _id may be excluded alongside inclusions), so
        # catching it here gives a 400 instead of an OperationFailure from the driver.
        raise InvalidProjection("fields cannot mix included and excluded names; use one or the other", param="fields")
    return include or exclude


def _text_clause(field_def, op: str, raw: str, param: str) -> dict[str, Any]:
    """contains/startswith/endswith as an escaped pattern — user input is never a pattern."""
    if not raw:
        raise InvalidValue(f"{param}: expected a non-empty value", param=param)
    escaped = re.escape(raw)
    pattern = {"contains": escaped, "startswith": f"^{escaped}", "endswith": f"{escaped}$"}[op]
    clause: dict[str, Any] = {"$regex": pattern}
    if not field_def.case_sensitive:
        clause["$options"] = "i"
    return clause


def _build_clause(field_def, op: str, raw: str, param: str) -> dict[str, Any]:
    """Everything except equality, as the operator document Mongo expects."""
    if op == "exists":
        return {"$exists": coerce(bool, raw, param)}
    if op in LIST_OPS:
        values = [coerce(field_def.type, item, param) for item in _split_list(raw)]
        if not values:
            raise InvalidValue(f"{param}: expected at least one value", param=param)
        return {OP_SUFFIXES[op]: values}
    if op in ("contains", "startswith", "endswith"):
        return _text_clause(field_def, op, raw, param)
    if op == "regex":
        if len(raw) > MAX_REGEX_LENGTH:
            raise InvalidValue(f"{param}: pattern exceeds {MAX_REGEX_LENGTH} characters", param=param)
        return {"$regex": raw}
    return {OP_SUFFIXES[op]: coerce(field_def.type, raw, param)}


def parse(
    query_string: str,
    schema: Schema,
    *,
    default_per_page: int = 25,
    max_per_page: int = 100,
    cursors: Cursors | None = None,
    page_param: str = "page",
    per_page_param: str = "per_page",
    sort_param: str = "sort",
    fields_param: str = "fields",
    after_param: str = "after",
    ignore_unknown: bool = False,
) -> Query:
    """Parse ``query_string`` into a :class:`Query` validated against ``schema``.

    Pass ``cursors`` to enable keyset pagination via ``?after=``; without it the parameter is
    rejected. Set ``ignore_unknown`` to skip undeclared parameters instead of raising — useful when
    the same URL carries parameters your view consumes (``?include=...``, cache busters).
    """
    pairs = parse_qsl(query_string.lstrip("?"), keep_blank_values=True)

    equality: dict[str, tuple[str, list[Any]]] = {}
    operators: dict[str, dict[str, Any]] = {}
    sort: list[tuple[str, int]] = []
    projection: dict[str, int] = {}
    page: int | None = None
    per_page = default_per_page
    after: str | None = None

    for raw_key, raw_value in pairs:
        key = raw_key.strip()
        if key == page_param:
            page = _positive_int(raw_value, page_param)
            continue
        if key == per_page_param:
            per_page = min(_positive_int(raw_value, per_page_param), max_per_page)
            continue
        if key == sort_param:
            sort.extend(_parse_sort(raw_value, schema))
            continue
        if key == fields_param:
            projection.update(_parse_projection(raw_value, schema))
            continue
        if key == after_param:
            after = raw_value
            continue

        field_name, op = _split_key(key)
        if ignore_unknown and field_name not in schema:
            continue
        field_def = schema.get(field_name)  # raises UnknownField
        if op not in field_def.ops:
            raise UnsupportedOperator(
                f"field {field_name!r} does not support {op!r} (allowed: {sorted(field_def.ops)})", param=key
            )

        column = schema.column(field_name)
        if op == "eq":
            equality.setdefault(column, (field_name, []))[1].append(coerce(field_def.type, raw_value, key))
            continue
        clause = _build_clause(field_def, op, raw_value, key)
        existing = operators.setdefault(column, {})
        duplicate = set(existing) & set(clause) - {"$options"}
        if duplicate:
            raise InvalidValue(
                f"{field_name!r} was given two {sorted(duplicate)[0]} clauses; combine them into one", param=key
            )
        existing.update(clause)

    filter_: dict[str, Any] = {}
    for column, (field_name, values) in equality.items():
        if len(values) == 1:
            filter_[column] = values[0]
        else:
            if not schema.get(field_name).multi:
                raise InvalidValue(
                    f"{field_name!r} was given {len(values)} values; declare it as Field(..., multi=True) "
                    "to combine them into an $in",
                    param=field_name,
                )
            filter_[column] = {"$in": values}

    for column, clauses in operators.items():
        if column in filter_:
            raise InvalidValue(
                f"{column!r} has both an equality match and {sorted(clauses)}; use one or the other", param=column
            )
        filter_[column] = clauses

    # Every page, not just the ones reached by a cursor, needs a total order: two documents
    # sharing a sort value could otherwise straddle the boundary and be skipped or repeated.
    if cursors is not None and not any(column == cursors.id_field for column, _ in sort):
        sort = [*sort, (cursors.id_field, 1)]

    if after is not None:
        if page is not None:
            raise InvalidPagination(
                f"{after_param} and {page_param} are different pagination modes; send only one", param=after_param
            )
        if cursors is None:
            raise InvalidCursor(
                f"{after_param} is not enabled; pass cursors=Cursors(...) to parse()", param=after_param
            )
        keyset = keyset_filter(sort, cursors.decode(after))
        filter_ = {"$and": [filter_, keyset]} if filter_ else keyset

    resolved_page = page or 1
    return Query(
        filter=filter_,
        sort=sort,
        projection=projection or None,
        skip=0 if after is not None else (resolved_page - 1) * per_page,
        limit=per_page,
        page=resolved_page,
        per_page=per_page,
        cursors=cursors,
    )
