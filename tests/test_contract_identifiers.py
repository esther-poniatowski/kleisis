"""Run the cases of the grammar of a contract identifier against the module and the schema."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from kleisis.contracts import (
    PATTERN,
    ContractIdentifier,
    is_contract_identifier,
    parse_contract_identifier,
)

ROOT = Path(__file__).resolve().parents[1]
CASES: dict[str, Any] = yaml.safe_load(
    (ROOT / "specs" / "cases" / "contract-identifiers.yml").read_text(encoding="utf-8")
)
SCHEMA: dict[str, Any] = json.loads(
    (ROOT / "specs" / "schemas" / "contract-identifier.schema.json").read_text(encoding="utf-8")
)


def _schema_accepts(text: str) -> bool:
    """Apply the pattern of the schema as JSON Schema applies it: a search, `$` at the very end.

    ECMA-262 lets `$` match only at the end of the input, whereas Python also matches it before a
    final line break, so the translation writes `\\Z`; named groups take the Python form.
    """
    pattern = SCHEMA["pattern"].replace("(?<", "(?P<").removesuffix("$") + r"\Z"
    return re.search(pattern, text) is not None


@pytest.mark.parametrize("case", CASES["valid"], ids=[c["identifier"] for c in CASES["valid"]])
def test_valid(case: dict[str, str]) -> None:
    """The module and the schema accept each valid identifier, and the parts rebuild it."""
    identifier = case["identifier"]
    assert is_contract_identifier(identifier)
    assert str(parse_contract_identifier(identifier)) == identifier
    assert _schema_accepts(identifier)


@pytest.mark.parametrize(
    "case", CASES["invalid"], ids=[repr(c["identifier"]) for c in CASES["invalid"]]
)
def test_invalid(case: dict[str, str]) -> None:
    """The module and the schema reject each invalid identifier."""
    identifier = case["identifier"]
    assert not is_contract_identifier(identifier)
    with pytest.raises(ValueError):
        parse_contract_identifier(identifier)
    assert not _schema_accepts(identifier)


def test_schema_pattern_equals_module_pattern() -> None:
    """The schema publishes the pattern of the module, in the syntax of named groups of ECMA-262."""
    assert SCHEMA["pattern"] == PATTERN.replace("(?P<", "(?<")


def test_non_string_is_rejected() -> None:
    """A value that is not a string is no identifier."""
    assert not is_contract_identifier(2)
    with pytest.raises(ValueError):
        parse_contract_identifier(None)


def test_parts() -> None:
    """The parts of an identifier are its namespace, its name and its version."""
    assert parse_contract_identifier("gnomon:view.next-act@8") == ContractIdentifier(
        "gnomon", "view.next-act", 8
    )
