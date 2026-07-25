# Changelog

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
