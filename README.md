# qsmongo

[![CI](https://github.com/boskodjokic/qsmongo/actions/workflows/ci.yml/badge.svg)](https://github.com/boskodjokic/qsmongo/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Turn an HTTP query string into a MongoDB query that is **safe by construction** — nothing is
queryable until you declare it — and **fast by construction**: keyset pagination that costs the
same on page 10,000 as on page 1.

```
GET /users?age__gte=21&status__in=active,trial&fields=name,age&sort=-created_at&per_page=50
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
query.projection  # {'name': 1, 'age': 1}
query.sort  # [('audit.created', -1)]

collection.find(**query.find_kwargs())
```

No dependencies. Python 3.10+.

## Why

Every REST API over MongoDB grows the same ad-hoc filter parser, and it goes wrong in the same
five ways:

1. **Type mismatch.** `?age=21` gives you the string `"21"`. Mongo does not match `21` against
   `"21"`, so the endpoint returns an empty list and nobody can tell whether the data is missing or
   the filter is broken.
2. **Operator injection.** Passing user input into a query document unfiltered means a crafted
   parameter can inject `$where` or `$ne` and walk straight past your access checks.
3. **Separator collisions.** A product genuinely named `Dolce & Gabbana`, a colour called
   `black and white`, a tag containing a comma — each one quietly truncates the filter.
4. **Unbounded pages.** `?per_page=100000` is a denial-of-service vector you shipped yourself.
5. **Offset pagination.** `skip=100000` makes the server walk 100,000 documents it will never
   return, and when the sort key has ties, documents shift between pages and are skipped or served
   twice.

## Install

```bash
pip install qsmongo
```

## The grammar

| Query string | Result |
| --- | --- |
| `name=Ada` | `{"name": "Ada"}` |
| `age__gte=21` | `{"age": {"$gte": 21}}` |
| `age__gte=21&age__lt=65` | `{"age": {"$gte": 21, "$lt": 65}}` |
| `status__in=active,trial` | `{"status": {"$in": ["active", "trial"]}}` |
| `name__contains=ada` | `{"name": {"$regex": "ada", "$options": "i"}}` |
| `name__startswith=Ad` | `{"name": {"$regex": "^Ad", "$options": "i"}}` |
| `deleted_at__exists=false` | `{"deleted_at": {"$exists": False}}` |
| `fields=name,price` | `{"name": 1, "price": 1}` |
| `fields=-internal_note` | `{"internal_note": 0}` |
| `sort=-created_at,name` | `[("created_at", -1), ("name", 1)]` |
| `page=2&per_page=50` | `skip=50, limit=50` |
| `after=<cursor>&per_page=50` | keyset range clause, `skip=0` |

Operators: `ne` `gt` `gte` `lt` `lte` `in` `nin` `contains` `startswith` `endswith` `exists`
`regex`. No suffix means equality.

## Declaring fields

```python
Schema(
    name=Field(str),  # eq/ne/in/nin/exists + contains/startswith/endswith
    age=Field(int),  # numbers also get gt/gte/lt/lte
    email=Field(str, alias="contact.email"),  # public name differs from the document field
    tag=Field(str, multi=True),  # ?tag=red&tag=blue -> $in
    sku=Field(str, case_sensitive=True),  # index-friendly startswith
    internal_cost=Field(float, projectable=False),  # never selectable via ?fields
    description=Field(str, ops=set()),  # selectable, never queryable
    pattern=Field(str, ops={"eq", "regex"}),  # raw regex is opt-in
)
```

A field that is not declared cannot be queried, sorted on, projected, or smuggled in as an
operator — `parse` raises `UnknownField` instead. That whitelist *is* the security model.

## Keyset pagination

Offset paging is fine until it isn't. Pass a `Cursors` codec and clients can page by cursor:

```python
from qsmongo import Cursors, parse

cursors = Cursors(secret=settings.CURSOR_SECRET)  # HMAC-signed, so clients cannot forge one

query = parse(request.url.query, schema, cursors=cursors)
items = list(collection.find(**query.find_kwargs()))

return {"items": items, "next": query.next_cursor(items[-1] if items else None)}
```

The client sends that token back as `?after=<cursor>`, and the skip becomes a range clause:

```python
{"$or": [{"score": {"$lt": 42}}, {"score": 42, "_id": {"$gt": "abc"}}]}
```

Details that matter:

- **An `_id` tiebreaker is appended to every sort** when cursors are enabled, so the ordering is
  total. Without it, two documents sharing a `created_at` can straddle a page boundary and one of
  them is lost.
- **Cursors are signed** when you supply a secret, and the secret never appears in a `repr`.
  Unsigned, tampered, or truncated tokens raise `InvalidCursor`.
- **The sort must stay the same between pages.** Changing it raises rather than silently
  paginating nonsense.
- `after` and `page` are different modes; sending both is an error.
- Forward-only. Backward paging is not implemented.

[`tests/test_keyset_property.py`](tests/test_keyset_property.py) walks a dataset engineered so that
every page boundary lands in the middle of a tie, and asserts the pages reconstruct the full
ordering exactly — no document skipped, none repeated.

## Index advice

A query that parses cleanly can still be a collection scan. `analyze` checks the query against your
declared indexes using MongoDB's ESR ordering — **E**quality keys first, then **S**ort keys, then
**R**ange keys — and suggests one when nothing fits:

```python
from qsmongo import Index, analyze

INDEXES = Index.from_index_information(collection.index_information())

advice = analyze(query, INDEXES, extra_equality=["tenant_id"])
if not advice.ok:
    log.warning("unindexed query\n%s", advice)
```

```
no index serves this query
  - {status: 1, price: 1, audit.created: -1} filters this query but does not provide the sort
    {audit.created: -1}, so MongoDB sorts in memory (it aborts past its blocking-sort memory
    limit, 100 MB by default)
  suggested index: {status: 1, audit.created: -1, price: 1}
```

That example is the classic mistake: `{status, price, created_at}` looks sensible, but the range on
`price` sits between the equality key and the sort key, so the index stops providing the ordering.
Swapping the last two fixes it, and nothing about the query itself changes.

It also flags the predicates that cannot use an index at all — case-insensitive or unanchored
regex, `$ne`/`$nin`, `$exists: false` — reports covered queries, and understands that a sort on a
field pinned by an equality match is free. `extra_equality` is for the clauses your own code adds
(a tenant id, a soft-delete flag): they belong at the front of the index, and the advice is wrong
without them.

**This is a lint, not a query planner.** It reasons about the query shape, not your data
distribution or the real plan cache. `explain()` remains the only ground truth; this is here to
catch the obvious problems in CI or a dev-mode log, before they reach a database.

## Safety

- **Keys** are matched against the schema, so `$where=...` or `age__$gt=...` never reach the driver.
- **Values** are only ever coerced to the declared scalar type. Nothing is `eval`'d or parsed as
  JSON, so a value of `{"$ne": null}` stays the harmless nine-character string it is.
- **Substring search is escaped.** `contains` / `startswith` / `endswith` run the value through
  `re.escape` and anchor it, so `?name__contains=.*` looks for a literal `.*`. Raw `regex` is
  opt-in per field and length-capped.
- **`per_page`** is clamped to `max_per_page` (default 100), so a client cannot ask for the
  collection.
- **Projection is whitelisted**, so a field marked `projectable=False` cannot be selected even
  when it is filterable.

Case-insensitive matching cannot use an ordinary index. `case_sensitive=True` drops the `i` option,
which is what makes `startswith` an index-friendly prefix scan on a large collection.

## Errors

All inherit `QSMongoError` (a `ValueError`) and carry the offending `.param`:

`UnknownField` · `UnsupportedOperator` · `InvalidValue` · `InvalidPagination` · `InvalidCursor` ·
`InvalidProjection`

```python
try:
    query = parse(request.url.query, schema, cursors=cursors)
except QSMongoError as exc:
    raise HTTPException(status_code=400, detail={"parameter": exc.param, "error": str(exc)})
```

See [`examples/fastapi_app.py`](examples/fastapi_app.py) for a complete endpoint.

## Edge cases, on purpose

Covered in [`tests/test_edge_cases.py`](tests/test_edge_cases.py):

- `title=black and white` — the word `and` is a value, not a keyword.
- `title=Dolce%20%26%20Gabbana` — an encoded `&` stays inside the value.
- `title=Dolce & Gabbana` — an *unencoded* `&` cannot be recovered by any parser, so it raises
  rather than silently searching for `Dolce`.
- `tag__in=red\,blue,green` — a backslash escapes a comma inside a list.
- `title=black+white` — `+` decodes to a space, per form encoding.
- `age=` — a blank value on a typed field is an error, not `0`.

## Composing with your own scope

`query.filter` is a plain dict, so multi-tenant scoping stays yours — the library never invents
clauses on your behalf:

```python
collection.find({"$and": [{"tenant_id": user.tenant_id}, query.filter]})
```

Or use the aggregation form when you need `$lookup` afterwards:

```python
collection.aggregate([*query.pipeline(), {"$lookup": {...}}])
```

## Prior art

[`mongo-queries-manager`](https://pypi.org/project/mongo-queries-manager/) solves the same problem
and has more features — projection with `$elemMatch`, `$text`, custom casters. It takes the
opposite default: every field is queryable unless blacklisted, and types are inferred from the
value (`5.6` becomes a float, `true` becomes a bool).

Use it if you want breadth, or don't want to declare a schema.

Use `qsmongo` if you want an undeclared field to be an error rather than a silent leak, a numeric
SKU like `01234` to stay the string it is in your documents, and cursor pagination.
[`fastapi-filter`](https://pypi.org/project/fastapi-filter/) is also whitelist-based via pydantic
models, but is coupled to FastAPI and an ODM; `qsmongo` takes a string and returns a dict.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## License

MIT
