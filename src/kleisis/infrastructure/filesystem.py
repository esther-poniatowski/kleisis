"""Validated filesystem operations shared by application services."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from kleisis.errors import DriftError, RegistryError


def digest(path: Path) -> str:
    """Return the SHA-256 digest of one regular file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_file(source: Path, target: Path) -> None:
    """Copy one regular file without following a symbolic link."""
    if source.is_symlink() or not source.is_file():
        raise RegistryError(f"materialized target is not a regular file: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def copy_tree(source: Path, target: Path) -> None:
    """Copy all regular files below a tree and reject symbolic links."""
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise RegistryError(f"schema source tree contains a symbolic link: {path}")
        if path.is_file():
            copy_file(path, target / path.relative_to(source))


def tree_manifest(root: Path) -> dict[str, str]:
    """Map every generated file to its digest."""
    if not root.is_dir():
        raise DriftError(f"generated resource directory is absent: {root}")
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise DriftError(f"generated resource directory contains a symbolic link: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest(path)
    return result
