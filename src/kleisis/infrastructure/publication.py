"""Recoverable publication of coupled generated outputs."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import cast

from kleisis.errors import RegistryError

TRANSACTION_FILE = ".kleisis-transaction.json"
LEGACY_TRANSACTION_FILE = ".schema-closure-transaction.json"


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _validated_entries(
    journal: Path, allowed_targets: set[Path], backup_prefix: str
) -> list[dict[str, object]]:
    try:
        raw = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RegistryError(f"cannot read publication journal {journal}: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"entries"}:
        raise RegistryError(f"publication journal has an invalid root: {journal}")
    raw_entries = raw["entries"]
    if not isinstance(raw_entries, list) or len(raw_entries) != len(allowed_targets):
        raise RegistryError(f"publication journal has an invalid entry count: {journal}")
    entries: list[dict[str, object]] = []
    targets: set[Path] = set()
    transaction_ids: set[str] = set()
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict) or set(raw_entry) != {
            "target",
            "backup",
            "had_target",
        }:
            raise RegistryError(f"publication journal contains an invalid entry: {journal}")
        target_value = raw_entry["target"]
        backup_value = raw_entry["backup"]
        had_target = raw_entry["had_target"]
        if not isinstance(target_value, str) or not Path(target_value).is_absolute():
            raise RegistryError(f"publication journal target must be absolute: {target_value!r}")
        if not isinstance(backup_value, str) or not Path(backup_value).is_absolute():
            raise RegistryError(f"publication journal backup must be absolute: {backup_value!r}")
        if type(had_target) is not bool:
            raise RegistryError("publication journal had_target must be a boolean")
        target = Path(target_value).resolve()
        backup = Path(backup_value).resolve()
        if target not in allowed_targets or target in targets:
            raise RegistryError(f"publication journal contains an unknown target: {target}")
        pattern = (
            rf"\.{re.escape(target.name)}\."
            rf"{re.escape(backup_prefix)}-([0-9a-f]{{32}})\.backup"
        )
        match = re.fullmatch(pattern, backup.name)
        if backup.parent != target.parent or match is None or backup.is_symlink():
            raise RegistryError(f"publication journal contains an invalid backup: {backup}")
        transaction_ids.add(match.group(1))
        targets.add(target)
        entries.append({"target": target, "backup": backup, "had_target": had_target})
    if targets != allowed_targets or len(transaction_ids) != 1:
        raise RegistryError(
            f"publication journal does not describe one complete transaction: {journal}"
        )
    return entries


def recover_transaction(project_root: Path, allowed_targets: set[Path]) -> None:
    """Restore the old outputs left by an interrupted publication."""
    candidates = (
        (project_root / TRANSACTION_FILE, "kleisis"),
        (project_root / LEGACY_TRANSACTION_FILE, "schema-closure"),
    )
    present = [
        (journal, prefix)
        for journal, prefix in candidates
        if journal.exists() or journal.is_symlink()
    ]
    if len(present) > 1:
        raise RegistryError("multiple publication journals exist")
    if not present:
        return
    journal, backup_prefix = present[0]
    if journal.is_symlink():
        raise RegistryError(f"publication journal must not be a symbolic link: {journal}")
    if not journal.is_file():
        raise RegistryError(f"publication journal must be a regular file: {journal}")
    normalized_targets = {target.resolve() for target in allowed_targets}
    entries = _validated_entries(journal, normalized_targets, backup_prefix)
    try:
        for entry in reversed(entries):
            target = cast(Path, entry["target"])
            backup = cast(Path, entry["backup"])
            if backup.exists():
                if target.exists():
                    _remove_path(target)
                backup.rename(target)
            elif not cast(bool, entry["had_target"]) and target.exists():
                _remove_path(target)
        journal.unlink()
    except OSError as exc:
        raise RegistryError(
            f"cannot recover interrupted publication from {journal}: {exc}"
        ) from exc


def publish_pair(project_root: Path, pairs: list[tuple[Path, Path]]) -> None:
    """Replace coupled outputs and restore both when either replacement fails."""
    if len(pairs) != 2 or len({target.resolve() for _, target in pairs}) != 2:
        raise RegistryError("publication requires two distinct output targets")
    transaction_id = uuid.uuid4().hex
    entries: list[dict[str, object]] = []
    for _, target in pairs:
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = target.with_name(f".{target.name}.kleisis-{transaction_id}.backup")
        entries.append(
            {"target": str(target), "backup": str(backup), "had_target": target.exists()}
        )
    journal = project_root / TRANSACTION_FILE
    existing = [
        candidate
        for candidate in (journal, project_root / LEGACY_TRANSACTION_FILE)
        if candidate.exists() or candidate.is_symlink()
    ]
    if existing:
        raise RegistryError(f"publication journal already exists: {existing[0]}")
    journal.write_text(json.dumps({"entries": entries}, indent=2) + "\n", encoding="utf-8")
    installed: list[Path] = []
    try:
        for entry in entries:
            target = Path(str(entry["target"]))
            if bool(entry["had_target"]):
                target.rename(Path(str(entry["backup"])))
        for candidate, target in pairs:
            candidate.rename(target)
            installed.append(target)
    except Exception as original:
        try:
            for target in reversed(installed):
                _remove_path(target)
            for entry in reversed(entries):
                backup = Path(str(entry["backup"]))
                target = Path(str(entry["target"]))
                if backup.exists():
                    backup.rename(target)
        except Exception as rollback_error:
            raise RegistryError(
                "publication failed and rollback was incomplete; "
                f"recovery journal retained: {journal}"
            ) from rollback_error
        journal.unlink()
        raise original
    else:
        journal.unlink()
        for entry in entries:
            backup = Path(str(entry["backup"]))
            if backup.exists():
                _remove_path(backup)
