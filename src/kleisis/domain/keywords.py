"""Keywords `x-` of schemas: their declarations, and their uses at the positions of a schema.

A tool that gives a meaning to an `x-` keyword declares it in its configuration, and Kleisis
writes the declaration into the generated registry under `keywords`. A consumer reads the
declarations of its dependencies from their registries. Each keyword therefore has one declaring
source, and a schema uses only keywords that its repository or a dependency declares.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from kleisis.findings import pointer

KEYWORD = re.compile(r"x-[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
"""Form of a keyword name: `x-` followed by words in lower kebab case."""

_SCHEMA_VALUED = frozenset(
    {
        "additionalItems",
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)
"""Keywords of JSON Schema whose value is one schema, or a list of schemas for `items`."""

_SCHEMA_LISTS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
"""Keywords of JSON Schema whose value is a list of schemas."""

_SCHEMA_MAPS = frozenset(
    {"$defs", "definitions", "dependentSchemas", "patternProperties", "properties"}
)
"""Keywords of JSON Schema whose value maps names to schemas, never to keywords."""


def used_keywords(schema: Any) -> Iterator[tuple[str, str]]:
    """Yield each `x-` keyword of ``schema`` at a position of a schema, with its JSON Pointer.

    A key of `properties` or of another map of schemas names a property, not a keyword, and the
    value of a keyword outside JSON Schema, such as an `x-` keyword or `const`, is data. Neither is
    searched.

    Examples
    --------
    >>> schema = {"x-kind": "note", "properties": {"x-name": {"x-label": "name"}}}
    >>> list(used_keywords(schema))
    [('x-kind', '/x-kind'), ('x-label', '/properties/x-name/x-label')]
    """
    yield from _walk(schema, ())


def _walk(node: Any, path: tuple[str | int, ...]) -> Iterator[tuple[str, str]]:
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        here = (*path, key)
        if isinstance(key, str) and key.startswith("x-"):
            yield key, pointer(*here)
        elif key in _SCHEMA_VALUED:
            if isinstance(value, list):
                for index, item in enumerate(value):
                    yield from _walk(item, (*here, index))
            else:
                yield from _walk(value, here)
        elif key in _SCHEMA_LISTS and isinstance(value, list):
            for index, item in enumerate(value):
                yield from _walk(item, (*here, index))
        elif key in _SCHEMA_MAPS and isinstance(value, dict):
            for name, item in value.items():
                yield from _walk(item, (*here, name))
        elif key == "dependencies" and isinstance(value, dict):
            for name, item in value.items():
                yield from _walk(item, (*here, name))
