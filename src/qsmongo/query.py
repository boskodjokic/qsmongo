"""The parse result: a filter, a sort, and a page window."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Query:
    """Everything needed to run the request against a collection.

    ``filter`` is a plain Mongo query document, so it composes with anything you already have::

        collection.find({"$and": [tenant_scope, query.filter]})
    """

    filter: dict[str, Any] = field(default_factory=dict)
    sort: list[tuple[str, int]] = field(default_factory=list)
    skip: int = 0
    limit: int = 25
    page: int = 1
    per_page: int = 25

    def find_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for ``collection.find(**query.find_kwargs())``."""
        kwargs: dict[str, Any] = {"filter": self.filter, "skip": self.skip, "limit": self.limit}
        if self.sort:
            kwargs["sort"] = self.sort
        return kwargs

    def pipeline(self) -> list[dict[str, Any]]:
        """The same query as aggregation stages, for when you need to $lookup afterwards."""
        stages: list[dict[str, Any]] = [{"$match": self.filter}]
        if self.sort:
            stages.append({"$sort": dict(self.sort)})
        if self.skip:
            stages.append({"$skip": self.skip})
        stages.append({"$limit": self.limit})
        return stages
