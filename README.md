# qsmongo

[![CI](https://github.com/boskodjokic/qsmongo/actions/workflows/ci.yml/badge.svg)](https://github.com/boskodjokic/qsmongo/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Turn an HTTP query string into a safe, typed MongoDB filter.

```
GET /users?age__gte=21&status__in=active,trial&sort=-created_at&page=2&per_page=50
```

```python
from qsmongo import Field, Schema, parse

schema = Schema(
    name=Field(str),
    age=Field(int),
    status=Field(str),
    created_at=Field(datetime, alias="audit.created"),
)

query = parse(request.url.query, schema)

query.filter  # {'age': {'$gte': 21}, 'status': {'$in': ['active', 'trial']}}
query.sort  # [('audit.created', -1)]
query.skip, query.limit  # 50, 50

collection.find(**query.find_kwargs())
```

No dependencies. Python 3.10+.

## Why

Every REST API over MongoDB grows the same ad-hoc filter parser, and it goes wrong in the same
four ways:

1. **Type mismatch.** `?age=21` gives you the string `"21"`. Mongo does not match `21` against
   `"21"`, so the endpoint returns an empty list and nobody can tell whether the data is missing or
   the filter is broken.
2. **Operator injection.** Passing user input into a query document unfiltered means a crafted
   parameter can inject `$where` or `$ne` and walk straight past your access checks.
3. **Separator collisions.** A product genuinely named `Dolce & Gabbana`, a colour called
   `black and white`, a tag containing a comma — each one quietly truncates the filter.
4. **Unbounded pages.** `?per_page=100000` is a denial-of-service vector you shipped yourself.

`qsmongo` handles all four in about 300 lines, with the failure modes as named exceptions you can
map onto HTTP 400.

## Install

```bash
pip install qsmongo
```

## The grammar

| Query string | Filter |
| --- | --- |
| `name=Ada` | `{"name": "Ada"}` |
| `age__gte=21` | `{"age": {"$gte": 21}}` |
| `age__gte=21&age__lt=65` | `{"age": {"$gte": 21, "$lt": 65}}` |
| `status__in=active,trial` | `{"status": {"$in": ["active", "trial"]}}` |
| `name__regex=^Ad` | `{"name": {"$regex": "^Ad"}}` |
| `deleted_at__exists=false` | `{"deleted_at": {"$exists": False}}` |
| `sort=-created_at,name` | `[("created_at", -1), ("name", 1)]` |
| `page=2&per_page=50` | `skip=50, limit=50` |

Operators: `ne` `gt` `gte` `lt` `lte` `in` `nin` `regex` `exists`. No suffix means equality.

## Declaring fields

```python
Schema(
    name=Field(str),  # str defaults to eq/ne/in/nin/regex/exists
    age=Field(int),  # numbers also get gt/gte/lt/lte
    email=Field(str, alias="contact.email"),  # public name differs from the document field
    tag=Field(str, multi=True),  # ?tag=red&tag=blue -> $in
    secret=Field(str, ops={"eq"}, sortable=False),
)
```

A field that is not declared cannot be queried, cannot be sorted on, and cannot be smuggled in as
an operator — `parse` raises `UnknownField` instead. That whitelist *is* the security model.

## Safety

- **Keys** are matched against the schema, so `$where=...` or `age__$gt=...` never reach the driver.
- **Values** are only ever coerced to the declared scalar type. Nothing is `eval`'d or parsed as
  JSON, so a value of `{"$ne": null}` stays the harmless nine-character string it is.
- **`per_page`** is clamped to `max_per_page` (default 100), so a client cannot ask for the
  collection.
- **`regex`** patterns are length-capped, and the operator is opt-in per field.

## Errors

All inherit `QSMongoError` (a `ValueError`) and carry the offending `.param`:

`UnknownField` · `UnsupportedOperator` · `InvalidValue` · `InvalidPagination`

With FastAPI:

```python
from fastapi import HTTPException
from qsmongo import QSMongoError, parse

try:
    query = parse(request.url.query, schema)
except QSMongoError as exc:
    raise HTTPException(status_code=400, detail={"parameter": exc.param, "error": str(exc)})
```

## Edge cases, on purpose

These are covered in [`tests/test_edge_cases.py`](tests/test_edge_cases.py):

- `title=black and white` — the word `and` is a value, not a keyword.
- `title=Dolce%20%26%20Gabbana` — an encoded `&` stays inside the value.
- `title=Dolce & Gabbana` — an *unencoded* `&` cannot be recovered by any parser, so it raises
  rather than silently searching for `Dolce`.
- `tag__in=red\,blue,green` — a backslash escapes a comma inside a list.
- `title=black+white` — `+` decodes to a space, per form encoding.
- `age=` — a blank value on a typed field is an error, not `0`.

## Composing with your own scope

`query.filter` is a plain dict, so multi-tenant scoping stays yours:

```python
collection.find({"$and": [{"tenant_id": user.tenant_id}, query.filter]})
```

Or use the aggregation form when you need `$lookup` afterwards:

```python
collection.aggregate([*query.pipeline(), {"$lookup": {...}}])
```

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## License

MIT
