"""
brain-mcp — Base class for all conversation ingesters.

Every ingester extends BaseIngester and implements:
- source_type: unique identifier ('claude-code', 'cursor', etc.)
- display_name: human-friendly name ('Claude Code', 'Cursor', etc.)
- discover(): find conversation sources on disk
- ingest(source_path): read conversations and return schema-compliant records

Optional override:
- incremental_ingest(source_path, state): efficient partial re-ingest
"""

from abc import ABC, abstractmethod
from datetime import datetime


class BaseIngester(ABC):
    """Base class for all conversation ingesters."""

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Unique identifier: 'claude-code', 'cursor', etc."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-friendly name: 'Claude Code', 'Cursor', etc."""
        ...

    @abstractmethod
    def discover(self) -> list[dict]:
        """
        Find conversation sources on disk.

        Returns:
            List of dicts with at least {path, count_hint}.
            May include optional keys: version, subtype, size.
        """
        ...

    @abstractmethod
    def ingest(self, source_path: str) -> list[dict]:
        """
        Read conversations from source_path.

        Args:
            source_path: Path to the conversation source (directory or file).

        Returns:
            List of message dicts matching the canonical schema
            (see brain_mcp.ingest.schema).
        """
        ...

    def incremental_ingest(
        self, source_path: str, state: dict
    ) -> tuple[list[dict], dict]:
        """
        Incremental ingest. Default: full re-ingest.

        Override for efficiency (e.g., only read files newer than last sync).

        Args:
            source_path: Path to the conversation source.
            state: Previous sync state dict (from SyncStateManager).

        Returns:
            Tuple of (new_records, updated_state).
        """
        records = self.ingest(source_path)
        return records, {"last_full_ingest": datetime.now().isoformat()}

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} source_type={self.source_type!r}>"
