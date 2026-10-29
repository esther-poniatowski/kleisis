"""Tests for exact source adapters."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest
from conftest import git

from kleisis.domain.models import ArtifactSource, GitSource
from kleisis.errors import SourceError
from kleisis.infrastructure.sources import ExactSourceProvider, _git_environment


class Response:
    """A bounded in-memory HTTP response fixture."""

    def __init__(self, data: bytes):
        self.data = data

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *arguments: object) -> None:
        del arguments

    def read(self, size: int = -1) -> bytes:
        return self.data if size < 0 else self.data[:size]


def zip_bytes(name: str, content: str) -> bytes:
    """Build one retained ZIP artifact."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.writestr(name, content)
    return output.getvalue()


def test_artifact_provider_verifies_digest_and_extracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = zip_bytes("schemas/item.schema.yaml", "type: string\n")
    monkeypatch.setattr(
        "kleisis.infrastructure.sources.urllib.request.urlopen",
        lambda locator, timeout: Response(data),
    )
    source = ArtifactSource(
        kind="artifact",
        locator="https://example.invalid/source.zip",
        source_commit="a" * 40,
        sha256=hashlib.sha256(data).hexdigest(),
    )
    destination = tmp_path / "destination"

    ExactSourceProvider().acquire(source, destination)

    assert (destination / "schemas/item.schema.yaml").read_text(encoding="utf-8") == (
        "type: string\n"
    )


def test_artifact_provider_rejects_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = zip_bytes("../escape", "invalid")
    monkeypatch.setattr(
        "kleisis.infrastructure.sources.urllib.request.urlopen",
        lambda locator, timeout: Response(data),
    )
    source = ArtifactSource(
        kind="artifact",
        locator="https://example.invalid/source.zip",
        source_commit="a" * 40,
        sha256=hashlib.sha256(data).hexdigest(),
    )

    with pytest.raises(SourceError, match="leaves the destination"):
        ExactSourceProvider().acquire(source, tmp_path / "destination")


def test_git_provider_archives_exact_commit_without_worktree_filters(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "schema.yaml").write_text("value: exact\n", encoding="utf-8")
    (repository / ".gitattributes").write_text("schema.yaml filter=missing\n", encoding="utf-8")
    git(["init", "--quiet"], repository)
    git(["config", "user.name", "Kleisis Tests"], repository)
    git(["config", "user.email", "tests@example.invalid"], repository)
    git(["add", "."], repository)
    git(["commit", "--quiet", "-m", "fixture"], repository)
    commit = git(["rev-parse", "HEAD"], repository)
    source = GitSource(kind="git", repository=str(repository), commit=commit)
    destination = tmp_path / "destination"

    ExactSourceProvider().acquire(source, destination)

    assert (destination / "schema.yaml").read_text(encoding="utf-8") == "value: exact\n"


def test_git_environment_rejects_ambient_git_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_DIR", "/tmp/ambient")
    monkeypatch.setenv("GIT_CONFIG_KEY_9", "filter.unsafe.clean")
    monkeypatch.setenv("KLEISIS_GIT_AUTHORIZATION", "Authorization: Bearer secret")

    environment = _git_environment("KLEISIS_GIT_AUTHORIZATION")

    assert "GIT_DIR" not in environment
    assert "GIT_CONFIG_KEY_9" not in environment
    assert environment["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    assert environment["GIT_CONFIG_VALUE_0"] == "Authorization: Bearer secret"
