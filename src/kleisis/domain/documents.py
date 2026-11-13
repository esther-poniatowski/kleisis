"""Strict loading of YAML and JSON documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

MERGE_TAG = "tag:yaml.org,2002:merge"


class UniqueKeyLoader(yaml.SafeLoader):
    """A safe YAML loader that rejects duplicate mapping keys and merge keys."""


def _unique_mapping(
    loader: UniqueKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        if key_node.tag == MERGE_TAG:
            # A merge key belongs to YAML 1.1: a YAML 1.2 reader takes `<<` as an ordinary key, so
            # the document would state different keys to different readers.
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found a merge key `<<`, which a YAML 1.2 reader reads as an ordinary key; "
                "write the merged keys out",
                key_node.start_mark,
            )
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _unique_mapping,
)


def load_document(path: Path) -> Any:
    """Load JSON strictly and YAML without duplicate mapping keys or merge keys."""
    if path.suffix.lower() == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
    else:
        # Reading from the open file lets each YAML error name the file in its position.
        with path.open(encoding="utf-8") as stream:
            value = yaml.load(stream, Loader=UniqueKeyLoader)
    json.dumps(value, allow_nan=False)
    return value
