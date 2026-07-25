"""The parse result: a filter, a projection, a sort, and a page window."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .cursor import Cursors
from .errors import InvalidCursor


@dataclass(frozen=True)
class Query:
    """Everything needed to run the request against a collection.

    ``filter`` is a plain Mongo query document, so it composes with anything you already have::

        collection.find({"$and": [tenant_scope, query.filter]})
    """

    filter: dict[str, Any] = field(default_factory=dict)
    sort: list[tuple[str, int]] = field(default_factory=list)
    projection: dict[str, int] | None = None
    skip: int = 0
    limit: int = 25
    page: int = 1
    per_page: int = 25
    cursors: Cursors | None = None

    def find_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for ``collection.find(**query.find_kwargs())``."""
        kwargs: dict[str, Any] = {"filter": self.filter, "skip": self.skip, "limit": self.limit}
        if self.sort:
            kwargs["sort"] = self.sort
        if self.projection:
            kwargs["projection"] = self.projection
        return kwargs

    def pipeline(self) -> list[dict[str, Any]]:
        """The same query as aggregation stages, for when you need to $lookup afterwards."""
        stages: list[dict[str, Any]] = [{"$match": self.filter}]
        if self.sort:
            stages.append({"$sort": dict(self.sort)})
        if self.skip:
            stages.append({"$skip": self.skip})
        stages.append({"$limit": self.limit})
        if self.projection:
            stages.append({"$project": self.projection})
        return stages

    def next_cursor(self, last_document: dict | None) -> str | None:
        """Cursor that resumes after ``last_document`` — the final document of this page.

        Returns None for an empty page, which is also how a client knows it has reached the end.
        Note that the cursor encodes the sort fields, so the next request must repeat the same
        ``sort``; sending a different one is rejected rather than silently paginating nonsense.
        """
        if self.cursors is None:
            raise InvalidCursor("cursor support is not enabled; pass cursors=Cursors(...) to parse()")
        if not last_document:
            return None
        return self.cursors.encode(last_document, self.sort)
