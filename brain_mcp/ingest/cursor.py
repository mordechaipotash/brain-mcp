#!/usr/bin/env python3
"""
brain-mcp — Cursor IDE conversation ingester.

Reads Cursor conversations from:
1. SQLite state databases (state.vscdb / cursor.vscdb)
   - AI chat data from ItemTable key 'workbench.panel.aichat.view.aichat.chatdata'
   - Composer data from ItemTable keys LIKE 'composerData:%'
2. Agent transcript JSONL files from ~/.cursor/projects/*/agent-transcripts/

Produces records matching the canonical parquet schema.
"""

import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

from .base import BaseIngester
from .registry import register
from .schema import make_record, finalize_conversation


# ─── Discovery paths ────────────────────────────────────────────────────────

VSCDB_PATHS = [
    "~/Library/Application Support/Cursor/User/globalStorage/state.vscdb",
    "~/Library/Application Support/Cursor/User/globalStorage/cursor.vscdb",
    "~/.config/Cursor/User/globalStorage/state.vscdb",
    "~/.config/Cursor/User/globalStorage/cursor.vscdb",
]

AGENT_TRANSCRIPT_GLOBS = [
    "~/.cursor/projects/*/agent-transcripts/*.jsonl",
]


def discover(config=None) -> list[dict]:
    """Discover available Cursor data sources."""
    sources = []

    for p in VSCDB_PATHS:
        path = Path(p).expanduser()
        if path.exists():
            sources.append({
                "type": "cursor",
                "subtype": "vscdb",
                "path": str(path),
                "size": path.stat().st_size,
            })

    for glob_pattern in AGENT_TRANSCRIPT_GLOBS:
        base = Path(glob_pattern.split("*")[0]).expanduser()
        if base.exists():
            full = str(Path(glob_pattern.replace("~", str(Path.home()))))
            import glob as _glob
            files = _glob.glob(full)
            for f in files:
                fp = Path(f)
                sources.append({
                    "type": "cursor",
                    "subtype": "agent-transcript",
                    "path": str(fp),
                    "size": fp.stat().st_size,
                })

    return sources


# ─── SQLite parsers ─────────────────────────────────────────────────────────

def _open_readonly(db_path: str) -> sqlite3.Connection:
    """Open a SQLite database in read-only mode."""
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _parse_aichat_data(raw_json: str) -> list[dict]:
    """
    Parse AI chat data from the 'workbench.panel.aichat.view.aichat.chatdata' key.

    Format: JSON with tabs[].bubbles[] where each bubble has role + text.
    """
    records = []
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return records

    tabs = data if isinstance(data, list) else data.get("tabs", [])

    for tab_idx, tab in enumerate(tabs):
        if not isinstance(tab, dict):
            continue

        bubbles = tab.get("bubbles", [])
        conv_id = tab.get("id", f"cursor_chat_{tab_idx}")
        title = tab.get("title", tab.get("name", "Cursor Chat"))

        conv_records = []
        for i, bubble in enumerate(bubbles):
            if not isinstance(bubble, dict):
                continue

            role = bubble.get("type", bubble.get("role", ""))
            if role in ("ai", "assistant", "bot"):
                role = "assistant"
            elif role in ("human", "user"):
                role = "user"
            else:
                continue

            content = bubble.get("text", bubble.get("content", bubble.get("message", "")))
            if not content or not isinstance(content, str):
                continue

            # Parse timestamp
            ts_raw = bubble.get("timestamp", bubble.get("createdAt"))
            ts = _parse_ts(ts_raw)

            record = make_record(
                source="cursor",
                conversation_id=f"cursor_{conv_id}",
                role=role,
                content=content,
                timestamp=ts,
                msg_index=i,
                conversation_title=str(title),
            )
            if record:
                conv_records.append(record)

        records.extend(finalize_conversation(conv_records))

    return records


def _parse_composer_data(key: str, raw_json: str) -> list[dict]:
    """
    Parse composer data from ItemTable keys matching 'composerData:*'.

    Format: JSON with conversations or messages array.
    """
    records = []
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return records

    if not isinstance(data, dict):
        return records

    # Extract composer ID from key
    composer_id = key.replace("composerData:", "") if ":" in key else key

    # Try different structures Cursor uses
    messages = data.get("messages", data.get("conversation", []))
    if isinstance(messages, dict):
        messages = messages.get("messages", [])

    if not isinstance(messages, list):
        return records

    title = data.get("title", data.get("name", f"Composer {composer_id[:12]}"))

    conv_records = []
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue

        role = msg.get("role", msg.get("type", ""))
        if role in ("ai", "assistant", "bot", "model"):
            role = "assistant"
        elif role in ("human", "user"):
            role = "user"
        else:
            continue

        # Content can be in various fields
        content = msg.get("content", msg.get("text", msg.get("message", "")))
        if isinstance(content, list):
            # Handle content blocks like Claude format
            parts = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
            content = "\n".join(parts)

        if not content or not isinstance(content, str) or len(content.strip()) < 5:
            continue

        ts_raw = msg.get("timestamp", msg.get("createdAt", msg.get("created_at")))
        ts = _parse_ts(ts_raw)

        record = make_record(
            source="cursor",
            conversation_id=f"cursor_composer_{composer_id}",
            role=role,
            content=content,
            timestamp=ts,
            msg_index=i,
            model=msg.get("model"),
            conversation_title=str(title),
            message_id=msg.get("id"),
        )
        if record:
            conv_records.append(record)

    records.extend(finalize_conversation(conv_records))
    return records


def parse_vscdb(db_path: Path) -> list[dict]:
    """Parse a Cursor state.vscdb / cursor.vscdb file."""
    all_records = []

    try:
        conn = _open_readonly(str(db_path))
    except sqlite3.Error as e:
        print(f"  Cannot open {db_path}: {e}", file=sys.stderr)
        return []

    try:
        cursor = conn.cursor()

        # Check if ItemTable exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ItemTable'"
        )
        if not cursor.fetchone():
            conn.close()
            return []

        # Try AI chat data
        try:
            cursor.execute(
                "SELECT value FROM ItemTable WHERE key = ?",
                ("workbench.panel.aichat.view.aichat.chatdata",),
            )
            row = cursor.fetchone()
            if row and row[0]:
                records = _parse_aichat_data(row[0])
                all_records.extend(records)
        except Exception as e:
            print(f"  AI chat parse error: {e}", file=sys.stderr)

        # Try composer data
        try:
            cursor.execute(
                "SELECT key, value FROM ItemTable WHERE key LIKE 'composerData:%'"
            )
            for key, value in cursor.fetchall():
                if value:
                    records = _parse_composer_data(key, value)
                    all_records.extend(records)
        except Exception as e:
            print(f"  Composer data parse error: {e}", file=sys.stderr)

    except Exception as e:
        print(f"  Error reading {db_path}: {e}", file=sys.stderr)
    finally:
        conn.close()

    return all_records


# ─── Agent transcript parser ────────────────────────────────────────────────

def parse_agent_transcript(jsonl_path: Path) -> list[dict]:
    """
    Parse a Cursor agent transcript JSONL file.
    Similar format to Claude Code JSONL.
    """
    records = []
    session_id = jsonl_path.stem
    project_name = jsonl_path.parent.parent.name  # projects/<name>/agent-transcripts/

    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"  Error reading {jsonl_path.name}: {e}", file=sys.stderr)
        return []

    conv_records = []
    for line_num, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Handle both flat and nested message formats
        msg = data.get("message", data)
        role = msg.get("role", data.get("type", ""))

        if role in ("ai", "assistant", "bot", "model"):
            role = "assistant"
        elif role in ("human", "user"):
            role = "user"
        else:
            continue

        # Extract content
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            content = "\n".join(parts)

        if not content or len(content.strip()) < 5:
            continue

        ts_raw = data.get("timestamp", msg.get("timestamp"))
        ts = _parse_ts(ts_raw)

        msg_id = data.get("uuid", data.get("id", f"{session_id}_{line_num}"))

        record = make_record(
            source="cursor",
            conversation_id=f"cursor_agent_{session_id}",
            role=role,
            content=content,
            timestamp=ts,
            msg_index=len(conv_records),
            model=msg.get("model"),
            project=project_name,
            conversation_title=f"Cursor Agent: {project_name}",
            message_id=str(msg_id),
        )
        if record:
            conv_records.append(record)

    return finalize_conversation(conv_records)


# ─── Utilities ───────────────────────────────────────────────────────────────

def _parse_ts(ts_raw) -> datetime:
    """Parse a timestamp from various formats."""
    if ts_raw is None:
        return datetime.now()

    if isinstance(ts_raw, (int, float)):
        try:
            if ts_raw > 1e12:
                return datetime.fromtimestamp(ts_raw / 1000)
            return datetime.fromtimestamp(ts_raw)
        except (ValueError, OSError):
            return datetime.now()

    if isinstance(ts_raw, str):
        try:
            return datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            pass

    return datetime.now()


# ─── Main entry points ──────────────────────────────────────────────────────

def ingest(source_path: str, **kwargs) -> list[dict]:
    """
    Ingest Cursor conversations from all available sources.

    Args:
        source_path: Base path (not heavily used — we scan known locations).
                     Can be a path to a specific .vscdb file or directory.

    Returns:
        List of records matching the canonical schema.
    """
    all_records = []

    source = Path(source_path).expanduser().resolve()

    # If pointed at a specific vscdb file
    if source.is_file() and source.suffix == ".vscdb":
        print(f"Parsing Cursor DB: {source}")
        records = parse_vscdb(source)
        all_records.extend(records)
    else:
        # Scan all known vscdb locations
        for vscdb_path_str in VSCDB_PATHS:
            vscdb_path = Path(vscdb_path_str).expanduser()
            if vscdb_path.exists():
                print(f"Parsing Cursor DB: {vscdb_path}")
                records = parse_vscdb(vscdb_path)
                all_records.extend(records)

    # Scan agent transcripts
    import glob as _glob
    for glob_pattern in AGENT_TRANSCRIPT_GLOBS:
        full = glob_pattern.replace("~", str(Path.home()))
        for filepath in _glob.glob(full):
            fp = Path(filepath)
            try:
                records = parse_agent_transcript(fp)
                all_records.extend(records)
            except Exception as e:
                print(f"  Error parsing transcript {fp.name}: {e}", file=sys.stderr)

    # Also check if source_path itself contains transcripts
    if source.is_dir():
        for jsonl_file in source.glob("**/*.jsonl"):
            try:
                records = parse_agent_transcript(jsonl_file)
                all_records.extend(records)
            except Exception as e:
                print(f"  Error: {jsonl_file.name}: {e}", file=sys.stderr)

    print(f"Ingested {len(all_records)} messages from Cursor")
    return all_records


_ingest = ingest  # preserve module-level function reference
_discover = discover  # preserve module-level function reference


@register
class CursorIngester(BaseIngester):
    """Cursor IDE conversation ingester (plugin)."""

    @property
    def source_type(self) -> str:
        return "cursor"

    @property
    def display_name(self) -> str:
        return "Cursor"

    def discover(self) -> list[dict]:
        return _discover()

    def ingest(self, source_path: str) -> list[dict]:
        return _ingest(source_path)


if __name__ == "__main__":
    records = ingest("~")
    print(f"Total records: {len(records)}")
