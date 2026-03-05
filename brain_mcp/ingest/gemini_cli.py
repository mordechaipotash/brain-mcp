#!/usr/bin/env python3
"""
brain-mcp — Gemini CLI conversation ingester.

Reads Gemini CLI chat session JSON files from ~/.gemini/tmp/*/chats/.

Each session file is a JSON object:
{
  "sessionId": "...",
  "projectHash": "...",
  "startTime": "2026-01-30T05:15:52.768Z",
  "lastUpdated": "2026-01-30T05:15:54.430Z",
  "messages": [
    {
      "id": "...",
      "timestamp": "2026-01-30T05:15:52.768Z",
      "type": "user",          # "user" or "gemini"
      "content": "message text",
      "thoughts": [...],       # optional (gemini only)
      "tokens": {...},         # optional (gemini only)
      "model": "gemini-2.5-flash",  # optional (gemini only)
      "toolCalls": [...]       # optional
    }
  ]
}
"""

import json
import sys
from pathlib import Path
from datetime import datetime

from .schema import make_record, finalize_conversation


GEMINI_BASE = "~/.gemini"
CHAT_GLOB = "tmp/*/chats/*.json"


def discover(config=None) -> list[dict]:
    """Discover available Gemini CLI chat session files."""
    sources = []
    base = Path(GEMINI_BASE).expanduser()
    if not base.exists():
        return sources

    for chat_file in base.glob(CHAT_GLOB):
        sources.append({
            "type": "gemini-cli",
            "path": str(chat_file),
            "size": chat_file.stat().st_size,
        })

    return sources


def _parse_ts(ts_raw) -> datetime:
    """Parse a timestamp string (ISO 8601)."""
    if not ts_raw:
        return datetime.now()

    if isinstance(ts_raw, str):
        try:
            return datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            pass

    if isinstance(ts_raw, (int, float)):
        try:
            if ts_raw > 1e12:
                return datetime.fromtimestamp(ts_raw / 1000)
            return datetime.fromtimestamp(ts_raw)
        except (ValueError, OSError):
            pass

    return datetime.now()


def _extract_project_name(project_hash: str, session_path: Path) -> str:
    """
    Extract a project name from the project hash directory.

    The hash isn't human-readable, so we use the directory structure
    or fall back to a truncated hash.
    """
    # The parent of chats/ is the project hash dir
    project_dir = session_path.parent.parent
    if project_dir.name and project_dir.name != "tmp":
        return f"gemini-{project_dir.name[:12]}"
    return "gemini-cli"


def parse_session_file(session_path: Path) -> list[dict]:
    """Parse a single Gemini CLI session JSON file into canonical records."""
    try:
        with open(session_path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  Error reading {session_path.name}: {e}", file=sys.stderr)
        return []

    if not isinstance(data, dict):
        return []

    session_id = data.get("sessionId", session_path.stem)
    project_hash = data.get("projectHash", "")
    project_name = _extract_project_name(project_hash, session_path)
    messages = data.get("messages", [])

    if not isinstance(messages, list):
        return []

    # Build a title from the first user message
    title = None
    for msg in messages:
        if isinstance(msg, dict) and msg.get("type") == "user":
            content = msg.get("content", "")
            if content:
                title = content[:80].strip()
                if len(content) > 80:
                    title += "..."
                break
    title = title or f"Gemini CLI {session_id[:8]}"

    conv_records = []
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue

        msg_type = msg.get("type", "")

        # Map role
        if msg_type == "user":
            role = "user"
        elif msg_type == "gemini":
            role = "assistant"
        else:
            continue

        content = msg.get("content", "")
        if not content or not isinstance(content, str) or len(content.strip()) < 3:
            continue

        ts = _parse_ts(msg.get("timestamp"))
        model = msg.get("model")  # Only present on gemini messages

        record = make_record(
            source="gemini-cli",
            conversation_id=f"gemini_cli_{session_id}",
            role=role,
            content=content,
            timestamp=ts,
            msg_index=i,
            model=model,
            project=project_name,
            conversation_title=title,
            message_id=msg.get("id", f"{session_id}_{i}"),
        )
        if record:
            conv_records.append(record)

    return finalize_conversation(conv_records)


def ingest(source_path: str, **kwargs) -> list[dict]:
    """
    Ingest Gemini CLI conversations.

    Args:
        source_path: Path to ~/.gemini or a specific session JSON file.

    Returns:
        List of records matching the canonical schema.
    """
    source = Path(source_path).expanduser().resolve()

    all_records = []
    errors = 0

    if source.is_file() and source.suffix == ".json":
        # Single file
        records = parse_session_file(source)
        all_records.extend(records)
    else:
        # Scan directory
        gemini_dir = source
        if not gemini_dir.exists():
            # Fallback to default
            gemini_dir = Path(GEMINI_BASE).expanduser()

        if not gemini_dir.exists():
            print(f"Gemini CLI directory not found at {gemini_dir}", file=sys.stderr)
            return []

        session_files = list(gemini_dir.glob(CHAT_GLOB))
        if not session_files and gemini_dir != Path(GEMINI_BASE).expanduser():
            # Maybe source_path is already inside .gemini
            session_files = list(gemini_dir.glob("**/*.json"))

        print(f"Found {len(session_files)} Gemini CLI session files")

        for session_path in session_files:
            try:
                records = parse_session_file(session_path)
                all_records.extend(records)
            except Exception as e:
                errors += 1
                if errors <= 5:
                    print(f"  Error: {session_path.name}: {e}", file=sys.stderr)

    print(f"Ingested {len(all_records)} messages from Gemini CLI ({errors} errors)")
    return all_records


if __name__ == "__main__":
    records = ingest("~/.gemini")
    print(f"Total records: {len(records)}")
