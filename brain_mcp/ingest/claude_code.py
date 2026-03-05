#!/usr/bin/env python3
"""
brain-mcp — Claude Code conversation ingester.

Reads Claude Code JSONL conversation files from ~/.claude/projects/
and produces records matching the canonical parquet schema.

Usage:
    python -m ingest.claude_code
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta

from .base import BaseIngester
from .registry import register
from .schema import make_record, finalize_conversation


def extract_project_name(project_path: Path) -> str:
    """Extract readable project name from Claude Code directory path."""
    name = project_path.name
    name = name.lstrip("-")
    parts = name.split("-")
    # Remove common path fragments
    meaningful = [p for p in parts if p not in ("Users", "Library", "Mobile", "Documents", "com", "apple", "CloudDocs")]
    if meaningful:
        return "-".join(meaningful[-3:])
    return name[-50:]


def extract_content(message: dict) -> str:
    """Extract text content from Claude Code message structure."""
    if not message:
        return ""

    content = message.get("content", "")

    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                # Skip tool_use and tool_result — they're noise
            elif isinstance(block, str):
                texts.append(block)
        return "\n".join(texts)

    if isinstance(content, str):
        return content

    return str(content) if content else ""


def parse_jsonl_file(filepath: Path) -> list[dict]:
    """Parse a single Claude Code JSONL file into records."""
    records = []
    project_name = extract_project_name(filepath.parent)

    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"  Error reading {filepath.name}: {e}", file=sys.stderr)
        return []

    session_id = None

    for line_num, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = data.get("type")
        if msg_type not in ("user", "assistant"):
            continue

        if not session_id:
            session_id = data.get("sessionId", filepath.stem)

        message = data.get("message", {})
        role = message.get("role") or data.get("type")
        content = extract_content(message)

        # Quality filter
        if not content or len(content.strip()) < 5:
            continue

        content_lower = content.lower().strip()
        noise = ["warmup", "prompt is too long", "request interrupted"]
        if any(content_lower == n for n in noise):
            continue

        if role == "user" and len(content) < 15:
            continue
        if role == "assistant" and len(content) < 10:
            continue

        # Parse timestamp
        ts_str = data.get("timestamp", "")
        try:
            if ts_str:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            else:
                ts = datetime.fromtimestamp(filepath.stat().st_mtime)
        except Exception:
            ts = datetime.fromtimestamp(filepath.stat().st_mtime)

        msg_id = data.get("uuid", f"{session_id}_{line_num}")

        record = make_record(
            source="claude-code",
            conversation_id=f"cc_local_{session_id}",
            role=role,
            content=content,
            timestamp=ts,
            msg_index=len(records),
            model=message.get("model"),
            project=project_name,
            conversation_title=project_name,
            message_id=msg_id,
            temporal_precision="day",
        )

        if record:
            records.append(record)

    return finalize_conversation(records)


def ingest(source_path: str, **kwargs) -> list[dict]:
    """
    Ingest all Claude Code conversations from a directory.

    Args:
        source_path: Path to Claude Code projects directory
                     (usually ~/.claude/projects/)

    Returns:
        List of records matching the canonical schema
    """
    projects_dir = Path(source_path).expanduser().resolve()
    if not projects_dir.exists():
        print(f"Claude Code projects not found at {projects_dir}", file=sys.stderr)
        return []

    all_files = list(projects_dir.glob("**/*.jsonl"))
    print(f"Found {len(all_files)} Claude Code JSONL files")

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

    print(f"Ingested {len(all_records)} messages from Claude Code ({errors} errors)")
    return all_records


_ingest = ingest  # preserve module-level function reference


@register
class ClaudeCodeIngester(BaseIngester):
    """Claude Code conversation ingester (plugin)."""

    @property
    def source_type(self) -> str:
        return "claude-code"

    @property
    def display_name(self) -> str:
        return "Claude Code"

    def discover(self) -> list[dict]:
        base = Path("~/.claude/projects").expanduser()
        if not base.exists():
            return []
        files = list(base.glob("**/*.jsonl"))
        if not files:
            return []
        return [{"path": str(base), "count_hint": len(files)}]

    def ingest(self, source_path: str) -> list[dict]:
        return _ingest(source_path)


if __name__ == "__main__":
    records = ingest("~/.claude/projects/")
    print(f"Total records: {len(records)}")
