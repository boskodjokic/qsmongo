# Design note: OR support

**Status:** decided, not implemented. Revisit when a real user asks.
**Date:** 2026-07-25 · **Applies to:** qsmongo (Python) and the planned Java port.

## Context

Every predicate in qsmongo ANDs with every other. That is not an oversight — it is the property
the rest of the library is built on.

The parser accumulates into a flat map of column → clause:

```python
_equality[column] = (field, [values...])
_operators[column] = {"$gte": ..., "$lt": ...}
```

Flatness is what makes the guarantees cheap:

- range bounds on one field merge into a single document (`age__gte` + `age__lt`)
- an incoherent query is rejected (`age=21&age__gte=30`)
- two clauses fighting over the same operator are caught (`name__contains` + `name__startswith`)
- `analyze` can label each column equality-or-range and walk it against ESR

OR breaks the assumption that a column has one meaning per query, so it touches all four.

## Options considered

### A. Disjunctive normal form — an OR of AND-groups

Predicates carry a group label. Groups AND internally and OR with each other:

```
?or.a.status=live&or.a.price__gte=10&or.b.status=draft

{"$or": [{"status": "live", "price": {"$gte": 10}},
         {"status": "draft"}]}
```

Each group remains a flat map, so every guarantee above holds *within* a group, and cross-group
conflicts are meaningless by construction rather than undetected. Implementation is one
`FilterBuilder` per group, combined at the end — roughly 30 lines in `parse()`, given the 0.4.0
refactor that made the pipeline reachable independently of the URL grammar.

`or.` is a reserved prefix checked before field lookup, like `page` and `sort`.

Covers the real use case: faceted search, "active OR trialing", saved filter sets.

### B. A boolean expression grammar

`(status eq live and price gte 10) or (status eq draft)` — tokeniser, precedence, arbitrary-depth
tree. This is a different library. It destroys flat conflict detection entirely, turns index
advice into a query-planning problem, and lands in RSQL/FIQL territory where mature parsers
already exist.

**Rejected.** Competing there is not worth it, and the complexity would swallow the project's
main selling point.

### C. Do nothing

AND-only stays. Anyone needing OR composes it themselves — `query.filter` is a plain dict:

```python
collection.find({"$or": [{"$and": [scope, a.filter]}, {"$and": [scope, b.filter]}]})
```

## Decision

**C for now. A when someone asks. Never B.**

AND-only is a feature, not a gap: it is why the library is ~800 lines and why the guarantees are
cheap to state and to test. Adding OR speculatively would be the first change that makes qsmongo
meaningfully bigger without a user behind it.

## Consequences if A is implemented

| Area | Impact |
|---|---|
| `parse()` | reserved-prefix routing into per-group builders, ~30 lines |
| `FilterBuilder` | unchanged — construct several |
| Conflict detection | preserved within groups; no regression |
| Projection, sort, paging | untouched |
| Keyset cursors | still valid as `{"$and": [{"$or": user}, {"$or": keyset}]}`, but index efficiency degrades |
| **`analyze`** | **the bulk of the work** |
| README | the "AND only" simplicity claim has to go |

### Why the advisor is the expensive part

`_classify` currently folds any `$or` into `ranges`. That is a deliberate shortcut: today the only
`$or` that can exist is the keyset clause, whose branches are all on sort keys and read as a seek.

With user OR the shortcut is simply wrong. The correct model:

- a top-level `$or` needs **every** branch independently indexed, or it degrades to a collection
  scan — Mongo can union index scans, but only when each branch has one
- sorting across an `$or` usually forces a blocking sort unless a SORT_MERGE applies, so the
  in-memory-sort warning becomes both more common and harder to get right

So `IndexAdvice` must become per-branch rather than a single verdict — an API change to the second
public function. Estimate 100–150 lines with tests, against ~30 for the parsing.

Total: about a day, four-fifths of it in the advisor.

## Trigger to revisit

A user (issue, or a real endpoint that cannot be expressed) needing an OR of AND-groups. Not
"it would be more complete".
