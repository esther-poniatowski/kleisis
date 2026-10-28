"""Tests for transaction recovery and rollback evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from schema_closure.errors import RegistryError
from schema_closure.infrastructure.publication import (
    TRANSACTION_FILE,
    publish_pair,
    recover_transaction,
)


def _targets(root: Path) -> tuple[Path, Path]:
    resources = root / ".schema-resources"
    registry = root / "specs/schema-registry.yml"
    resources.mkdir(parents=True)
    registry.parent.mkdir(parents=True, exist_ok=True)
    (resources / "old").write_text("old resources", encoding="utf-8")
    registry.write_text("old registry", encoding="utf-8")
    return resources, registry


def test_recovery_validates_complete_journal_before_mutating(tmp_path: Path) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    resources, registry = _targets(root)
    transaction = "a" * 32
    backup = resources.with_name(f".{resources.name}.schema-closure-{transaction}.backup")
    backup.mkdir()
    (backup / "previous").write_text("previous resources", encoding="utf-8")
    unknown = root / "outside-contract"
    unknown_backup = root / f".{unknown.name}.schema-closure-{transaction}.backup"
    journal = {
        "entries": [
            {"target": str(resources), "backup": str(backup), "had_target": True},
            {"target": str(unknown), "backup": str(unknown_backup), "had_target": False},
        ]
    }
    (root / TRANSACTION_FILE).write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(RegistryError, match="unknown target"):
        recover_transaction(root, {resources, registry})

    assert (resources / "old").read_text(encoding="utf-8") == "old resources"
    assert (backup / "previous").read_text(encoding="utf-8") == "previous resources"


def test_broken_journal_symlink_is_rejected_without_following_it(tmp_path: Path) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    resources, registry = _targets(root)
    external = tmp_path / "external-journal"
    (root / TRANSACTION_FILE).symlink_to(external)

    with pytest.raises(RegistryError, match="symbolic link"):
        recover_transaction(root, {resources, registry})

    assert not external.exists()


def test_failed_rollback_preserves_recovery_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    resources, registry = _targets(root)
    resource_candidate = tmp_path / "new-resources"
    resource_candidate.mkdir()
    (resource_candidate / "new").write_text("new resources", encoding="utf-8")
    registry_candidate = tmp_path / "new-registry.yml"
    registry_candidate.write_text("new registry", encoding="utf-8")
    original_rename = Path.rename

    def failing_rename(path: Path, target: Path) -> Path:
        if path == registry_candidate:
            raise OSError("planted publication failure")
        if path.name.startswith(f".{resources.name}.schema-closure-") and target == resources:
            raise OSError("planted rollback failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", failing_rename)

    with pytest.raises(RegistryError, match="rollback was incomplete"):
        publish_pair(root, [(resource_candidate, resources), (registry_candidate, registry)])

    assert (root / TRANSACTION_FILE).is_file()
