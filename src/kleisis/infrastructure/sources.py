"""Acquire exact Git states and retained artifacts."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from kleisis.domain.models import ArtifactSource, GitSource, Source
from kleisis.errors import SourceError

_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
_MAX_EXPANDED_BYTES = 256 * 1024 * 1024
_MAX_MEMBER_BYTES = 32 * 1024 * 1024
_MAX_MEMBERS = 10_000
_GIT_TIMEOUT_SECONDS = 120
_HTTP_TIMEOUT_SECONDS = 30
_PASSTHROUGH_ENVIRONMENT = {
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NO_PROXY",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TEMP",
    "TMP",
    "TMPDIR",
}


class ExactSourceProvider:
    """Acquire portable exact sources without consulting ambient checkouts."""

    def acquire(self, source: Source, destination: Path) -> None:
        """Materialize one exact source into an empty destination."""
        if isinstance(source, GitSource):
            _acquire_git(source, destination)
        elif isinstance(source, ArtifactSource):
            _acquire_artifact(source, destination)
        else:  # pragma: no cover - closed by the Source union
            raise SourceError(f"unsupported source: {source}")


def _git_environment(credential_env: str | None) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if key in _PASSTHROUGH_ENVIRONMENT
    }
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "0",
        }
    )
    if credential_env is not None:
        authorization = os.environ.get(credential_env)
        if not authorization:
            raise SourceError(f"Git credential environment variable is absent: {credential_env}")
        environment.update(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.extraHeader",
                "GIT_CONFIG_VALUE_0": authorization,
            }
        )
    return environment


def _run_git(arguments: list[str], cwd: Path, credential_env: str | None = None) -> str:
    environment = _git_environment(credential_env)
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            env=environment,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = (
            exc.stderr.strip()
            if isinstance(exc, subprocess.CalledProcessError) and exc.stderr
            else str(exc)
        )
        raise SourceError(f"Git source acquisition failed: {detail}") from exc
    return completed.stdout.strip()


def _acquire_git(source: GitSource, destination: Path) -> None:
    repository = destination.parent / f"{destination.name}.git"
    repository.mkdir(parents=True)
    _run_git(["init", "--bare", "--quiet"], repository, source.credential_env)
    _run_git(["remote", "add", "origin", source.repository], repository, source.credential_env)
    _run_git(
        ["fetch", "--quiet", "--depth", "1", "origin", source.commit],
        repository,
        source.credential_env,
    )
    actual = _run_git(["rev-parse", "FETCH_HEAD"], repository, source.credential_env)
    if actual != source.commit:
        raise SourceError(f"Git source resolved to {actual}, expected {source.commit}")
    archive = destination.parent / f"{destination.name}.tar"
    _run_git(
        [
            "--literal-pathspecs",
            "archive",
            "--format=tar",
            f"--output={archive}",
            "FETCH_HEAD",
            *(root.as_posix() for root in source.archive_roots),
        ],
        repository,
        source.credential_env,
    )
    destination.mkdir(parents=True)
    try:
        _extract_tar(archive, destination)
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(repository, ignore_errors=True)


def _artifact_bytes(locator: str) -> bytes:
    parsed = urlparse(locator)
    try:
        if parsed.scheme in {"http", "https"}:
            with urllib.request.urlopen(  # noqa: S310 - exact digest follows
                locator, timeout=_HTTP_TIMEOUT_SECONDS
            ) as response:
                data = response.read(_MAX_DOWNLOAD_BYTES + 1)
                if not isinstance(data, bytes):  # pragma: no cover - urllib contract
                    raise SourceError(f"artifact response is not bytes: {locator}")
                if len(data) > _MAX_DOWNLOAD_BYTES:
                    raise SourceError(f"artifact exceeds {_MAX_DOWNLOAD_BYTES} bytes: {locator}")
                return data
    except OSError as exc:
        raise SourceError(f"cannot read artifact {locator}: {exc}") from exc
    raise SourceError(f"unsupported artifact locator scheme: {locator}")


def _safe_archive_target(destination: Path, name: str) -> Path:
    target = (destination / name).resolve()
    if not target.is_relative_to(destination.resolve()):
        raise SourceError(f"archive member leaves the destination: {name}")
    return target


def _extract_zip(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if len(members) > _MAX_MEMBERS:
            raise SourceError(f"artifact contains more than {_MAX_MEMBERS} members")
        expanded = 0
        for member in members:
            target = _safe_archive_target(destination, member.filename)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            mode = member.external_attr >> 16
            if mode & 0o170000 == 0o120000:
                raise SourceError(f"artifact contains a symbolic link: {member.filename}")
            if member.file_size > _MAX_MEMBER_BYTES:
                raise SourceError(f"artifact member is too large: {member.filename}")
            expanded += member.file_size
            if expanded > _MAX_EXPANDED_BYTES:
                raise SourceError(f"artifact expands beyond {_MAX_EXPANDED_BYTES} bytes")
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source_file, target.open("wb") as target_file:
                shutil.copyfileobj(source_file, target_file)


def _extract_tar(archive: Path, destination: Path) -> None:
    with tarfile.open(archive) as bundle:
        members = bundle.getmembers()
        if len(members) > _MAX_MEMBERS:
            raise SourceError(f"archive contains more than {_MAX_MEMBERS} members")
        expanded = 0
        for member in members:
            target = _safe_archive_target(destination, member.name)
            if member.issym() or member.islnk():
                continue
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise SourceError(f"artifact contains an unsupported member: {member.name}")
            if member.size > _MAX_MEMBER_BYTES:
                raise SourceError(f"archive member is too large: {member.name}")
            expanded += member.size
            if expanded > _MAX_EXPANDED_BYTES:
                raise SourceError(f"archive expands beyond {_MAX_EXPANDED_BYTES} bytes")
            target.parent.mkdir(parents=True, exist_ok=True)
            source_file = bundle.extractfile(member)
            if source_file is None:
                raise SourceError(f"cannot read archive member: {member.name}")
            with source_file, target.open("wb") as target_file:
                shutil.copyfileobj(source_file, target_file)


def _acquire_artifact(source: ArtifactSource, destination: Path) -> None:
    data = _artifact_bytes(source.locator)
    actual = hashlib.sha256(data).hexdigest()
    if actual != source.sha256:
        raise SourceError(f"artifact digest is {actual}, expected {source.sha256}")
    destination.mkdir(parents=True)
    archive = destination.parent / "source-archive"
    archive.write_bytes(data)
    try:
        if zipfile.is_zipfile(archive):
            _extract_zip(archive, destination)
        elif tarfile.is_tarfile(archive):
            _extract_tar(archive, destination)
        else:
            raise SourceError("retained artifact must be a ZIP or tar archive")
    finally:
        archive.unlink(missing_ok=True)
