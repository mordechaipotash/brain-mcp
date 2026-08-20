#!/usr/bin/env python3
"""
brain-mcp — Pi (pi.ai coding agent) conversation ingester.

Reads Pi JSONL session files from ~/.pi/agent/sessions/ and produces
records matching the canonical parquet schema.

Pi's format (observed v3, and per issue #2):
  - one JSON object per line
  - `session` line carries metadata: id, timestamp, cwd
  - `message` lines carry the conversation: nested .message.role / .message.content,
    the same block shape as Claude Code messages
  - `model_change` lines carry provider/modelId; the model is NOT on .message,
    so we track the most recent model_change and stamp it onto following messages
  - `thinking_level_change` / `custom` are operational noise, skipped
  - role `toolResult` is tool output, skipped (same policy as claude_code.py)

Usage:
    python -m brain_mcp.ingest.pi
"""

import json
import sys
from pathlib import Path
from datetime import datetime

from .base import BaseIngester
from .registry import register
from .schema import make_record, finalize_conversation

SESSIONS_DIR = "~/.pi/agent/sessions"


def extract_project_name(cwd: str, fallback: str) -> str:
    """Readable project name from the session's recorded cwd."""
    if cwd:
        name = Path(cwd).name.strip()
        if name and name not in (".", "/"):
            return name[-50:]
    return fallback[-50:]


def extract_content(message: dict) -> str:
    """Extract text content from a Pi message structure (same block shape as Claude Code)."""
    if not message:
        return ""

    content = message.get("content", "")

    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                # Skip tool_use / tool_result blocks — they're noise
            elif isinstance(block, str):
                texts.append(block)
        return "\n".join(texts)

    if isinstance(content, str):
        return content

    return str(content) if content else ""


def parse_jsonl_file(filepath: Path) -> list[dict]:
    """Parse a single Pi session JSONL file into records."""
    records = []

    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"  Error reading {filepath.name}: {e}", file=sys.stderr)
        return []

    session_id = None
    project_name = extract_project_name("", filepath.parent.name.strip("-"))
    current_model = None

    for line_num, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = data.get("type")

        if msg_type == "session":
            session_id = data.get("id", filepath.stem)
            project_name = extract_project_name(data.get("cwd", ""), project_name)
            continue

        if msg_type == "model_change":
            current_model = data.get("modelId") or current_model
            continue

        if msg_type != "message":
            continue

        message = data.get("message", {})
        role = message.get("role", "")
        # toolResult is tool output, not conversation
        if role not in ("user", "assistant"):
            continue

        content = extract_content(message)

        # Quality filter (same thresholds as claude_code.py)
        if not content or len(content.strip()) < 5:
            continue
        if role == "user" and len(content) < 15:
            continue
        if role == "assistant" and len(content) < 10:
            continue

        if not session_id:
            session_id = filepath.stem

        # Parse timestamp
        ts_str = data.get("timestamp", "")
        try:
            if ts_str:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                ts = datetime.fromtimestamp(filepath.stat().st_mtime)
        except Exception:
            ts = datetime.fromtimestamp(filepath.stat().st_mtime)

        msg_id = data.get("id", f"{session_id}_{line_num}")

        record = make_record(
            source="pi",
            conversation_id=f"pi_{session_id}",
            role=role,
            content=content,
            timestamp=ts,
            msg_index=len(records),
            model=current_model,
            project=project_name,
            conversation_title=project_name,
            message_id=msg_id,
            parent_id=data.get("parentId"),
            temporal_precision="exact",
        )

        if record:
            records.append(record)

    return finalize_conversation(records)


def ingest(source_path: str, **kwargs) -> list[dict]:
    """
    Ingest all Pi sessions from a directory.

    Args:
        source_path: Path to Pi sessions directory (usually ~/.pi/agent/sessions/)

    Returns:
        List of records matching the canonical schema
    """
    sessions_dir = Path(source_path).expanduser().resolve()
    if not sessions_dir.exists():
        print(f"Pi sessions not found at {sessions_dir}", file=sys.stderr)
        return []

    all_files = list(sessions_dir.glob("**/*.jsonl"))
    print(f"Found {len(all_files)} Pi session files")

    all_records = []
    errors = 0

    for filepath in all_files:
        try:
            records = parse_jsonl_file(filepath)
            all_records.extend(records)
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  Error: {filepath.name}: {e}", file=sys.stderr)

    print(f"Ingested {len(all_records)} messages from Pi ({errors} errors)")
    return all_records


_ingest = ingest  # preserve module-level function reference


@register
class PiIngester(BaseIngester):
    """Pi (pi.ai coding agent) conversation ingester (plugin)."""

    @property
    def source_type(self) -> str:
        return "pi"

    @property
    def display_name(self) -> str:
        return "Pi"

    def discover(self) -> list[dict]:
        base = Path(SESSIONS_DIR).expanduser()
        if not base.exists():
            return []
        files = list(base.glob("**/*.jsonl"))
        if not files:
            return []
        return [{"path": str(base), "count_hint": len(files)}]

    def ingest(self, source_path: str) -> list[dict]:
        return _ingest(source_path)


if __name__ == "__main__":
    records = ingest(SESSIONS_DIR)
    print(f"Total records: {len(records)}")
