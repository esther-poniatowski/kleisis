"""Grammar of a contract identifier, `<namespace>:<name>@<version>`, a public interface of Kleisis.

The [schema of the grammar](../../specs/schemas/contract-identifier.schema.json) publishes the
same pattern, and the cases of `specs/cases/contract-identifiers.yml` fix its verdicts. A reader
in another tool refers to the schema by its `$id`, or runs the cases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WORD = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*"
PATTERN = rf"^(?P<namespace>{_WORD}):(?P<name>{_WORD}(?:\.{_WORD})*)@(?P<version>[1-9][0-9]*)$"
"""Pattern of a contract identifier, written as the schema of the grammar writes it.

The namespace names the owner of the contract. The name holds one or more words joined by `.`,
such as `view.next-act`, each word in lower kebab case. The version is a positive integer written
without a leading zero.
"""

_IDENTIFIER = re.compile(PATTERN.removeprefix("^").removesuffix("$"))


@dataclass(frozen=True)
class ContractIdentifier:
    """A contract identifier split into its three parts.

    Examples
    --------
    >>> parse_contract_identifier("gnomon:view.next-act@8")
    ContractIdentifier(namespace='gnomon', name='view.next-act', version=8)
    >>> str(ContractIdentifier("kleisis", "schema-registry", 2))
    'kleisis:schema-registry@2'
    """

    namespace: str
    name: str
    version: int

    def __str__(self) -> str:
        return f"{self.namespace}:{self.name}@{self.version}"


def is_contract_identifier(text: object) -> bool:
    """Return whether ``text`` is a string that follows the grammar of a contract identifier."""
    return isinstance(text, str) and _IDENTIFIER.fullmatch(text) is not None


def parse_contract_identifier(text: object) -> ContractIdentifier:
    """Return the parts of a contract identifier, or raise ``ValueError`` naming the grammar."""
    match = _IDENTIFIER.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise ValueError(
            f"{text!r} is no contract identifier: write <namespace>:<name>@<version>, with words "
            "in lower kebab case, words of the name joined by '.', and a positive version"
        )
    return ContractIdentifier(
        match.group("namespace"), match.group("name"), int(match.group("version"))
    )
