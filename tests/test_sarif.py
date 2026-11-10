"""Tests of the SARIF output of the validation and check commands."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from conftest import concrete_schema
from findings_profile import identities, log_violations
from test_service import (
    LABEL,
    NOTE,
    ROOTED,
    make_consumer,
    make_resource_only,
    make_rooted_consumer,
    make_two_sources,
    write_tree,
)

import kleisis.application.service as service_module
from kleisis import __version__
from kleisis.application.service import SchemaClosureService
from kleisis.cli import main
from kleisis.domain.models import Source
from kleisis.errors import SourceError
from kleisis.findings import (
    REFERENCE_UNRESOLVED,
    RULES,
    Finding,
    Rule,
    complete,
    field_pointer,
    pointer,
)
from kleisis.sarif import sarif_log

VECTORS = Path(__file__).parent / "fixtures/elenchos"
RECORD = "path:specs/schema-resources.yml"
OWNED = "path:specs/schemas/owned.schema.yaml"


def load_vectors(name: str) -> list[dict[str, Any]]:
    """Read one file of test vectors of the findings profile."""
    vectors: list[dict[str, Any]] = json.loads((VECTORS / name).read_text(encoding="utf-8"))
    return vectors


@pytest.mark.parametrize("vector", load_vectors("identity.json"), ids=lambda item: item["case"])
def test_validator_accepts_identity_vectors_and_builds_their_identities(
    vector: dict[str, Any],
) -> None:
    # The vectors omit the rule list of the driver, which the gate does not check.
    assert log_violations(vector["log"], require_rules=False) == []
    assert identities(vector["log"]) == vector["expected"]


@pytest.mark.parametrize("vector", load_vectors("rejected.json"), ids=lambda item: item["case"])
def test_validator_rejects_rejected_vectors(vector: dict[str, Any]) -> None:
    assert log_violations(vector["log"], require_rules=False) != []


def run_main(arguments: list[str]) -> int:
    """Run the command line and return its exit status."""
    try:
        main(arguments)
    except SystemExit as exc:
        assert isinstance(exc.code, int)
        return exc.code
    return 0


def run_sarif(
    capsys: pytest.CaptureFixture[str], command: str, root: Path
) -> tuple[int, dict[str, Any]]:
    """Run one command with SARIF output and return its exit status and its log."""
    status = run_main([command, "--root", str(root), "--format", "sarif"])
    log: dict[str, Any] = json.loads(capsys.readouterr().out)
    return status, log


def invocation(log: dict[str, Any]) -> dict[str, Any]:
    """Return the single invocation of the single run of ``log``."""
    [run] = log["runs"]
    [declared] = run["invocations"]
    assert isinstance(declared, dict)
    return declared


def stop_reason(log: dict[str, Any]) -> str:
    """Return the reason stated by the failed invocation of the single run of ``log``."""
    declared = invocation(log)
    assert declared["executionSuccessful"] is False
    [notification] = declared["toolExecutionNotifications"]
    assert notification["level"] == "error"
    text = notification["message"]["text"]
    assert isinstance(text, str)
    return text


def use_provider(monkeypatch: pytest.MonkeyPatch, provider: object) -> None:
    """Make the command acquire every source through ``provider``."""
    monkeypatch.setattr(service_module, "ExactSourceProvider", lambda: provider)


class FailingSourceProvider:
    """Fail to acquire every source."""

    def acquire(self, source: Source, destination: Path) -> None:
        del source, destination
        raise SourceError("planted acquisition failure")


Scenario = Callable[[Path, pytest.MonkeyPatch], Path]


def record_only(text: str, *, owned: bool = True) -> Scenario:
    """A consumer holding one record and, when ``owned``, an empty owned schema directory."""

    def build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        del monkeypatch
        root = write_tree(tmp_path / "consumer", {"specs/schema-resources.yml": text})
        if owned:
            (root / "specs/schemas").mkdir()
        return root

    return build


def owned_tree(files: dict[str, str], exports: str = "[consumer:owned@1]") -> Scenario:
    """A consumer without dependency, owning ``files`` below ``specs/schemas``."""

    def build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        del monkeypatch
        return write_tree(
            tmp_path / "consumer",
            {
                "specs/schema-resources.yml": f"format_version: 1\nexports: {exports}\n",
                **{f"specs/schemas/{name}": text for name, text in files.items()},
            },
        )

    return build


def owned_reference(reference: str, files: dict[str, str] | None = None) -> Scenario:
    """A consumer whose single export holds one ``$ref``, beside ``files``."""
    return owned_tree(
        {"owned.schema.yaml": concrete_schema("consumer:owned@1", reference=reference)}
        | (files or {})
    )


def two_sources(reference: str, edit: Callable[[Path, Path], None] | None = None) -> Scenario:
    """The consumer of ``make_two_sources``, after ``edit`` of its tree and of the tests tree."""

    def build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        consumer, provider = make_two_sources(tmp_path, reference)
        if edit is not None:
            edit(tmp_path, consumer)
        use_provider(monkeypatch, provider)
        return consumer

    return build


def resource_only(reference: str | None, version: int = 2) -> Scenario:
    """The consumer of ``make_resource_only``."""

    def build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        consumer, provider = make_resource_only(tmp_path, reference, registry_version=version)
        use_provider(monkeypatch, provider)
        return consumer

    return build


def failing_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "consumer"
    make_consumer(root, "a" * 40)
    use_provider(monkeypatch, FailingSourceProvider())
    return root


def linked_support(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = owned_tree({"owned.schema.yaml": concrete_schema("consumer:owned@1")})(
        tmp_path, monkeypatch
    )
    (root / "specs/schemas/defs.txt").write_text("type: string\n", encoding="utf-8")
    (root / "specs/schemas/support.schema.yaml").symlink_to("defs.txt")
    return root


def rooted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    del monkeypatch
    return make_rooted_consumer(tmp_path, ROOTED)


def replace_in(path: Path, old: str, new: str) -> None:
    """Replace one text in a fixture file."""
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def primitives_registry_version_3(tests: Path, consumer: Path) -> None:
    del consumer
    replace_in(
        tests / "primitives/specs/schema-registry.yml", "schema_version: 2", "schema_version: 3"
    )


def primitives_contract_renamed(tests: Path, consumer: Path) -> None:
    del consumer
    replace_in(
        tests / "primitives/schemas/record.schema.yaml",
        "const: primitives:record@1",
        "const: x:y@1",
    )


def shared_contract_unprovided(tests: Path, consumer: Path) -> None:
    del tests
    replace_in(
        consumer / "specs/schema-resources.yml",
        "contracts: [shared:main@1]",
        "contracts: [shared:main@1, shared:absent@1]",
    )


def note_mapped_twice(tests: Path, consumer: Path) -> None:
    del consumer
    write_tree(
        tests / "shared",
        {
            "schemas/note.schema.yaml": f"$id: {NOTE}\ntype: integer\n",
            "specs/schema-registry.yml": f"""schema_version: 2
schemas:
  shared:main@1: schemas/main.schema.yaml
exports: [shared:main@1]
resources:
  {NOTE}: schemas/note.schema.yaml
""",
        },
    )


def note_mapped_to_text(tests: Path, consumer: Path) -> None:
    del consumer
    replace_in(
        tests / "primitives/specs/schema-registry.yml",
        f"{NOTE}: schemas/note.schema.yaml",
        f"{NOTE}: schemas/text.schema.yaml",
    )


def owned_label_differs(tests: Path, consumer: Path) -> None:
    del tests
    write_tree(consumer, {"specs/schemas/label.schema.yaml": f"$id: {LABEL}\ntype: integer\n"})


def child_shares_other_bytes(tests: Path, consumer: Path) -> None:
    del tests
    with (consumer / "specs/schema-resources.yml").open("a", encoding="utf-8") as record:
        record.write("projects:\n  - path: projects/child\n")
    write_tree(
        consumer / "projects/child",
        {
            "specs/schemas/child.schema.yaml": concrete_schema("child:owned@1"),
            ".schema-resources/shared/schemas/main.schema.yaml": concrete_schema("shared:main@1"),
            "specs/schema-registry.yml": """schema_version: 2
schemas:
  child:owned@1: specs/schemas/child.schema.yaml
  shared:main@1: .schema-resources/shared/schemas/main.schema.yaml
exports: [child:owned@1]
resources: {}
""",
        },
    )


def child_with_missing_dependency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    del monkeypatch
    return write_tree(
        tmp_path / "parent",
        {
            "specs/schemas/parent.schema.yaml": concrete_schema("parent:owned@1"),
            "specs/schema-resources.yml": """format_version: 1
exports: [parent:owned@1]
projects:
  - path: projects/child
""",
            "projects/child/specs/schemas/child.schema.yaml": concrete_schema("child:owned@1"),
            "projects/child/specs/schema-registry.yml": """schema_version: 2
schemas:
  child:owned@1: specs/schemas/child.schema.yaml
  tool:base@1: specs/schemas/base.schema.yaml
exports: [child:owned@1]
resources: {}
""",
        },
    )


CONSUMER_RECORD = "format_version: 1\nexports: [consumer:owned@1]\n"
SCENARIOS: dict[str, tuple[Scenario, list[str]]] = {
    "record/unreadable": (
        record_only("format_version: [\n"),
        [f"kleisis|record/unreadable|{RECORD}"],
    ),
    "record/invalid": (
        record_only("format_version: 2\n"),
        [f"kleisis|record/invalid|{RECORD}|/format_version"],
    ),
    "record/path-overlap": (
        record_only("format_version: 1\npaths:\n  resources: specs\n"),
        [f"kleisis|record/path-overlap|{RECORD}|/paths/resources|specs/schemas"],
    ),
    "path/escape": (
        record_only("format_version: 1\npaths:\n  owned_schemas: ../outside\n"),
        [f"kleisis|path/escape|{RECORD}|/paths/owned_schemas"],
    ),
    "path/absent": (
        record_only("format_version: 1\n", owned=False),
        [f"kleisis|path/absent|{RECORD}|/paths/owned_schemas"],
    ),
    "source/unavailable": (
        failing_source,
        [f"kleisis|source/unavailable|{RECORD}|/dependencies|shared"],
    ),
    "registry/invalid": (
        two_sources(NOTE, primitives_registry_version_3),
        [
            "kleisis|registry/invalid|path:.schema-resources/primitives/specs/schema-registry.yml"
            "|/schema_version"
        ],
    ),
    "schema/unreadable": (
        owned_tree(
            {
                "owned.schema.yaml": concrete_schema("consumer:owned@1"),
                "broken.schema.yaml": "type: [\n",
            }
        ),
        ["kleisis|schema/unreadable|path:specs/schemas/broken.schema.yaml"],
    ),
    "schema/link": (
        linked_support,
        ["kleisis|schema/link|path:specs/schemas/support.schema.yaml"],
    ),
    "contract/binding": (
        owned_tree({"owned.schema.yaml": "type: object\nrequired: [_schema]\n"}, exports="[]"),
        [f"kleisis|contract/binding|{OWNED}|/properties/_schema"],
    ),
    "contract/binding-mismatch": (
        two_sources(NOTE, primitives_contract_renamed),
        [
            "kleisis|contract/binding-mismatch"
            "|path:.schema-resources/primitives/schemas/record.schema.yaml|/properties/_schema/const"
        ],
    ),
    "contract/duplicate": (
        owned_tree(
            {
                "a.schema.yaml": concrete_schema("consumer:owned@1"),
                "b.schema.yaml": concrete_schema("consumer:owned@1"),
            }
        ),
        ["kleisis|contract/duplicate|path:specs/schemas/b.schema.yaml|/properties/_schema/const"],
    ),
    "contract/unexported": (
        owned_tree(
            {
                "owned.schema.yaml": concrete_schema("consumer:owned@1"),
                "hidden.schema.yaml": concrete_schema("consumer:hidden@1"),
            }
        ),
        [
            "kleisis|contract/unexported|path:specs/schemas/hidden.schema.yaml"
            "|/properties/_schema/const"
        ],
    ),
    "contract/unowned-export": (
        owned_tree(
            {"owned.schema.yaml": concrete_schema("consumer:owned@1")},
            exports="[consumer:owned@1, consumer:ghost@1, consumer:phantom@1]",
        ),
        [
            f"kleisis|contract/unowned-export|{RECORD}|/exports|consumer:ghost@1",
            f"kleisis|contract/unowned-export|{RECORD}|/exports|consumer:phantom@1",
        ],
    ),
    "contract/unprovided": (
        two_sources(NOTE, shared_contract_unprovided),
        [f"kleisis|contract/unprovided|{RECORD}|/dependencies|shared:absent@1"],
    ),
    "dependency/no-resources": (
        resource_only(LABEL, version=1),
        [f"kleisis|dependency/no-resources|{RECORD}|/dependencies|primitives"],
    ),
    "dependency/unused": (
        resource_only(None),
        [f"kleisis|dependency/unused|{RECORD}|/dependencies|primitives"],
    ),
    "reference/unmapped": (
        two_sources("https://primitives.example/missing"),
        [f"kleisis|reference/unmapped|{OWNED}|link:https://primitives.example/missing"],
    ),
    "reference/conflicting-bytes": (
        two_sources(NOTE, note_mapped_twice),
        [f"kleisis|reference/conflicting-bytes|{OWNED}|link:{NOTE}"],
    ),
    "reference/identifier-mismatch": (
        two_sources(NOTE, note_mapped_to_text),
        [
            "kleisis|reference/identifier-mismatch"
            f"|path:.schema-resources/primitives/schemas/text.schema.yaml|/$id|{NOTE}"
        ],
    ),
    "reference/leaves-source": (
        owned_reference("../../../outside.schema.yaml"),
        [f"kleisis|reference/leaves-source|{OWNED}|link:../../../outside.schema.yaml"],
    ),
    "reference/vault-rooted": (
        rooted,
        [f"kleisis|reference/vault-rooted|{OWNED}|link:{ROOTED}"],
    ),
    "reference/target-absent": (
        owned_reference("missing.schema.yaml"),
        [f"kleisis|reference/target-absent|{OWNED}|link:missing.schema.yaml"],
    ),
    "reference/unresolved": (
        owned_reference(
            "defs.schema.yaml#/$defs/missing",
            {"defs.schema.yaml": "$defs:\n  value: {type: string}\n"},
        ),
        [f"kleisis|reference/unresolved|{OWNED}|link:defs.schema.yaml#/$defs/missing"],
    ),
    "reference/chain-too-long": (
        owned_reference("loop.schema.yaml", {"loop.schema.yaml": '$ref: "#"\n'}),
        [f"kleisis|reference/chain-too-long|{OWNED}|link:loop.schema.yaml"],
    ),
    "resources/conflicting-bytes": (
        two_sources(NOTE, owned_label_differs),
        [
            "kleisis|resources/conflicting-bytes"
            "|path:.schema-resources/primitives/schemas/label.schema.yaml|/$id"
        ],
    ),
    "child/missing-dependency": (
        child_with_missing_dependency,
        [
            "kleisis|child/missing-dependency|path:projects/child/specs/schema-registry.yml"
            "|/schemas/tool:base@1"
        ],
    ),
    "child/shared-bytes": (
        two_sources(NOTE, child_shares_other_bytes),
        [
            "kleisis|child/shared-bytes"
            "|path:projects/child/.schema-resources/shared/schemas/main.schema.yaml"
        ],
    ),
}


@pytest.mark.parametrize("rule", sorted(SCENARIOS))
def test_validation_failure_yields_one_conforming_result_per_failure(
    rule: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenario, expected = SCENARIOS[rule]
    root = scenario(tmp_path, monkeypatch)

    status, log = run_sarif(capsys, "validate", root)

    assert status == 1
    assert log_violations(log) == []
    assert [result["ruleId"] for result in log["runs"][0]["results"]] == [rule] * len(expected)
    assert identities(log) == expected
    assert stop_reason(log) == (
        f"the build stopped at its first failure ({rule}); later stages were not evaluated"
    )


def test_drift_of_each_output_yields_its_own_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    consumer = two_sources(NOTE)(tmp_path, monkeypatch)
    SchemaClosureService().materialize(consumer)
    (consumer / ".schema-resources/shared/schemas/main.schema.yaml").write_text(
        "# drift\n", encoding="utf-8"
    )
    with (consumer / "specs/schema-registry.yml").open("a", encoding="utf-8") as registry:
        registry.write("# drift\n")

    status, log = run_sarif(capsys, "check", consumer)

    assert status == 1
    assert log_violations(log) == []
    assert identities(log) == [
        "kleisis|drift/resources|path:.schema-resources/",
        "kleisis|drift/registry|path:specs/schema-registry.yml",
    ]
    assert invocation(log) == {"executionSuccessful": True}
    uris = [
        result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        for result in log["runs"][0]["results"]
    ]
    assert uris == [".schema-resources/", "specs/schema-registry.yml"]


def test_absent_registry_is_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    consumer = two_sources(NOTE)(tmp_path, monkeypatch)
    SchemaClosureService().materialize(consumer)
    (consumer / "specs/schema-registry.yml").unlink()

    status, log = run_sarif(capsys, "check", consumer)

    assert status == 1
    assert identities(log) == ["kleisis|drift/registry|path:specs/schema-registry.yml"]
    assert invocation(log) == {"executionSuccessful": True}


def test_filesystem_failure_declares_a_failed_run_without_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(self: SchemaClosureService, project_root: Path) -> None:
        raise OSError("planted failure")

    monkeypatch.setattr(SchemaClosureService, "validate", fail)

    status = run_main(["validate", "--root", str(tmp_path), "--format", "sarif"])

    captured = capsys.readouterr()
    log = json.loads(captured.out)
    assert status == 1
    assert captured.err == "kleisis: filesystem operation failed: planted failure\n"
    assert log_violations(log) == []
    assert log["runs"][0]["results"] == []
    assert stop_reason(log) == "filesystem operation failed: planted failure"


@pytest.mark.parametrize("command", ["validate", "check"])
def test_clean_run_yields_a_log_without_result(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    consumer = two_sources(NOTE)(tmp_path, monkeypatch)
    SchemaClosureService().materialize(consumer)

    status, log = run_sarif(capsys, command, consumer)

    assert status == 0
    assert log_violations(log) == []
    assert len(log["runs"]) == 1
    run = log["runs"][0]
    assert run["results"] == []
    assert invocation(log) == {"executionSuccessful": True}
    assert run["tool"]["driver"]["name"] == "kleisis"
    assert run["tool"]["driver"]["version"] == __version__
    assert [rule["id"] for rule in run["tool"]["driver"]["rules"]] == [rule.id for rule in RULES]


def test_text_format_keeps_the_failure_line_and_writes_no_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = owned_reference("missing.schema.yaml")(tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as raised:
        main(["validate", "--root", str(root)])

    assert raised.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("kleisis: schema target is not a regular source file: ")


def test_sarif_format_is_refused_for_materialize(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["materialize", "--format", "sarif"])

    assert raised.value.code == 2
    assert "--format sarif applies to check and validate only" in capsys.readouterr().err


@pytest.mark.parametrize("failing", [False, True])
def test_output_option_writes_the_log_to_its_path(
    failing: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reference = "missing.schema.yaml" if failing else "defs.schema.yaml"
    root = owned_reference(reference, {"defs.schema.yaml": "type: string\n"})(tmp_path, monkeypatch)
    output = tmp_path / "build/gate/kleisis.sarif"

    status = run_main(
        ["validate", "--root", str(root), "--format", "sarif", "--output", str(output)]
    )

    assert status == (1 if failing else 0)
    assert capsys.readouterr().out == ""
    log = json.loads(output.read_text(encoding="utf-8"))
    assert log_violations(log) == []
    assert invocation(log)["executionSuccessful"] is not failing


def test_output_option_requires_the_sarif_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status = run_main(["validate", "--root", str(tmp_path), "--output", str(tmp_path / "a")])

    assert status == 2
    assert "--output requires --format sarif" in capsys.readouterr().err


def test_unwritable_output_exits_with_status_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = owned_reference("missing.schema.yaml")(tmp_path, monkeypatch)
    blocker = tmp_path / "blocker"
    blocker.write_text("", encoding="utf-8")

    status = run_main(
        ["validate", "--root", str(root), "--format", "sarif", "--output", f"{blocker}/a.sarif"]
    )

    assert status == 2
    assert "kleisis: cannot write the SARIF log: " in capsys.readouterr().err


def test_version_option_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    for option in ("-v", "--version"):
        assert run_main([option]) == 0
        assert capsys.readouterr().out.strip() == __version__


def test_short_forms_name_the_root_and_the_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = owned_reference("missing.schema.yaml")(tmp_path, monkeypatch)
    output = tmp_path / "logs/kleisis.sarif"

    status = run_main(["validate", "-r", str(root), "--format", "sarif", "-o", str(output)])

    assert status == 1
    assert capsys.readouterr().out == ""
    log = json.loads(output.read_text(encoding="utf-8"))
    assert invocation(log)["executionSuccessful"] is False


def test_root_is_read_from_the_environment_then_overridden_by_the_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = owned_reference("missing.schema.yaml")(tmp_path, monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("KLEISIS_ROOT", str(root))

    assert run_main(["validate"]) == 1
    assert "schema target is not a regular source file" in capsys.readouterr().err

    assert run_main(["validate", "--root", str(elsewhere)]) == 1
    assert "schema-resources.yml" in capsys.readouterr().err


def test_root_that_is_not_a_folder_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("KLEISIS_ROOT", raising=False)

    assert run_main(["validate", "--root", str(tmp_path / "absent")]) == 2
    assert "is not a folder" in capsys.readouterr().err


def test_record_option_is_read_from_the_current_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = owned_reference("missing.schema.yaml")(tmp_path, monkeypatch)
    monkeypatch.chdir(root / "specs")

    # The record is read, so the run reaches the unresolved reference of the owned schema.
    assert run_main(["validate", "--root", str(root), "--record", "schema-resources.yml"]) == 1
    assert "schema target is not a regular source file" in capsys.readouterr().err

    # A path relative to the root is no longer read: from `specs/`, it names a missing file.
    status = run_main(["validate", "--root", str(root), "--record", "specs/schema-resources.yml"])
    assert status == 1
    assert "specs/specs/schema-resources.yml" in capsys.readouterr().err


def test_record_outside_the_root_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = owned_reference("missing.schema.yaml")(tmp_path, monkeypatch)
    outside = tmp_path / "outside.yml"
    outside.write_text("", encoding="utf-8")

    assert run_main(["validate", "--root", str(root), "--record", str(outside)]) == 2
    assert "lies outside the root" in capsys.readouterr().err


def test_every_scenario_rule_and_every_catalogued_rule_agree() -> None:
    covered = set(SCENARIOS) | {"drift/resources", "drift/registry"}
    assert covered == {rule.id for rule in RULES}
    assert len({rule.id for rule in RULES}) == len(RULES)


def test_names_and_uris_escape_reserved_characters() -> None:
    rule = REFERENCE_UNRESOLVED
    findings = [
        Finding(rule, "a", PurePosixPath("docs/notes#old%.md"), locus="link:foo|bar.md"),
        Finding(rule, "b", PurePosixPath("docs/café.md"), discriminator="x"),
        Finding(rule, "c", PurePosixPath("."), folder=True),
    ]

    log = sarif_log(findings, "0.0.0")

    assert log_violations(log) == []
    results = log["runs"][0]["results"]
    assert [result["locations"][0]["logicalLocations"][0]["name"] for result in results] == [
        "path:docs/notes%23old%25.md#link:foo|bar.md",
        "path:docs/café.md",
        "path:./",
    ]
    assert [
        result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for result in results
    ] == ["docs/notes%23old%25.md", "docs/caf%C3%A9.md", "./"]
    assert identities(log) == [
        "kleisis|reference/unresolved|path:docs/notes#old%25.md|link:foo%7Cbar.md",
        "kleisis|reference/unresolved|path:docs/café.md||x",
        "kleisis|reference/unresolved|path:./",
    ]


def test_field_names_become_json_pointers() -> None:
    assert field_pointer("dependencies[0].source.archive_roots[1]") == (
        "/dependencies/0/source/archive_roots/1"
    )
    assert field_pointer("resource record") == ""
    assert field_pointer("record") == ""
    assert pointer("schemas", "a/b~c") == "/schemas/a~1b~0c"


def test_segments_convert_as_the_examples_of_the_profile() -> None:
    assert field_pointer("relations[2].target") == "/relations/2/target"
    assert pointer("relations", 2, "target") == "/relations/2/target"
    assert pointer("a/b") == "/a~1b"
    assert pointer("c~1d") == "/c~01d"
    assert pointer("") == "/"
    assert pointer() == ""


def test_subject_outside_the_repository_falls_back_to_the_root() -> None:
    rule = Rule("example/key", "An example.")
    record = PurePosixPath("specs/schema-resources.yml")

    assert complete(Finding(rule, "a"), record).subject == record
    escaped = complete(Finding(rule, "b", PurePosixPath("../elsewhere.yml")), record)
    assert (escaped.subject, escaped.folder) == (PurePosixPath("."), True)


def _set(path: str, value: object) -> Callable[[dict[str, Any]], None]:
    """Return a mutation that sets one key of the first result, or deletes it for ``None``."""

    def mutate(result: dict[str, Any]) -> None:
        *parents, key = path.split(".")
        target: Any = result
        for parent in parents:
            target = target[int(parent)] if parent.isdigit() else target[parent]
        if value is None:
            del target[key]
        else:
            target[key] = value

    return mutate


LOCATION = "locations.0"
NAME = f"{LOCATION}.logicalLocations.0.name"
URI = f"{LOCATION}.physicalLocation.artifactLocation.uri"


@pytest.mark.parametrize(
    "mutation",
    [
        _set("suppressions", []),
        _set("fingerprints", {"elenchos/v1": "x"}),
        _set("baselineState", "new"),
        _set("kind", "fail"),
        _set("level", None),
        _set("level", "none"),
        _set("ruleId", "Reference/Unresolved"),
        _set("ruleId", "unknown/rule"),
        _set("message", {"text": ""}),
        _set(NAME, "specs/schemas/owned.schema.yaml"),
        _set(NAME, "path:specs/schemas/owned.schema.yaml#"),
        _set(NAME, "path:./specs/owned.schema.yaml"),
        _set(NAME, "path:specs/%41.yaml"),
        _set(NAME, "path:specs/schemas/owned.schema.yaml#section:a"),
        _set(NAME, "path:specs/schemas/owned.schema.yaml#/a~2b"),
        _set(URI, "specs/schemas/other.schema.yaml"),
        _set(f"{LOCATION}.physicalLocation.artifactLocation.uriBaseId", None),
        _set(f"{LOCATION}.physicalLocation", None),
        _set("partialFingerprints", {"discriminator/v1": ""}),
        _set("partialFingerprints", {"other/v1": "x"}),
    ],
)
def test_validator_rejects_planted_defects_in_a_conforming_log(
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    finding = Finding(
        REFERENCE_UNRESOLVED,
        "planted",
        PurePosixPath("specs/schemas/owned.schema.yaml"),
        locus="link:defs.schema.yaml#/$defs/missing",
    )
    log = sarif_log([finding], "0.0.0")
    assert log_violations(log) == []

    mutation(log["runs"][0]["results"][0])

    assert log_violations(log) != []


def test_validator_requires_column_kind_with_results() -> None:
    log = sarif_log([Finding(REFERENCE_UNRESOLVED, "planted", PurePosixPath("a.yml"))], "0.0.0")
    del log["runs"][0]["columnKind"]

    assert log_violations(log) != []


def _stopped_log() -> dict[str, Any]:
    finding = Finding(REFERENCE_UNRESOLVED, "planted", PurePosixPath("a.yml"))
    return sarif_log([finding], "0.0.0", stopped="planted stop")


def test_validator_admits_a_failed_run_without_results() -> None:
    log = _stopped_log()
    assert log_violations(log) == []

    del log["runs"][0]["results"]

    assert log_violations(log) == []


ERROR_NOTE = {"level": "error", "message": {"text": "planted"}}


@pytest.mark.parametrize(
    "mutation",
    [
        _set("tool.driver.rules", None),
        _set("tool.driver.rules.0.id", None),
        _set("tool.driver.name", "elenchos"),
        _set("invocations", [{}]),
        _set("invocations", [{"executionSuccessful": False}]),
        _set(
            "invocations",
            [{"executionSuccessful": True, "toolExecutionNotifications": [ERROR_NOTE]}],
        ),
        _set("invocations", [{"executionSuccessful": True}, {"executionSuccessful": True}]),
        _set(
            "invocations",
            [
                {
                    "executionSuccessful": False,
                    "toolExecutionNotifications": [{"level": "fatal", "message": {"text": "x"}}],
                }
            ],
        ),
        _set(
            "invocations",
            [{"executionSuccessful": False, "toolExecutionNotifications": [{"level": "error"}]}],
        ),
        lambda run: run.update(results=None),
        _set("results", {}),
    ],
)
def test_validator_rejects_planted_defects_in_a_complete_run(
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    log = sarif_log([Finding(REFERENCE_UNRESOLVED, "planted", PurePosixPath("a.yml"))], "0.0.0")
    assert log_violations(log) == []

    mutation(log["runs"][0])

    assert log_violations(log) != []


def test_validator_rejects_a_complete_run_without_results() -> None:
    log = sarif_log([], "0.0.0")
    assert log_violations(log) == []

    del log["runs"][0]["results"]

    assert log_violations(log) != []
