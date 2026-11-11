"""Recoverable publication of coupled generated outputs.

The journal of a publication and the backups of its targets lie in the state folder. Earlier
releases left the journal at the root and each backup beside its target; such a journal is still
recovered before any new publication.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import cast

from kleisis.errors import RegistryError

JOURNAL_NAME = "transaction.json"
"""The name of the publication journal inside the state folder."""
FORMER_JOURNALS = {
    ".kleisis-transaction.json": "kleisis",
    ".schema-closure-transaction.json": "schema-closure",
}
"""The journals that earlier releases left at the root, each with the prefix of its backups."""

BackupRule = Callable[[Path], tuple[Path, str]]
"""Map a target to the folder of its backup and the pattern of the backup name.

The pattern holds one group, the transaction identifier.
"""


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _journals(project_root: Path, state: Path) -> list[tuple[Path, str | None]]:
    """List each place of a journal, with the backup prefix of a former place."""
    return [
        (state / JOURNAL_NAME, None),
        *((project_root / name, prefix) for name, prefix in FORMER_JOURNALS.items()),
    ]


def _backup_rule(state: Path, former_prefix: str | None) -> BackupRule:
    """Return where the journal of one place keeps the backup of each target."""
    if former_prefix is None:
        return lambda target: (
            state,
            rf"{re.escape(target.name)}\.([0-9a-f]{{32}})\.[0-9]+\.backup",
        )
    return lambda target: (
        target.parent,
        rf"\.{re.escape(target.name)}\.{re.escape(former_prefix)}-([0-9a-f]{{32}})\.backup",
    )


def _validated_entries(
    journal: Path, target_sets: list[set[Path]], backup_rule: BackupRule
) -> list[dict[str, object]]:
    """Read a journal that names exactly one of ``target_sets``, before any change."""
    try:
        raw = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RegistryError(f"cannot read publication journal {journal}: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"entries"}:
        raise RegistryError(f"publication journal has an invalid root: {journal}")
    raw_entries = raw["entries"]
    if not isinstance(raw_entries, list) or len(raw_entries) not in {
        len(targets) for targets in target_sets
    }:
        raise RegistryError(f"publication journal has an invalid entry count: {journal}")
    known_targets = set().union(*target_sets)
    entries: list[dict[str, object]] = []
    targets: set[Path] = set()
    backups: set[Path] = set()
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
        if target not in known_targets or target in targets:
            raise RegistryError(f"publication journal contains an unknown target: {target}")
        folder, pattern = backup_rule(target)
        match = re.fullmatch(pattern, backup.name)
        if backup.parent != folder or match is None or backup.is_symlink() or backup in backups:
            raise RegistryError(f"publication journal contains an invalid backup: {backup}")
        transaction_ids.add(match.group(1))
        targets.add(target)
        backups.add(backup)
        entries.append({"target": target, "backup": backup, "had_target": had_target})
    if targets not in target_sets or len(transaction_ids) != 1:
        raise RegistryError(
            f"publication journal does not describe one complete transaction: {journal}"
        )
    return entries


def recover_transaction(
    project_root: Path,
    state: Path,
    targets: set[Path],
    former_targets: set[Path] | None = None,
) -> None:
    """Restore the old outputs left by an interrupted publication.

    A journal in ``state`` must name ``targets``. A journal at a former place may instead name
    ``former_targets``, the outputs at the default places of format 1 of the resource record.
    """
    state = state.resolve()
    present = [
        (journal, prefix)
        for journal, prefix in _journals(project_root, state)
        if journal.exists() or journal.is_symlink()
    ]
    if len(present) > 1:
        raise RegistryError(
            "multiple publication journals exist: "
            + ", ".join(str(journal) for journal, _ in present)
        )
    if not present:
        return
    journal, former_prefix = present[0]
    if journal.is_symlink():
        raise RegistryError(f"publication journal must not be a symbolic link: {journal}")
    if not journal.is_file():
        raise RegistryError(f"publication journal must be a regular file: {journal}")
    target_sets = [{target.resolve() for target in targets}]
    if former_prefix is not None and former_targets is not None:
        target_sets.append({target.resolve() for target in former_targets})
    entries = _validated_entries(journal, target_sets, _backup_rule(state, former_prefix))
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


def publish_pair(project_root: Path, state: Path, pairs: list[tuple[Path, Path]]) -> None:
    """Replace coupled outputs and restore both when either replacement fails.

    The journal and the backups lie in ``state``, below the root as the targets, so that each
    backup is a rename within one file system.
    """
    if len(pairs) != 2 or len({target.resolve() for _, target in pairs}) != 2:
        raise RegistryError("publication requires two distinct output targets")
    existing = [
        journal
        for journal, _ in _journals(project_root, state)
        if journal.exists() or journal.is_symlink()
    ]
    if existing:
        raise RegistryError(f"publication journal already exists: {existing[0]}")
    state.mkdir(parents=True, exist_ok=True)
    transaction_id = uuid.uuid4().hex
    entries: list[dict[str, object]] = []
    for index, (_, target) in enumerate(pairs):
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = state / f"{target.name}.{transaction_id}.{index}.backup"
        entries.append(
            {"target": str(target), "backup": str(backup), "had_target": target.exists()}
        )
    journal = state / JOURNAL_NAME
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
