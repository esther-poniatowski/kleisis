"""Ports used by the schema-closure application service."""

from pathlib import Path
from typing import Protocol

from schema_closure.domain.models import Source


class SourceProvider(Protocol):
    """Acquire one exact source into an empty destination."""

    def acquire(self, source: Source, destination: Path) -> None:
        """Write the exact source tree into ``destination``."""
