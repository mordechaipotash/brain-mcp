#!/usr/bin/env python3
"""
brain-mcp — ChatGPT data export ingester.

Reads conversations.json from ChatGPT's Settings → Export → ZIP download.
This is an alternative to the chatgpt.py ingester — handles the same file format
but with explicit discovery paths for common download locations.

Format: Array of conversation objects. Each has a `mapping` dict containing
a tree structure (parent→children) which we walk to get messages in order.

Discovery paths:
  - ~/Downloads/conversations.json
  - ~/.config/brain-mcp/imports/conversations.json
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from collections import deque

from .schema import make_record, finalize_conversation


DISCOVERY_PATHS = [
    "~/Downloads/conversations.json",
    "~/.config/brain-mcp/imports/conversations.json",
]


def discover(config=None) -> list[dict]:
    """Discover available ChatGPT export files."""
    sources = []
    for p in DISCOVERY_PATHS:
        path = Path(p).expanduser()
        if path.exists():
            sources.append({
                "type": "chatgpt-export",
                "path": str(path),
                "size": path.stat().st_size,
            })
    return sources


def _walk_tree(mapping: dict) -> list[str]:
    """
    Walk the ChatGPT mapping tree from root to leaves, returning node IDs in order.

    The mapping is a dict of node_id → {message, parent, children}.
    We find the root (node with no parent or parent not in mapping),
    then BFS through children to get chronological order.
    """
    if not mapping:
        return []

    # Find root nodes (no parent, or parent not in mapping)
    all_ids = set(mapping.keys())
    roots = []
    for node_id, node in mapping.items():
        parent = node.get("parent")
        if parent is None or parent not in all_ids:
            roots.append(node_id)

    if not roots:
        # Fallback: just return all keys
        return list(mapping.keys())

    # BFS from roots, following children
    ordered = []
    visited = set()
    queue = deque(roots)

    while queue:
        node_id = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        ordered.append(node_id)

        node = mapping.get(node_id, {})
        children = node.get("children", [])
        for child_id in children:
            if child_id not in visited:
                queue.append(child_id)

    return ordered


def parse_export(export_path: Path) -> list[dict]:
    """
    Parse a ChatGPT conversations.json export file.

    Walks the tree structure to extract messages in correct order.
    """
    try:
        with open(export_path, "r", encoding="utf-8") as f:
            conversations = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error reading {export_path}: {e}", file=sys.stderr)
        return []

    if not isinstance(conversations, list):
        print(f"Expected list in {export_path}, got {type(conversations)}", file=sys.stderr)
        return []

    all_records = []

    for conv in conversations:
        if not isinstance(conv, dict):
            continue

        title = conv.get("title", "Untitled")
        conv_id = conv.get("id", conv.get("conversation_id", ""))
        if not conv_id:
            ct = conv.get("create_time", 0)
            conv_id = f"chatgpt_export_{int(ct)}_{hash(title) % 10000}"

        mapping = conv.get("mapping", {})
        if not mapping:
            continue

        # Walk the tree to get ordered node IDs
        ordered_ids = _walk_tree(mapping)

        # Extract messages in tree order
        messages = []
        for node_id in ordered_ids:
            node = mapping.get(node_id, {})
            msg = node.get("message")
            if not msg:
                continue

            author = msg.get("author", {})
            role = author.get("role", "")
            if role not in ("user", "assistant"):
                continue

            # Extract content from parts
            content_obj = msg.get("content", {})
            if isinstance(content_obj, dict):
                parts = content_obj.get("parts", [])
                text_parts = []
                for part in parts:
                    if isinstance(part, str):
                        text_parts.append(part)
                    elif isinstance(part, dict):
                        # Handle text content type
                        if part.get("content_type") == "text":
                            text_parts.append(part.get("text", ""))
                        elif "text" in part:
                            text_parts.append(part["text"])
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

        # Sort by timestamp as a safety measure (tree walk should already be ordered)
        messages.sort(key=lambda m: m["timestamp"])

        # Convert to canonical records
        conv_records = []
        for i, msg in enumerate(messages):
            record = make_record(
                source="chatgpt-export",
                conversation_id=f"chatgpt_export_{conv_id}",
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
    Ingest ChatGPT conversations from an export file or directory.

    Args:
        source_path: Path to conversations.json or directory containing it.

    Returns:
        List of records matching the canonical schema.
    """
    source = Path(source_path).expanduser().resolve()

    # Direct file
    if source.is_file() and source.name == "conversations.json":
        json_path = source
    elif source.is_file():
        json_path = source  # Trust the caller
    elif source.is_dir() and (source / "conversations.json").exists():
        json_path = source / "conversations.json"
    else:
        # Try discovery paths
        json_path = None
        for p in DISCOVERY_PATHS:
            candidate = Path(p).expanduser()
            if candidate.exists():
                json_path = candidate
                break

        if json_path is None:
            print(f"No conversations.json found at {source} or discovery paths", file=sys.stderr)
            return []

    print(f"Parsing ChatGPT export: {json_path}")
    try:
        records = parse_export(json_path)
        print(f"Ingested {len(records)} messages from ChatGPT export")
        return records
    except Exception as e:
        print(f"Error parsing ChatGPT export: {e}", file=sys.stderr)
        return []


if __name__ == "__main__":
    import sys as _sys
    path = _sys.argv[1] if len(_sys.argv) > 1 else "~/Downloads/conversations.json"
    records = ingest(path)
    print(f"Total records: {len(records)}")
