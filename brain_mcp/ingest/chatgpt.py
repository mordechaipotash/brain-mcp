#!/usr/bin/env python3
"""
brain-mcp — ChatGPT conversation ingester.

Reads ChatGPT export JSON (from Settings → Data Controls → Export Data)
and produces records matching the canonical parquet schema.

The export contains a conversations.json file with all chat history.

Usage:
    python -m ingest.chatgpt
"""

import json
import sys
from pathlib import Path
from datetime import datetime

from .base import BaseIngester
from .registry import register
from .schema import make_record, finalize_conversation


def parse_chatgpt_export(export_path: Path) -> list[dict]:
    """
    Parse a ChatGPT conversations.json export file.

    ChatGPT export format:
    [
      {
        "title": "Conversation Title",
        "create_time": 1700000000.0,
        "update_time": 1700001000.0,
        "mapping": {
          "msg-id": {
            "message": {
              "author": {"role": "user"},
              "content": {"parts": ["message text"]},
              "create_time": 1700000000.0
            },
            "parent": "parent-msg-id",
            "children": ["child-msg-id"]
          }
        }
      }
    ]
    """
    with open(export_path, "r", encoding="utf-8") as f:
        conversations = json.load(f)

    if not isinstance(conversations, list):
        print(f"Expected list in {export_path}, got {type(conversations)}", file=sys.stderr)
        return []

    all_records = []

    for conv in conversations:
        title = conv.get("title", "Untitled")
        conv_id = conv.get("id", conv.get("conversation_id", ""))
        if not conv_id:
            # Generate from create_time + title
            ct = conv.get("create_time", 0)
            conv_id = f"chatgpt_{int(ct)}_{hash(title) % 10000}"

        mapping = conv.get("mapping", {})
        if not mapping:
            continue

        # Extract messages in order by following parent→child chain
        messages = []
        for node_id, node in mapping.items():
            msg = node.get("message")
            if not msg:
                continue

            author = msg.get("author", {})
            role = author.get("role", "")
            if role not in ("user", "assistant"):
                continue

            # Extract content
            content_obj = msg.get("content", {})
            if isinstance(content_obj, dict):
                parts = content_obj.get("parts", [])
                text_parts = []
                for part in parts:
                    if isinstance(part, str):
                        text_parts.append(part)
                    elif isinstance(part, dict) and part.get("content_type") == "text":
                        text_parts.append(part.get("text", ""))
                content = "\n".join(text_parts).strip()
            elif isinstance(content_obj, str):
                content = content_obj
            else:
                continue

            if not content or len(content) < 5:
                continue

            # Parse timestamp
            create_time = msg.get("create_time") or conv.get("create_time")
            if isinstance(create_time, (int, float)) and create_time > 0:
                ts = datetime.fromtimestamp(create_time)
            else:
                ts = datetime.now()

            model_slug = msg.get("metadata", {}).get("model_slug")

            messages.append({
                "role": role,
                "content": content,
                "timestamp": ts,
                "model": model_slug,
                "message_id": node_id,
                "parent_id": node.get("parent"),
            })

        # Sort by timestamp
        messages.sort(key=lambda m: m["timestamp"])

        # Convert to canonical records
        conv_records = []
        for i, msg in enumerate(messages):
            record = make_record(
                source="chatgpt",
                conversation_id=f"chatgpt_{conv_id}",
                role=msg["role"],
                content=msg["content"],
                timestamp=msg["timestamp"],
                msg_index=i,
                model=msg.get("model"),
                conversation_title=title,
                message_id=msg.get("message_id"),
                parent_id=msg.get("parent_id"),
            )
            if record:
                conv_records.append(record)

        all_records.extend(finalize_conversation(conv_records))

    return all_records


def ingest(source_path: str, **kwargs) -> list[dict]:
    """
    Ingest ChatGPT conversations from an export directory.

    Args:
        source_path: Path to ChatGPT export directory
                     (contains conversations.json)

    Returns:
        List of records matching the canonical schema
    """
    export_dir = Path(source_path).expanduser().resolve()

    # Find conversations.json
    if export_dir.is_file() and export_dir.name == "conversations.json":
        json_path = export_dir
    elif (export_dir / "conversations.json").exists():
        json_path = export_dir / "conversations.json"
    else:
        # Search for it
        candidates = list(export_dir.glob("**/conversations.json"))
        if not candidates:
            print(f"No conversations.json found in {export_dir}", file=sys.stderr)
            return []
        json_path = candidates[0]

    print(f"Parsing ChatGPT export: {json_path}")

    try:
        records = parse_chatgpt_export(json_path)
        print(f"Ingested {len(records)} messages from ChatGPT")
        return records
    except Exception as e:
        print(f"Error parsing ChatGPT export: {e}", file=sys.stderr)
        return []


_ingest = ingest  # preserve module-level function reference


@register
class ChatGPTIngester(BaseIngester):
    """ChatGPT conversation ingester (plugin)."""

    @property
    def source_type(self) -> str:
        return "chatgpt"

    @property
    def display_name(self) -> str:
        return "ChatGPT"

    def discover(self) -> list[dict]:
        candidates = [
            "~/Downloads/chatgpt-export/conversations.json",
            "~/.config/brain-mcp/imports/chatgpt/conversations.json",
        ]
        sources = []
        for p in candidates:
            path = Path(p).expanduser()
            if path.exists():
                sources.append({"path": str(path), "count_hint": 1, "size": path.stat().st_size})
        return sources

    def ingest(self, source_path: str) -> list[dict]:
        return _ingest(source_path)


if __name__ == "__main__":
    import sys as _sys
    path = _sys.argv[1] if len(_sys.argv) > 1 else "~/Downloads/chatgpt-export/"
    records = ingest(path)
    print(f"Total records: {len(records)}")
