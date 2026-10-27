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

import fnmatch
import hashlib
import json
import uuid
import zipfile
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import manifest
from .paths import imports_dir, manifest_path

# Namespace for deriving a stable id when an export row carries none.
_NS = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")  # RFC-4122 URL namespace
_UUID_LEN = 36

# Bump when the converter's output for the same export would change: the provenance
# event carries it so a replay of an old import uses the converter that wrote it.
CONVERTER = {"name": "import_chatgpt", "v": 2}  # v2: system and tool nodes are kept


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
        # Every role the export held: system prompts and tool output (code interpreter,
        # browsing) are part of the conversation. Which of them search shows is the
        # dialect view's call, not the recorder's.
        role = (message.get("author") or {}).get("role")
        if not isinstance(role, str) or not role:
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


def _is_conversations_json(name: str) -> bool:
    # Some Windows tools write backslash separators into member names.
    base = PurePosixPath(name.replace("\\", "/")).name
    return fnmatch.fnmatchcase(base, "conversations*.json")


def _looks_like_zip(path: Path) -> bool:
    """A real ZIP, or something that claims to be one (so a truncated download
    is reported as a broken archive rather than as malformed JSON)."""
    if path.suffix.lower() == ".zip" or zipfile.is_zipfile(path):
        return True
    try:
        with path.open("rb") as fh:
            return fh.read(4) == b"PK\x03\x04"
    except OSError:
        return False


# (label, raw bytes, read error, export file, (member path, index) or None)
_Chunk = tuple[str, "bytes | None", "str | None", Path, "tuple[str, int] | None"]


def _export_chunks(path: Path) -> Iterator[_Chunk]:
    """One chunk per conversations*.json behind `path`.

    `path` may be a JSON file, a directory (exports get split), or the ZIP that
    ChatGPT hands out. ZIP members are read in memory and never extracted, so a
    hostile member name cannot write anywhere. The export file and member each
    chunk came from ride along: provenance is recorded from what was actually read.
    """
    if path.is_file() and _looks_like_zip(path):
        try:
            with zipfile.ZipFile(path) as zf:
                # Iterate ZipInfo, not names: a duplicated name would otherwise
                # resolve to its last entry twice and silently drop the first.
                members = sorted((i for i in zf.infolist()
                                  if not i.is_dir() and _is_conversations_json(i.filename)),
                                 key=lambda i: (i.filename, i.header_offset))
                if not members:
                    yield path.name, None, "no conversations*.json in archive", path, None
                for info in members:
                    label = f"{path.name}:{info.filename}"
                    ref = (info.filename, info.header_offset)
                    try:
                        yield label, zf.read(info), None, path, ref
                    except Exception as e:  # zlib.error, EOFError, LZMAError, RuntimeError, …
                        yield label, None, f"{type(e).__name__}: {e}", path, ref
        except Exception as e:  # BadZipFile (truncated download), OSError, NotImplementedError, …
            yield path.name, None, f"{type(e).__name__}: {e} (incomplete download?)", path, None
        return

    if path.is_dir():
        files = sorted(path.glob("conversations*.json"))
        if not files:
            hint = " — pass the .zip itself" if any(path.glob("*.zip")) else ""
            yield path.name, None, f"no conversations*.json in directory{hint}", path, None
    else:
        files = [path]
    for f in files:
        try:
            yield f.name, f.read_bytes(), None, f, (f.name, 0)
        except OSError as e:
            yield f.name, None, f"{type(e).__name__}: {e}", f, (f.name, 0)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _export_entry(path: Path) -> dict:
    """The export file as the user holds it: what `sha256sum` on it would print."""
    entry: dict = {"name": path.name, "members": []}
    try:
        entry["size"] = path.stat().st_size
        entry["sha256"] = _sha256_file(path)
    except OSError as e:
        entry["error"] = f"{type(e).__name__}: {e}"
    return entry


def import_id(exports: list[dict]) -> str:
    """Order-independent identity of an import: the export and member hashes, plus the
    converter version. A re-zipped export with the same member gets its own id, so the
    `sha256sum` of *that* zip is on record."""
    parts = sorted(h for e in exports for h in
                   [e.get("sha256")] + [m.get("sha256") for m in e["members"]] if h)
    return hashlib.sha256("\n".join([f"{CONVERTER['name']}/{CONVERTER['v']}", *parts]).encode()).hexdigest()[:16]


def _last_import_id() -> str | None:
    """The import_id of the newest `import` event in the manifest, if any. A byte
    prefilter keeps this one pass cheap on a manifest with a line per captured chunk."""
    mp = manifest_path()
    if not mp.exists():
        return None
    last = None
    with open(mp, "rb") as f:
        for raw in f:
            if b'"event":"import"' not in raw:  # closing quote: not "import_files"
                continue
            try:
                last = json.loads(raw).get("import_id") or last
            except Exception:
                continue
    return last


def record_import(provenance: dict) -> bool:
    """Append one `import` event to the manifest. Returns whether a line was written.

    The manifest is an event log, so a real re-import is recorded again; only an
    immediate repeat of the previous import (a double start) is suppressed. The event
    states what was written into the origin, not what the floor holds yet — the scan
    that follows decides that. No top-level `blob`/`chunk_sha256`/`origin_gone`: older
    manifest readers must keep ignoring it.
    """
    if not provenance.get("conversations"):
        return False
    iid = provenance["import_id"]
    if _last_import_id() == iid:
        return False
    manifest.append({
        "event": "import", "importer": "chatgpt_export", "lane": "chatgpt_export",
        "import_id": iid, "converter": CONVERTER, "imported_at": manifest.now_iso(),
        "conversations": provenance["conversations"], "exports": provenance["exports"],
    })
    return True


def convert(sources: list[Path], dest: Path | None = None) -> dict:
    """Convert exports into the lane's origin directory.

    Unchanged conversations are left untouched so the scanner sees no rewrite.
    """
    out_dir = dest or imports_dir("chatgpt")
    out_dir.mkdir(parents=True, exist_ok=True)

    census = {"files_read": 0, "conversations": 0, "messages": 0,
              "written": 0, "unchanged": 0, "skipped_empty": 0,
              "dest": str(out_dir), "errors": []}
    exports: dict[Path, dict] = {}

    for source in sources:
        for name, data, error, export_path, ref in _export_chunks(source):
            entry = exports.get(export_path)
            if entry is None:
                entry = exports[export_path] = _export_entry(export_path)
            member = {"path": ref[0], "index": ref[1]} if ref else None
            if data is None:
                census["errors"].append(f"{name}: {error}")
                entry["members"].append({**(member or {"path": export_path.name, "index": 0}),
                                         "error": error})
                continue
            if member is not None:
                member["size"] = len(data)
                member["sha256"] = hashlib.sha256(data).hexdigest()  # before `del data` below
            try:
                conversations = json.loads(data.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                census["errors"].append(f"{name}: {type(e).__name__}: {e}")
                entry["members"].append({**member, "error": f"{type(e).__name__}: {e}"})
                continue
            finally:
                del data  # an export can exceed 1 GB; do not hold the raw bytes too
            if not isinstance(conversations, list):
                census["errors"].append(f"{name}: expected a JSON array of conversations")
                entry["members"].append({**member, "error": "expected a JSON array of conversations"})
                continue

            census["files_read"] += 1
            member["conversations"] = sum(1 for c in conversations if isinstance(c, dict))
            entry["members"].append(member)
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

    export_list = list(exports.values())
    census["provenance"] = {"import_id": import_id(export_list), "exports": export_list,
                            "conversations": census["conversations"]}
    return census
