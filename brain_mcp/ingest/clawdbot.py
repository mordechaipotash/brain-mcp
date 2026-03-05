#!/usr/bin/env python3
"""
brain-mcp — Clawdbot conversation ingester.

Reads Clawdbot session JSONL files from ~/.clawdbot/agents/*/sessions/
and produces records matching the canonical parquet schema.

Usage:
    python -m ingest.clawdbot
"""

import json
import sys
from pathlib import Path
from datetime import datetime

from .base import BaseIngester
from .registry import register
from .schema import make_record, finalize_conversation


def parse_clawdbot_session(jsonl_path: Path) -> list[dict]:
    """Parse a Clawdbot session JSONL into canonical records."""
    records = []
    session_id = None
    model = "unknown"

    with open(jsonl_path) as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            # Extract session metadata
            if entry.get("type") == "session":
                session_id = entry.get("id")

            if entry.get("type") == "model_change":
                model = entry.get("modelId", "unknown")

            if entry.get("type") == "message":
                msg = entry.get("message", {})
                role = msg.get("role")

                if role not in ("user", "assistant"):
                    continue

                # Extract text content
                content_parts = msg.get("content", [])
                text_parts = []
                for part in content_parts:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif isinstance(part, str):
                        text_parts.append(part)

                content = "\n".join(text_parts).strip()
                if not content:
                    continue

                # Parse timestamp
                ts_str = entry.get("timestamp") or msg.get("timestamp")
                if isinstance(ts_str, str):
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except Exception:
                        ts = datetime.now()
                elif isinstance(ts_str, (int, float)):
                    ts = datetime.fromtimestamp(ts_str / 1000)
                else:
                    ts = datetime.now()

                record = make_record(
                    source="clawdbot",
                    conversation_id=session_id or jsonl_path.stem,
                    role=role,
                    content=content,
                    timestamp=ts,
                    msg_index=len(records),
                    model=msg.get("model") or model,
                    conversation_title=f"Clawdbot {jsonl_path.stem[:8]}",
                    message_id=entry.get("id"),
                    parent_id=entry.get("parentId"),
                )

                if record:
                    records.append(record)

    return finalize_conversation(records)


def ingest(source_path: str, **kwargs) -> list[dict]:
    """
    Ingest all Clawdbot sessions from a directory.

    Args:
        source_path: Path to Clawdbot agents directory
                     (usually ~/.clawdbot/agents/)

    Returns:
        List of records matching the canonical schema
    """
    agents_dir = Path(source_path).expanduser().resolve()
    if not agents_dir.exists():
        print(f"Clawdbot agents not found at {agents_dir}", file=sys.stderr)
        return []

    session_files = list(agents_dir.glob("*/sessions/*.jsonl"))
    print(f"Found {len(session_files)} Clawdbot session files")

    all_records = []
    errors = 0

    for jsonl_path in session_files:
        if ".deleted." in jsonl_path.name:
            continue

        try:
            records = parse_clawdbot_session(jsonl_path)
            if records:
                all_records.extend(records)
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  Error: {jsonl_path.name}: {e}", file=sys.stderr)

    print(f"Ingested {len(all_records)} messages from Clawdbot ({errors} errors)")
    return all_records


_ingest = ingest  # preserve module-level function reference


@register
class ClawdbotIngester(BaseIngester):
    """Clawdbot conversation ingester (plugin)."""

    @property
    def source_type(self) -> str:
        return "clawdbot"

    @property
    def display_name(self) -> str:
        return "Clawdbot"

    def discover(self) -> list[dict]:
        base = Path("~/.clawdbot/agents").expanduser()
        if not base.exists():
            return []
        files = list(base.glob("*/sessions/*.jsonl"))
        if not files:
            return []
        return [{"path": str(base), "count_hint": len(files)}]

    def ingest(self, source_path: str) -> list[dict]:
        return _ingest(source_path)


if __name__ == "__main__":
    records = ingest("~/.clawdbot/agents/")
    print(f"Total records: {len(records)}")
