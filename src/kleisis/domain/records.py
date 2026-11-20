"""Parsing and structural validation of the configuration file `kleisis.yml`.

The configuration records the resources of a consumer: its paths, its exports, its dependencies
and its child projects. The code names it the resource record.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import yaml

from kleisis.contracts import is_contract_identifier
from kleisis.domain.documents import load_document
from kleisis.domain.keywords import KEYWORD
from kleisis.domain.models import (
    DEFAULT_BINDING,
    DEFAULT_SCHEMA_SUFFIXES,
    DEFAULT_STATE,
    FORMER_RESOURCES,
    RESOURCES_IN_STATE,
    ArtifactSource,
    Dependency,
    GitSource,
    Paths,
    Project,
    ResourceRecord,
    SchemaConvention,
    safe_relative_path,
)
from kleisis.errors import RecordError
from kleisis.findings import CONTRACT_IDENTIFIER, RECORD_UNREADABLE, KeyPath, Rule, pointer

RECORD_FORMAT = "kleisis:configuration@1"
"""The format identifier that the configuration declares in its key ``_schema``."""
RETIRED_FORMATS = ("kleisis:schema-resources@1", "kleisis:schema-resources@2")
"""The formats of the former record `specs/schema-resources.yml`, rejected with the upgrade."""
_UPGRADE = (
    f"write _schema: {RECORD_FORMAT} in kleisis.yml, and declare paths.owned_schemas and "
    "paths.registry, which no longer default to specs/schemas and specs/schema-registry.yml"
)
_FORMAT_1_NOTE = (
    f"format 1 kept the resources in {FORMER_RESOURCES}, now <paths.state>/{RESOURCES_IN_STATE} "
    f"by default; a configuration that keeps the former folder sets "
    f"paths.resources: {FORMER_RESOURCES}"
)

_FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DEPENDENCY_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_ENVIRONMENT_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")
_PROPERTY_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_SUFFIX = re.compile(r"\.[A-Za-z0-9_.-]+")


_CONFIGURATION = KeyPath(document="configuration")
"""The root of the configuration, from which each validator below names the node that it checks."""


def _invalid(place: KeyPath, defect: str, rule: Rule | None = None) -> RecordError:
    """Return the failure of the node at ``place``, named by its label and located by its pointer.

    ``defect`` states the failure after the label, as in ``must be a mapping``.
    """
    return RecordError(f"{place.label} {defect}", rule=rule, locus=place.pointer)


def _mapping(value: object, place: KeyPath) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise _invalid(place, "must be a mapping")
    return cast(dict[str, Any], value)


def _sequence(value: object, place: KeyPath) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _invalid(place, "must be a sequence")
    return value


def _keys(value: dict[str, Any], allowed: set[str], place: KeyPath) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _invalid(place, f"contains unknown fields: {', '.join(unknown)}")


def _nonempty_string(value: object, place: KeyPath) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(place, "must be a non-empty string")
    return value


def _contract(value: object, place: KeyPath) -> str:
    """Return a contract identifier, or raise naming its grammar."""
    text = _nonempty_string(value, place)
    if not is_contract_identifier(text):
        raise _invalid(
            place,
            f"{text!r} is no contract identifier: write <namespace>:<name>@<version>",
            rule=CONTRACT_IDENTIFIER,
        )
    return text


def _commit(value: object, place: KeyPath) -> str:
    result = _nonempty_string(value, place)
    if not _FULL_COMMIT.fullmatch(result):
        raise _invalid(place, "must be a full lowercase Git commit")
    return result


def _portable_https_url(value: object, place: KeyPath) -> str:
    result = _nonempty_string(value, place)
    parsed = urlparse(result)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise _invalid(place, "must be an HTTPS URL without embedded credentials")
    return result


def load_resource_record(path: Path) -> ResourceRecord:
    """Load the configuration of a consumer and reject every undeclared field."""
    try:
        raw = load_document(path)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise RecordError(
            f"cannot read the configuration {path}: {exc}", rule=RECORD_UNREADABLE, subject=path
        ) from exc
    try:
        return _resource_record(raw)
    except RecordError as exc:
        exc.locate(path)
        raise


def _check_format(root: dict[str, Any]) -> None:
    """Require the configuration to declare ``_schema: kleisis:configuration@1``, without alias.

    A former record, by a retired format or by the retired key ``format_version``, is rejected
    with its upgrade; a record of format 1 also names its moved default.
    """
    expected = f"write _schema: {RECORD_FORMAT}"
    if "format_version" in root:
        raise RecordError(
            f"the key format_version is retired; {_UPGRADE}; {_FORMAT_1_NOTE}",
            locus=pointer("format_version"),
        )
    if "_schema" not in root:
        raise RecordError(
            f"the configuration declares no _schema; {expected}", locus=pointer("_schema")
        )
    declared = root["_schema"]
    if declared in RETIRED_FORMATS:
        note = f"; {_FORMAT_1_NOTE}" if declared == RETIRED_FORMATS[0] else ""
        raise RecordError(
            f"the format {declared} is retired; {_UPGRADE}{note}", locus=pointer("_schema")
        )
    if declared != RECORD_FORMAT:
        raise RecordError(
            f"the configuration declares _schema {declared!r}; {expected}",
            locus=pointer("_schema"),
        )


def _resource_record(raw: object) -> ResourceRecord:
    root = _mapping(raw, _CONFIGURATION)
    _check_format(root)
    _keys(
        root,
        {"_schema", "paths", "schemas", "exports", "dependencies", "projects", "keywords"},
        _CONFIGURATION,
    )

    paths_place = _CONFIGURATION / "paths"
    raw_paths = _mapping(root.get("paths", {}), paths_place)
    _keys(raw_paths, {"owned_schemas", "registry", "resources", "state"}, paths_place)
    for required in ("owned_schemas", "registry"):
        if required not in raw_paths:
            raise RecordError(
                f"the configuration declares no {(paths_place / required).label}; Kleisis "
                "assumes no folder of the consumer",
                locus=(paths_place / required).pointer,
            )
    state = safe_relative_path(
        raw_paths.get("state", DEFAULT_STATE.as_posix()), paths_place / "state"
    )
    paths = Paths(
        owned_schemas=safe_relative_path(raw_paths["owned_schemas"], paths_place / "owned_schemas"),
        registry=safe_relative_path(raw_paths["registry"], paths_place / "registry"),
        resources=safe_relative_path(
            raw_paths.get("resources", (state / RESOURCES_IN_STATE).as_posix()),
            paths_place / "resources",
        ),
        state=state,
    )

    schemas = _schema_convention(root.get("schemas", {}))

    exports_place = _CONFIGURATION / "exports"
    exports = tuple(
        _contract(item, exports_place / index)
        for index, item in enumerate(_sequence(root.get("exports"), exports_place))
    )
    if len(exports) != len(set(exports)):
        raise _invalid(exports_place, "contains duplicate identifiers")

    dependencies: list[Dependency] = []
    dependency_ids: set[str] = set()
    dependencies_place = _CONFIGURATION / "dependencies"
    for index, item in enumerate(_sequence(root.get("dependencies"), dependencies_place)):
        place = dependencies_place / index
        raw_dependency = _mapping(item, place)
        _keys(raw_dependency, {"id", "source", "registry", "contracts", "binding"}, place)
        dependency_id = _nonempty_string(raw_dependency.get("id"), place / "id")
        if not _DEPENDENCY_ID.fullmatch(dependency_id):
            raise _invalid(place / "id", "must be lower kebab-case")
        if dependency_id in dependency_ids:
            raise RecordError(
                f"duplicate dependency id: {dependency_id}", locus=(place / "id").pointer
            )
        dependency_ids.add(dependency_id)

        source_place = place / "source"
        raw_source = _mapping(raw_dependency.get("source"), source_place)
        kind = raw_source.get("kind")
        source: GitSource | ArtifactSource
        if kind == "git":
            _keys(
                raw_source,
                {"kind", "repository", "commit", "credential_env", "archive_roots"},
                source_place,
            )
            credential_env = raw_source.get("credential_env")
            if credential_env is not None and (
                not isinstance(credential_env, str)
                or not _ENVIRONMENT_NAME.fullmatch(credential_env)
            ):
                raise _invalid(source_place / "credential_env", "must name an environment variable")
            roots_place = source_place / "archive_roots"
            raw_archive_roots = raw_source.get("archive_roots")
            archive_roots = tuple(
                safe_relative_path(item, roots_place / root_index)
                for root_index, item in enumerate(_sequence(raw_archive_roots, roots_place))
            )
            if raw_archive_roots is not None and not archive_roots:
                raise _invalid(roots_place, "must be non-empty when present")
            if len(archive_roots) != len(set(archive_roots)):
                raise _invalid(roots_place, "must be unique")
            if archive_roots != tuple(sorted(archive_roots)):
                raise _invalid(roots_place, "must follow path order")
            source = GitSource(
                kind="git",
                repository=_portable_https_url(
                    raw_source.get("repository"), source_place / "repository"
                ),
                commit=_commit(raw_source.get("commit"), source_place / "commit"),
                credential_env=credential_env,
                archive_roots=archive_roots,
            )
        elif kind == "artifact":
            _keys(raw_source, {"kind", "locator", "source_commit", "sha256"}, source_place)
            digest = _nonempty_string(raw_source.get("sha256"), source_place / "sha256")
            if not _SHA256.fullmatch(digest):
                raise _invalid(source_place / "sha256", "must be lowercase SHA-256")
            source = ArtifactSource(
                kind="artifact",
                locator=_portable_https_url(raw_source.get("locator"), source_place / "locator"),
                source_commit=_commit(
                    raw_source.get("source_commit"), source_place / "source_commit"
                ),
                sha256=digest,
            )
        else:
            raise _invalid(source_place / "kind", "must be git or artifact")

        contracts_place = place / "contracts"
        contracts = tuple(
            _contract(contract, contracts_place / contract_index)
            for contract_index, contract in enumerate(
                _sequence(raw_dependency.get("contracts"), contracts_place)
            )
        )
        if len(contracts) != len(set(contracts)):
            raise _invalid(contracts_place, "must be unique")
        dependencies.append(
            Dependency(
                id=dependency_id,
                source=source,
                registry=safe_relative_path(raw_dependency.get("registry"), place / "registry"),
                contracts=contracts,
                binding=_binding(raw_dependency.get("binding", DEFAULT_BINDING), place / "binding"),
            )
        )

    projects: list[Project] = []
    project_paths: set[object] = set()
    projects_place = _CONFIGURATION / "projects"
    for index, item in enumerate(_sequence(root.get("projects"), projects_place)):
        place = projects_place / index
        raw_project = _mapping(item, place)
        _keys(raw_project, {"path", "registry", "required"}, place)
        project_path = safe_relative_path(raw_project.get("path"), place / "path")
        if project_path in project_paths:
            raise RecordError(
                f"duplicate project path: {project_path}", locus=(place / "path").pointer
            )
        project_paths.add(project_path)
        required = raw_project.get("required", True)
        if not isinstance(required, bool):
            raise _invalid(place / "required", "must be a boolean")
        projects.append(
            Project(
                path=project_path,
                registry=safe_relative_path(raw_project.get("registry"), place / "registry"),
                required=required,
            )
        )

    return ResourceRecord(
        paths=paths,
        schemas=schemas,
        exports=exports,
        dependencies=tuple(dependencies),
        projects=tuple(projects),
        keywords=_keywords(root.get("keywords", {})),
    )


def _keywords(raw: object) -> dict[str, str]:
    """Return the declared keywords, each named `x-<words>` and described by a non-empty string."""
    place = _CONFIGURATION / "keywords"
    declared = _mapping(raw, place)
    for name, description in declared.items():
        if not KEYWORD.fullmatch(name):
            raise RecordError(
                f"keyword {name!r} must be x- followed by words in lower kebab case",
                locus=(place / name).pointer,
            )
        if not isinstance(description, str) or not description.strip():
            raise RecordError(
                f"keyword {name} must hold a non-empty description", locus=(place / name).pointer
            )
    return dict(declared)


def _binding(value: object, place: KeyPath) -> str:
    """Return the name of a binding property, a JSON property name without a pointer escape."""
    name = _nonempty_string(value, place)
    if not _PROPERTY_NAME.fullmatch(name):
        raise _invalid(place, "must be a property name of letters, digits, '_' and '-'")
    return name


def _schema_convention(raw: object) -> SchemaConvention:
    """Return the schema convention of the record, each key defaulting to the conventional value."""
    place = _CONFIGURATION / "schemas"
    convention = _mapping(raw, place)
    _keys(convention, {"suffixes", "binding"}, place)
    suffixes_place = place / "suffixes"
    raw_suffixes = convention.get("suffixes")
    suffixes = (
        DEFAULT_SCHEMA_SUFFIXES
        if raw_suffixes is None
        else tuple(
            _nonempty_string(item, suffixes_place / index)
            for index, item in enumerate(_sequence(raw_suffixes, suffixes_place))
        )
    )
    if not suffixes:
        raise _invalid(suffixes_place, "must not be empty")
    if len(suffixes) != len(set(suffixes)):
        raise _invalid(suffixes_place, "must be unique")
    for index, suffix in enumerate(suffixes):
        if not _SUFFIX.fullmatch(suffix):
            raise _invalid(suffixes_place / index, "must open with a dot and hold no separator")
    return SchemaConvention(
        suffixes=suffixes,
        binding=_binding(convention.get("binding", DEFAULT_BINDING), place / "binding"),
    )
