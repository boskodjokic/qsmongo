# Changelog

## 0.4.1

**Fixed**

- `analyze` reported a collection scan for a query with no predicates, even when an index provided
  exactly its sort. `parse("sort=-created_at")` against `Index([("created_at", -1)])` returned
  `ok=False` and suggested creating the index that already existed. An index was only treated as a
  candidate when its leading key was pinned by an equality predicate or narrowed by a range; one
  that earns its keep purely by providing the order was never considered. That is the shape of
  every "latest 25" listing, so the most common list query got the worst advice.

## 0.4.0

**Added**

- `FilterBuilder` — the parsing pipeline without the URL grammar. Feed it
  `(field, op, raw_value)` triples from any source and get the same whitelist, type coercion,
  escaped patterns and conflict detection that `parse()` provides:

      FilterBuilder(schema).add("age", "gte", "21").add("name", "contains", "ada").build()

  `parse()` is now implemented on top of it, so both front-ends share one implementation.
  `add()` takes an optional `param` naming the caller's own parameter in error messages.

## 0.3.1

**Fixed**

- A cursor reused with a *different* sort of the same length was accepted, and each value was
  compared against the wrong field — `sort=-score` then `sort=name` produced `{"name": {"$gt": 5}}`
  with no error. Cursors now record the sort that issued them and `decode` rejects a mismatch with
  `InvalidCursor`. Only a differing key *count* was caught before.

**Changed**

- Cursor payload format: `{"s": [[column, direction], ...], "v": [...]}` instead of a bare value
  list. Cursors issued by 0.3.0 do not decode under 0.3.1 and raise `InvalidCursor`.
- `Cursors.decode(token)` now takes the expected sort: `Cursors.decode(token, sort)`. `parse()` and
  `Query.next_cursor()` are unaffected.

## 0.3.0

Initial release.

- Query string → MongoDB filter, validated against a declared `Schema`.
- Strict type coercion, operator suffixes, `multi` fields, aliases.
- Projection via `?fields`, whitelisted per field.
- Escaped substring operators (`contains`/`startswith`/`endswith`); raw `regex` opt-in.
- Keyset pagination with opaque, optionally HMAC-signed cursors.
- ESR index advisor (`analyze`).
