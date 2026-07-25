"""qsmongo — turn an HTTP query string into a safe MongoDB filter.

from qsmongo import Field, Schema, parse

schema = Schema(name=Field(str), age=Field(int))
query = parse("age__gte=21&sort=-age&page=2", schema)
collection.find(**query.find_kwargs())
"""

from .advisor import Index, IndexAdvice, analyze
from .coerce import coerce
from .cursor import Cursors
from .errors import (
    InvalidCursor,
    InvalidPagination,
    InvalidProjection,
    InvalidValue,
    QSMongoError,
    UnknownField,
    UnsupportedOperator,
)
from .parser import parse
from .query import Query
from .schema import Field, Schema

__version__ = "0.3.0"

__all__ = [
    "Cursors",
    "Field",
    "Index",
    "IndexAdvice",
    "InvalidCursor",
    "InvalidPagination",
    "InvalidProjection",
    "InvalidValue",
    "QSMongoError",
    "Query",
    "Schema",
    "UnknownField",
    "UnsupportedOperator",
    "analyze",
    "coerce",
    "parse",
]
