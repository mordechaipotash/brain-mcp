"""Dashboard sources management API routes.

Provides:
- GET  /api/sources              — List all configured sources with stats
- GET  /api/sources/discover     — Auto-detect sources on disk
- POST /api/sources/sync-all     — Trigger full sync (background task)
- POST /api/sources/{idx}/sync   — Trigger sync for one source
- GET  /api/sources/cards        — HTML partial for source cards
"""

import asyncio
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["sources"])


def _get_source_stats() -> list[dict]:
    """Get stats for each configured source."""
    from brain_mcp.config import get_config

    cfg = get_config()
    sources = []

    # Get per-source message counts from parquet
    source_counts = {}
    try:
        from brain_mcp.server.db import get_conversations
        con = get_conversations()
        rows = con.execute("""
            SELECT source, COUNT(*) as msgs,
                   COUNT(DISTINCT conversation_id) as convs,
                   MAX(created) as last_msg
            FROM conversations
            GROUP BY source
        """).fetchall()
        for src, msgs, convs, last_msg in rows:
            source_counts[src] = {
                "messages": msgs,
                "conversations": convs,
                "last_message": str(last_msg)[:19] if last_msg else None,
            }
    except Exception:
        pass

    # Map config source types to parquet source names
    SOURCE_TYPE_MAP = {
        "claude-code": "claude-code",
        "clawdbot": "clawdbot",
        "chatgpt": "chatgpt",
        "claude-desktop": "claude-desktop",
    }

    for i, src in enumerate(cfg.sources or []):
        src_type = src.type if hasattr(src, "type") else src.get("type", "")
        src_path = src.path if hasattr(src, "path") else src.get("path", "")
        src_name = (src.name if hasattr(src, "name") else src.get("name")) or src_type

        parquet_name = SOURCE_TYPE_MAP.get(src_type, src_type)
        stats = source_counts.get(parquet_name, {})

        # Check if path exists
        resolved = Path(src_path).expanduser()
        path_exists = resolved.exists()

        sources.append({
            "index": i,
            "type": src_type,
            "name": src_name,
            "path": src_path,
            "path_exists": path_exists,
            "messages": stats.get("messages", 0),
            "conversations": stats.get("conversations", 0),
            "last_message": stats.get("last_message"),
            "enabled": True,
        })

    # Also include any sources found in parquet but not in config
    configured_types = {s.type if hasattr(s, "type") else s.get("type", "") for s in (cfg.sources or [])}
    for src_name, stats in source_counts.items():
        if src_name not in configured_types and src_name not in SOURCE_TYPE_MAP.values():
            sources.append({
                "index": -1,
                "type": src_name,
                "name": src_name,
                "path": "(auto-detected from data)",
                "path_exists": True,
                "messages": stats.get("messages", 0),
                "conversations": stats.get("conversations", 0),
                "last_message": stats.get("last_message"),
                "enabled": True,
            })

    return sources


def _get_last_sync_time() -> str:
    """Get the parquet file modification time as last sync."""
    try:
        from brain_mcp.config import get_config
        cfg = get_config()
        if cfg.parquet_path.exists():
            mtime = os.path.getmtime(cfg.parquet_path)
            dt = datetime.fromtimestamp(mtime)
            delta = datetime.now() - dt
            if delta.total_seconds() < 3600:
                return f"{int(delta.total_seconds() / 60)} minutes ago"
            elif delta.total_seconds() < 86400:
                return f"{int(delta.total_seconds() / 3600)} hours ago"
            else:
                return f"{int(delta.total_seconds() / 86400)} days ago"
    except Exception:
        pass
    return "never"


# ═══════════════════════════════════════════════════════════════════
# SOURCE LIST
# ═══════════════════════════════════════════════════════════════════

@router.get("", response_class=JSONResponse)
async def list_sources():
    """List all configured sources with stats as JSON."""
    return _get_source_stats()


@router.get("/cards", response_class=HTMLResponse)
async def source_cards(request: Request):
    """Source cards HTML partial for htmx."""
    templates = request.app.state.templates
    sources = _get_source_stats()
    last_sync = _get_last_sync_time()

    return templates.TemplateResponse("partials/source_cards.html", {
        "request": request,
        "sources": sources,
        "last_sync": last_sync,
    })


# ═══════════════════════════════════════════════════════════════════
# AUTO-DISCOVER
# ═══════════════════════════════════════════════════════════════════

@router.get("/discover", response_class=HTMLResponse)
async def discover_sources(request: Request):
    """Auto-detect conversation sources on disk. Returns HTML partial."""
    templates = request.app.state.templates
    discovered = []

    checks = [
        ("Claude Code", "claude-code", Path.home() / ".claude" / "projects"),
        ("Clawdbot", "clawdbot", Path.home() / ".clawdbot" / "agents"),
        ("Claude Desktop", "claude-desktop",
         Path.home() / "Library" / "Application Support" / "Claude" / "chat_conversations"),
    ]

    for name, src_type, path in checks:
        if path.exists():
            # Count session files
            files = list(path.rglob("*.jsonl"))
            if files:
                discovered.append({
                    "name": name,
                    "type": src_type,
                    "path": str(path),
                    "session_count": len(files),
                })

    # Check for ChatGPT exports
    downloads = Path.home() / "Downloads"
    if downloads.exists():
        chatgpt_files = list(downloads.glob("chatgpt*.json")) + \
                        list(downloads.glob("conversations.json"))
        if chatgpt_files:
            discovered.append({
                "name": "ChatGPT Export",
                "type": "chatgpt",
                "path": str(chatgpt_files[0]),
                "session_count": len(chatgpt_files),
            })

    return templates.TemplateResponse("partials/discover_sources.html", {
        "request": request,
        "discovered": discovered,
    })


# ═══════════════════════════════════════════════════════════════════
# SYNC TRIGGERS
# ═══════════════════════════════════════════════════════════════════

@router.post("/sync-all", response_class=HTMLResponse)
async def sync_all(request: Request):
    """Trigger a full sync (all sources). Returns task ID for SSE tracking."""
    from brain_mcp.dashboard.tasks import task_manager, TaskStatus

    # Check if a sync is already running
    for task in task_manager.list_tasks():
        if task.name.startswith("sync") and task.status == TaskStatus.RUNNING:
            return HTMLResponse(
                f'<div id="sync-progress" data-task-id="{task.id}">'
                f'<p aria-busy="true">Sync already running...</p>'
                f'</div>'
            )

    task = task_manager.create("sync-all")

    # Store event loop for thread-safe updates
    loop = asyncio.get_event_loop()
    task_manager.set_loop(loop)

    def run_sync():
        try:
            task_manager.update_sync(task.id,
                                     status=TaskStatus.RUNNING,
                                     started=datetime.now(),
                                     message="Starting sync...")

            from brain_mcp.config import get_config
            from brain_mcp.ingest import run_all_ingesters

            cfg = get_config()
            task_manager.update_sync(task.id,
                                     progress=0.1,
                                     message="Ingesting conversations...")

            total = run_all_ingesters(cfg)

            task_manager.update_sync(task.id,
                                     progress=0.9,
                                     message=f"Ingested {total:,} messages")

            # Reset DB connections so they pick up new data
            _reset_db_connections()

            task_manager.update_sync(task.id,
                                     status=TaskStatus.DONE,
                                     progress=1.0,
                                     finished=datetime.now(),
                                     message=f"Sync complete! {total:,} messages")

        except Exception as e:
            task_manager.update_sync(task.id,
                                     status=TaskStatus.FAILED,
                                     finished=datetime.now(),
                                     error=str(e),
                                     message=f"Sync failed: {e}")

    thread = threading.Thread(target=run_sync, daemon=True)
    thread.start()

    return HTMLResponse(
        f'<div id="sync-progress" '
        f'hx-get="/api/tasks/{task.id}" '
        f'hx-trigger="every 2s" '
        f'hx-target="#sync-progress" '
        f'hx-swap="innerHTML">'
        f'<p aria-busy="true">Sync started... <small>(task {task.id})</small></p>'
        f'</div>'
    )


@router.post("/{idx}/sync", response_class=HTMLResponse)
async def sync_source(request: Request, idx: int):
    """Trigger sync for a single source."""
    from brain_mcp.dashboard.tasks import task_manager, TaskStatus

    task = task_manager.create(f"sync-source-{idx}")
    loop = asyncio.get_event_loop()
    task_manager.set_loop(loop)

    def run_single_sync():
        try:
            from brain_mcp.config import get_config
            cfg = get_config()

            if idx < 0 or idx >= len(cfg.sources):
                task_manager.update_sync(task.id,
                                         status=TaskStatus.FAILED,
                                         error=f"Invalid source index: {idx}")
                return

            source = cfg.sources[idx]
            src_name = (source.name if hasattr(source, "name") else source.get("name")) or "source"

            task_manager.update_sync(task.id,
                                     status=TaskStatus.RUNNING,
                                     started=datetime.now(),
                                     message=f"Syncing {src_name}...")

            # Run just this source's ingester
            from brain_mcp.ingest.claude_code import ingest as ingest_cc
            from brain_mcp.ingest.clawdbot import ingest as ingest_cb
            from brain_mcp.ingest.chatgpt import ingest as ingest_chatgpt

            INGESTERS = {
                "claude-code": ingest_cc,
                "clawdbot": ingest_cb,
                "chatgpt": ingest_chatgpt,
            }

            src_type = source.type if hasattr(source, "type") else source.get("type", "")
            src_path = source.path if hasattr(source, "path") else source.get("path", "")

            ingester = INGESTERS.get(src_type)
            if not ingester:
                task_manager.update_sync(task.id,
                                         status=TaskStatus.FAILED,
                                         error=f"No ingester for type: {src_type}")
                return

            records = ingester(src_path)

            task_manager.update_sync(task.id,
                                     progress=0.8,
                                     message=f"Got {len(records):,} records, rebuilding parquet...")

            # For a single-source re-ingest, we need to merge with existing data
            # For simplicity, we run the full ingest (all sources)
            from brain_mcp.ingest import run_all_ingesters
            total = run_all_ingesters(cfg)

            _reset_db_connections()

            task_manager.update_sync(task.id,
                                     status=TaskStatus.DONE,
                                     progress=1.0,
                                     finished=datetime.now(),
                                     message=f"Done! {total:,} total messages")

        except Exception as e:
            task_manager.update_sync(task.id,
                                     status=TaskStatus.FAILED,
                                     finished=datetime.now(),
                                     error=str(e),
                                     message=f"Failed: {e}")

    thread = threading.Thread(target=run_single_sync, daemon=True)
    thread.start()

    return HTMLResponse(
        f'<p aria-busy="true" id="source-sync-{idx}">Syncing... '
        f'<small>(task {task.id})</small></p>'
    )


def _reset_db_connections():
    """Reset cached DB connections so they pick up new parquet data."""
    import brain_mcp.server.db as db_mod
    db_mod._conversations_db = None
    db_mod._summaries_db = None
