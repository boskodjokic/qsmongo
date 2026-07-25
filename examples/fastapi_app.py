"""A complete FastAPI endpoint built on qsmongo.

Run it:

    pip install qsmongo fastapi uvicorn
    uvicorn examples.fastapi_app:app --reload

Then try:

    curl 'localhost:8000/products?price__gte=50&price__lt=200&status__in=live,draft&sort=-created_at'
    curl 'localhost:8000/products?password=admin'          # 400, unknown field
    curl 'localhost:8000/products?price__gte=cheap'         # 400, bad value
    curl 'localhost:8000/products?%24where=1'               # 400, injection rejected

Set MONGODB_URL to run the query against a real collection; without it the endpoint returns the
filter it built, which is the interesting part anyway.
"""

import os
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Request

from qsmongo import Field, QSMongoError, Schema, parse

app = FastAPI(title="qsmongo example")

# One declaration per resource. Adding a filterable field is a one-line change here and
# nothing else in the endpoint moves.
PRODUCT_QUERY = Schema(
    name=Field(str),
    sku=Field(str, ops={"eq", "in"}),
    price=Field(float),
    status=Field(str, multi=True),
    in_stock=Field(bool),
    created_at=Field(datetime, alias="audit.created"),
    internal_cost=Field(float, sortable=False, ops={"eq"}),
)


def get_collection():
    """Return a real collection when MONGODB_URL is set, otherwise None."""
    url = os.environ.get("MONGODB_URL")
    if not url:
        return None
    from pymongo import MongoClient  # imported lazily so the example runs without pymongo

    return MongoClient(url)["shop"]["products"]


def current_tenant() -> str:
    """Stand-in for the tenant claim you would read off the caller's JWT."""
    return "acme"


@app.get("/products")
def list_products(request: Request, tenant_id: str = Depends(current_tenant)):
    try:
        query = parse(request.url.query, PRODUCT_QUERY, max_per_page=100, ignore_unknown=False)
    except QSMongoError as exc:
        # Every failure names the parameter that caused it, so the client gets a useful 400.
        raise HTTPException(status_code=400, detail={"parameter": exc.param, "error": str(exc)}) from exc

    # The caller's own scoping stays the caller's responsibility: qsmongo never invents clauses.
    scoped = {"$and": [{"tenant_id": tenant_id}, query.filter]} if query.filter else {"tenant_id": tenant_id}

    collection = get_collection()
    if collection is None:
        return {"filter": scoped, "sort": query.sort, "skip": query.skip, "limit": query.limit}

    total = collection.count_documents(scoped)
    items = list(collection.find(scoped, skip=query.skip, limit=query.limit, sort=query.sort or None))
    for item in items:
        item["_id"] = str(item["_id"])
    return {"items": items, "total": total, "page": query.page, "per_page": query.per_page}
