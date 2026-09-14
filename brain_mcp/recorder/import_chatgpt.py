"""ChatGPT data-export → origin JSONL, for the `chatgpt_export` watch lane.

ChatGPT has no local session files to watch, so there is nothing for a lane to
scan. This converter manufactures that origin once, from the ZIP export that
Settings → Data Controls → Export Data produces: one append-only
`<conversation_id>.jsonl` per conversation under `~/.brain/imports/chatgpt/`.
From there the normal scanner path applies — the lane is an ordinary watch lane
and the floor stays the floor.

The export's `mapping` is a tree, not a list: regenerated answers and edited
prompts create sibling branches, and the conversation you saw on screen is one
root-to-leaf path through it. A recorder must not silently pick one path and
drop the rest, so every message node is emitted, ordered by its own clock. The
branch structure survives in `parent_id`, so a reader can reconstruct any path.

Determinism is a requirement, not a nicety: the floor is content-hashed, so
re-running an import over an unchanged export must produce byte-identical files
or every run would open a new witness generation. Hence sorted keys, explicit
UTC, no `datetime.now()`, and no `hash()` (salted per process).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from .paths import imports_dir

# Namespace for deriving a stable id when an export row carries none.
_NS = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")  # RFC-4122 URL namespace
_UUID_LEN = 36


def _iso(epoch: float | int | None) -> str | None:
    """ChatGPT stamps epoch seconds; the floor reads a top-level ISO `timestamp`."""
    if not isinstance(epoch, (int, float)) or epoch <= 0:
        return None
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()


def _conversation_id(conv: dict) -> str:
    """The export's own UUID when it has one; otherwise a deterministic UUIDv5.

    The filename carries the session id, and the lane's pattern expects a UUID,
    so a fallback must still look like one — and must be identical on every run.
    """
    for key in ("conversation_id", "id"):
        val = conv.get(key)
        if isinstance(val, str) and len(val) == _UUID_LEN:
            return val
    seed = f"chatgpt:{conv.get('title')}:{conv.get('create_time')}"
    return str(uuid.uuid5(_NS, seed))


def _text_parts(message: dict) -> list[dict]:
    """Flatten ChatGPT's content shapes into the block array `text_of` reads.

    Empty messages are kept out (they carry no text at all), but short ones are
    kept: "yes" is a turn that happened, and a recorder does not judge length.
    """
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, dict):
        return []

    blocks: list[dict] = []
    for part in content.get("parts") or []:
        if isinstance(part, str):
            if part:
                blocks.append({"type": "text", "text": part})
        elif isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str) and text:
                blocks.append({"type": "text", "text": text})

    # Code interpreter / tool payloads live outside `parts`.
    if not blocks:
        for key in ("text", "result"):
            val = content.get(key)
            if isinstance(val, str) and val:
                blocks.append({"type": "text", "text": val})
    return blocks


def conversation_lines(conv: dict) -> tuple[str, list[str]]:
    """One conversation → (conversation_id, JSONL lines in clock order)."""
    conv_id = _conversation_id(conv)
    title = conv.get("title")
    mapping = conv.get("mapping") or {}

    rows = []
    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        role = (message.get("author") or {}).get("role")
        if role not in ("user", "assistant"):
            continue
        blocks = _text_parts(message)
        if not blocks:
            continue
        created = message.get("create_time") or conv.get("create_time")
        rows.append((created or 0.0, node_id, node, message, role, blocks))

    # The node's own clock orders the transcript; node_id breaks ties so that
    # two runs over the same export cannot disagree.
    rows.sort(key=lambda r: (r[0], r[1]))

    lines = []
    for created, node_id, node, message, role, blocks in rows:
        record = {
            "type": "message",
            "timestamp": _iso(created),
            "conversation_id": conv_id,
            "title": title,
            "node_id": node_id,
            "parent_id": node.get("parent"),
            "message": {
                "role": role,
                "model": (message.get("metadata") or {}).get("model_slug"),
                "content": blocks,
            },
        }
        lines.append(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return conv_id, lines


def _export_files(path: Path) -> Iterator[Path]:
    """A file, or every conversations*.json in a directory (exports get split)."""
    if path.is_file():
        yield path
        return
    yield from sorted(path.glob("conversations*.json"))


def convert(sources: list[Path], dest: Path | None = None) -> dict:
    """Convert exports into the lane's origin directory.

    Unchanged conversations are left untouched so the scanner sees no rewrite.
    """
    out_dir = dest or imports_dir("chatgpt")
    out_dir.mkdir(parents=True, exist_ok=True)

    census = {"files_read": 0, "conversations": 0, "messages": 0,
              "written": 0, "unchanged": 0, "skipped_empty": 0,
              "dest": str(out_dir), "errors": []}

    for source in sources:
        for export in _export_files(source):
            try:
                conversations = json.loads(export.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                census["errors"].append(f"{export.name}: {type(e).__name__}: {e}")
                continue
            if not isinstance(conversations, list):
                census["errors"].append(f"{export.name}: expected a JSON array of conversations")
                continue

            census["files_read"] += 1
            for conv in conversations:
                if not isinstance(conv, dict):
                    continue
                census["conversations"] += 1
                conv_id, lines = conversation_lines(conv)
                if not lines:
                    census["skipped_empty"] += 1
                    continue
                census["messages"] += len(lines)

                target = out_dir / f"{conv_id}.jsonl"
                payload = "\n".join(lines) + "\n"
                if target.exists() and target.read_text(encoding="utf-8") == payload:
                    census["unchanged"] += 1
                    continue
                # Atomic, and dot-prefixed while partial: the scanner ignores dotfiles.
                tmp = target.with_name(f".{target.name}.tmp")
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(target)
                census["written"] += 1

    return census
