"""Run the cases of the registry format against the public reader of Kleisis."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from kleisis.registry import RegistryError, read_registry

CASES: list[dict[str, Any]] = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "specs" / "cases" / "registries.yml").read_text(
        encoding="utf-8"
    )
)["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_case(case: dict[str, Any], tmp_path: Path) -> None:
    """The reader gives the expected values, or rejects the file under the expected rule."""
    path = tmp_path / "registry.yml"
    path.write_text(case["document"], encoding="utf-8")
    expect = case["expect"]
    if "error" in expect:
        with pytest.raises(RegistryError) as caught:
            read_registry(path)
        assert [finding.rule.id for finding in caught.value.findings] == [expect["error"]]
        return
    registry = read_registry(path)
    assert {
        "version": registry.schema_version,
        "schemas": {key: value.as_posix() for key, value in registry.schemas.items()},
        "exports": list(registry.exports),
        "resources": {key: value.as_posix() for key, value in registry.resources.items()},
    } == expect
