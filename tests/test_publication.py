"""Tests for transaction recovery and rollback evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kleisis.errors import RegistryError
from kleisis.infrastructure.publication import (
    FORMER_JOURNALS,
    JOURNAL_NAME,
    publish_pair,
    recover_transaction,
)

FORMER_NAMES = sorted(FORMER_JOURNALS)


def _targets(root: Path, resources_path: str = ".kleisis/resources") -> tuple[Path, Path]:
    resources = root / resources_path
    registry = root / "specs/schema-registry.yml"
    resources.mkdir(parents=True)
    registry.parent.mkdir(parents=True, exist_ok=True)
    (resources / "old").write_text("old resources", encoding="utf-8")
    registry.write_text("old registry", encoding="utf-8")
    return resources, registry


def _state(root: Path) -> Path:
    state = root / ".kleisis"
    state.mkdir(exist_ok=True)
    return state


def _interrupt(
    root: Path, journal: Path, backups: tuple[Path, Path], targets: tuple[Path, Path]
) -> None:
    """Leave the outputs and the journal of a publication interrupted after both renames."""
    entries = []
    for target, backup in zip(targets, backups, strict=True):
        target.rename(backup)
        entries.append({"target": str(target), "backup": str(backup), "had_target": True})
    resources, registry = targets
    resources.mkdir()
    (resources / "new").write_text("new resources", encoding="utf-8")
    registry.write_text("new registry", encoding="utf-8")
    journal.write_text(json.dumps({"entries": entries}), encoding="utf-8")


def _assert_recovered(resources: Path, registry: Path, journal: Path) -> None:
    assert (resources / "old").read_text(encoding="utf-8") == "old resources"
    assert not (resources / "new").exists()
    assert registry.read_text(encoding="utf-8") == "old registry"
    assert not journal.exists()


def test_recovery_validates_complete_journal_before_mutating(tmp_path: Path) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    resources, registry = _targets(root)
    state = _state(root)
    transaction = "a" * 32
    backup = state / f"{resources.name}.{transaction}.0.backup"
    backup.mkdir()
    (backup / "previous").write_text("previous resources", encoding="utf-8")
    unknown = root / "outside-contract"
    unknown_backup = state / f"{unknown.name}.{transaction}.1.backup"
    journal = {
        "entries": [
            {"target": str(resources), "backup": str(backup), "had_target": True},
            {"target": str(unknown), "backup": str(unknown_backup), "had_target": False},
        ]
    }
    (state / JOURNAL_NAME).write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(RegistryError, match="unknown target"):
        recover_transaction(root, state, {resources, registry})

    assert (resources / "old").read_text(encoding="utf-8") == "old resources"
    assert (backup / "previous").read_text(encoding="utf-8") == "previous resources"


def test_broken_journal_symlink_is_rejected_without_following_it(tmp_path: Path) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    resources, registry = _targets(root)
    state = _state(root)
    external = tmp_path / "external-journal"
    (state / JOURNAL_NAME).symlink_to(external)

    with pytest.raises(RegistryError, match="symbolic link"):
        recover_transaction(root, state, {resources, registry})

    assert not external.exists()


def test_journal_in_the_state_folder_is_recovered(tmp_path: Path) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    resources, registry = _targets(root)
    state = _state(root)
    transaction = "b" * 32
    backups = (
        state / f"{resources.name}.{transaction}.0.backup",
        state / f"{registry.name}.{transaction}.1.backup",
    )
    journal = state / JOURNAL_NAME
    _interrupt(root, journal, backups, (resources, registry))

    recover_transaction(root, state, {resources, registry})

    _assert_recovered(resources, registry, journal)
    assert not any(backup.exists() for backup in backups)


def test_a_state_journal_rejects_a_backup_outside_the_state_folder(tmp_path: Path) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    resources, registry = _targets(root)
    state = _state(root)
    transaction = "c" * 32
    backups = (
        resources.with_name(f".{resources.name}.kleisis-{transaction}.backup"),
        registry.with_name(f".{registry.name}.kleisis-{transaction}.backup"),
    )
    journal = state / JOURNAL_NAME
    _interrupt(root, journal, backups, (resources, registry))

    with pytest.raises(RegistryError, match="invalid backup"):
        recover_transaction(root, state, {resources, registry})

    assert journal.is_file()


@pytest.mark.parametrize("journal_name", FORMER_NAMES)
def test_journal_at_a_former_place_is_recovered(tmp_path: Path, journal_name: str) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    resources, registry = _targets(root)
    state = root / ".kleisis"
    prefix = FORMER_JOURNALS[journal_name]
    transaction = "d" * 32
    backups = (
        resources.with_name(f".{resources.name}.{prefix}-{transaction}.backup"),
        registry.with_name(f".{registry.name}.{prefix}-{transaction}.backup"),
    )
    journal = root / journal_name
    _interrupt(root, journal, backups, (resources, registry))

    recover_transaction(root, state, {resources, registry})

    _assert_recovered(resources, registry, journal)
    assert not any(backup.exists() for backup in backups)


@pytest.mark.parametrize("journal_name", FORMER_NAMES)
def test_former_journal_is_recovered_to_the_former_resources(
    tmp_path: Path, journal_name: str
) -> None:
    """A journal of an earlier release names the resources at their former default place."""
    root = tmp_path / "consumer"
    root.mkdir()
    former, registry = _targets(root, ".schema-resources")
    current = root / ".kleisis/resources"
    prefix = FORMER_JOURNALS[journal_name]
    transaction = "e" * 32
    backups = (
        former.with_name(f".{former.name}.{prefix}-{transaction}.backup"),
        registry.with_name(f".{registry.name}.{prefix}-{transaction}.backup"),
    )
    journal = root / journal_name
    _interrupt(root, journal, backups, (former, registry))

    with pytest.raises(RegistryError, match="unknown target"):
        recover_transaction(root, root / ".kleisis", {current, registry})
    recover_transaction(root, root / ".kleisis", {current, registry}, {former, registry})

    _assert_recovered(former, registry, journal)
    assert not current.exists()


def test_a_state_journal_is_not_recovered_to_the_former_resources(tmp_path: Path) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    former, registry = _targets(root, ".schema-resources")
    current = root / ".kleisis/resources"
    state = _state(root)
    transaction = "f" * 32
    backups = (
        state / f"{former.name}.{transaction}.0.backup",
        state / f"{registry.name}.{transaction}.1.backup",
    )
    _interrupt(root, state / JOURNAL_NAME, backups, (former, registry))

    with pytest.raises(RegistryError, match="unknown target"):
        recover_transaction(root, state, {current, registry}, {former, registry})


@pytest.mark.parametrize(
    "places",
    [
        (".kleisis/transaction.json", ".kleisis-transaction.json"),
        (".kleisis/transaction.json", ".schema-closure-transaction.json"),
        (".kleisis-transaction.json", ".schema-closure-transaction.json"),
    ],
)
def test_recovery_rejects_multiple_journals(tmp_path: Path, places: tuple[str, str]) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    resources, registry = _targets(root)
    for place in places:
        (root / place).write_text('{"entries": []}\n', encoding="utf-8")

    with pytest.raises(RegistryError, match="multiple publication journals exist"):
        recover_transaction(root, root / ".kleisis", {resources, registry})


@pytest.mark.parametrize(
    "place",
    [".kleisis/transaction.json", ".kleisis-transaction.json", ".schema-closure-transaction.json"],
)
def test_publication_rejects_an_existing_journal(tmp_path: Path, place: str) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    resources, registry = _targets(root)
    (root / place).write_text('{"entries": []}\n', encoding="utf-8")

    with pytest.raises(RegistryError, match="publication journal already exists"):
        publish_pair(
            root,
            root / ".kleisis",
            [(tmp_path / "resources", resources), (tmp_path / "registry", registry)],
        )


def _candidates(tmp_path: Path) -> tuple[Path, Path]:
    resource_candidate = tmp_path / "new-resources"
    resource_candidate.mkdir()
    (resource_candidate / "new").write_text("new resources", encoding="utf-8")
    registry_candidate = tmp_path / "new-registry.yml"
    registry_candidate.write_text("new registry", encoding="utf-8")
    return resource_candidate, registry_candidate


def test_publication_keeps_its_journal_and_backups_in_the_state_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    resources, registry = _targets(root, "var/resources")
    state = root / "var/state"
    resource_candidate, registry_candidate = _candidates(tmp_path)
    seen: list[tuple[Path, Path]] = []
    original_rename = Path.rename

    def recording_rename(path: Path, target: Path) -> Path:
        seen.append((path, Path(target)))
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", recording_rename)

    publish_pair(root, state, [(resource_candidate, resources), (registry_candidate, registry)])

    backups = [target for path, target in seen if path in {resources, registry}]
    assert len(backups) == 2
    assert all(backup.parent == state for backup in backups)
    assert (resources / "new").read_text(encoding="utf-8") == "new resources"
    assert registry.read_text(encoding="utf-8") == "new registry"
    assert sorted(path.name for path in state.iterdir()) == []
    assert sorted(path.name for path in registry.parent.iterdir()) == [registry.name]


def test_failed_rollback_preserves_recovery_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "consumer"
    root.mkdir()
    resources, registry = _targets(root)
    state = root / ".kleisis"
    resource_candidate, registry_candidate = _candidates(tmp_path)
    original_rename = Path.rename

    def failing_rename(path: Path, target: Path) -> Path:
        if path == registry_candidate:
            raise OSError("planted publication failure")
        if path.parent == state and path.name.startswith(f"{resources.name}."):
            raise OSError("planted rollback failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", failing_rename)

    with pytest.raises(RegistryError, match="rollback was incomplete"):
        publish_pair(root, state, [(resource_candidate, resources), (registry_candidate, registry)])

    assert (state / JOURNAL_NAME).is_file()
    assert not (root / ".kleisis-transaction.json").exists()
