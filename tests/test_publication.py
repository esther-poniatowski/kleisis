"""Tests for transaction recovery and rollback evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kleisis.errors import RegistryError
from kleisis.infrastructure.publication import (
    LEGACY_TRANSACTION_FILE,
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
    backup = resources.with_name(f".{resources.name}.kleisis-{transaction}.backup")
    backup.mkdir()
    (backup / "previous").write_text("previous resources", encoding="utf-8")
    unknown = root / "outside-contract"
    unknown_backup = root / f".{unknown.name}.kleisis-{transaction}.backup"
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


@pytest.mark.parametrize(
    ("journal_name", "backup_prefix"),
    [
        (TRANSACTION_FILE, "kleisis"),
        (LEGACY_TRANSACTION_FILE, "schema-closure"),
    ],
)
def test_transaction_is_recovered(
    tmp_path: Path, journal_name: str, backup_prefix: str
) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    resources, registry = _targets(root)
    transaction = "b" * 32
    resource_backup = resources.with_name(
        f".{resources.name}.{backup_prefix}-{transaction}.backup"
    )
    registry_backup = registry.with_name(
        f".{registry.name}.{backup_prefix}-{transaction}.backup"
    )
    resources.rename(resource_backup)
    registry.rename(registry_backup)
    resources.mkdir()
    (resources / "new").write_text("new resources", encoding="utf-8")
    registry.write_text("new registry", encoding="utf-8")
    journal = {
        "entries": [
            {"target": str(resources), "backup": str(resource_backup), "had_target": True},
            {"target": str(registry), "backup": str(registry_backup), "had_target": True},
        ]
    }
    (root / journal_name).write_text(json.dumps(journal), encoding="utf-8")

    recover_transaction(root, {resources, registry})

    assert (resources / "old").read_text(encoding="utf-8") == "old resources"
    assert registry.read_text(encoding="utf-8") == "old registry"
    assert not (root / journal_name).exists()
    assert not resource_backup.exists()
    assert not registry_backup.exists()


def test_recovery_rejects_multiple_journals(tmp_path: Path) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    resources, registry = _targets(root)
    for journal_name in (TRANSACTION_FILE, LEGACY_TRANSACTION_FILE):
        (root / journal_name).write_text('{"entries": []}\n', encoding="utf-8")

    with pytest.raises(RegistryError, match="multiple publication journals exist"):
        recover_transaction(root, {resources, registry})


@pytest.mark.parametrize("journal_name", [TRANSACTION_FILE, LEGACY_TRANSACTION_FILE])
def test_publication_rejects_an_existing_journal(
    tmp_path: Path, journal_name: str
) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    resources, registry = _targets(root)
    (root / journal_name).write_text('{"entries": []}\n', encoding="utf-8")

    with pytest.raises(RegistryError, match="publication journal already exists"):
        publish_pair(root, [(tmp_path / "resources", resources), (tmp_path / "registry", registry)])


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
        if path.name.startswith(f".{resources.name}.kleisis-") and target == resources:
            raise OSError("planted rollback failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", failing_rename)

    with pytest.raises(RegistryError, match="rollback was incomplete"):
        publish_pair(root, [(resource_candidate, resources), (registry_candidate, registry)])

    assert (root / TRANSACTION_FILE).is_file()
