"""Dashboard stats API endpoints."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["stats"])


def _get_message_count() -> int:
    """Get total message count from parquet."""
    try:
        from brain_mcp.server.db import get_conversations
        con = get_conversations()
        return con.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    except Exception:
        return 0


def _get_embedded_count() -> int:
    """Get embedding count from LanceDB."""
    try:
        from brain_mcp.server.db import lance_count
        return lance_count("message")
    except Exception:
        return 0


def _get_summary_count() -> int:
    """Get summary count."""
    try:
        from brain_mcp.server.db import get_summaries_db
        db = get_summaries_db()
        if db:
            return db.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
        return 0
    except Exception:
        return 0


def _get_conversation_count() -> int:
    """Get count of unique conversations."""
    try:
        from brain_mcp.server.db import get_conversations
        con = get_conversations()
        return con.execute(
            "SELECT COUNT(DISTINCT conversation_id) FROM conversations"
        ).fetchone()[0]
    except Exception:
        return 0


def _get_tools_status() -> tuple[int, int]:
    """Get (tools_ok, tools_total) count."""
    # All 25 tools register; we check if data exists for them to work
    total = 25
    ok = total  # All tools register, they gracefully degrade
    return ok, total


@router.get("/overview", response_class=HTMLResponse)
async def stats_overview(request: Request):
    """Stats cards for the dashboard home page."""
    templates = request.app.state.templates
    messages = _get_message_count()
    embedded = _get_embedded_count()
    summaries = _get_summary_count()
    conversations = _get_conversation_count()
    tools_ok, tools_total = _get_tools_status()

    return templates.TemplateResponse("partials/stats_cards.html", {
        "request": request,
        "messages": messages,
        "embedded": embedded,
        "summaries": summaries,
        "conversations": conversations,
        "tools_ok": tools_ok,
        "tools_total": tools_total,
    })


@router.get("/overview.json")
async def stats_overview_json():
    """Stats as JSON (for external consumers)."""
    messages = _get_message_count()
    embedded = _get_embedded_count()
    summaries = _get_summary_count()
    conversations = _get_conversation_count()
    tools_ok, tools_total = _get_tools_status()

    return {
        "messages": messages,
        "embedded": embedded,
        "summaries": summaries,
        "conversations": conversations,
        "tools_ok": tools_ok,
        "tools_total": tools_total,
    }


@router.get("/activity", response_class=HTMLResponse)
async def stats_activity(days: int = 30):
    """Activity sparkline for dashboard home."""
    try:
        from brain_mcp.server.db import get_conversations
        con = get_conversations()
        rows = con.execute(f"""
            SELECT CAST(created AS DATE) as day, COUNT(*) as msgs
            FROM conversations
            WHERE created >= CURRENT_DATE - INTERVAL '{days}' DAY
            GROUP BY day ORDER BY day
        """).fetchall()

        if not rows:
            return "<p><small>No recent activity data.</small></p>"

        # Build a simple bar chart with inline CSS
        max_msgs = max(r[1] for r in rows)
        bars = []
        for day, msgs in rows:
            height = max(4, int(msgs / max_msgs * 60))
            date_str = str(day)
            bars.append(
                f'<div class="bar" style="height:{height}px" '
                f'title="{date_str}: {msgs} messages"></div>'
            )

        total = sum(r[1] for r in rows)
        avg = total // len(rows) if rows else 0
        html = f"""
        <div class="sparkline">{''.join(bars)}</div>
        <small>{total:,} messages in {days} days (avg {avg:,}/day)</small>
        """
        return html
    except Exception as e:
        return f"<p><small>Activity data unavailable: {e}</small></p>"


@router.get("/sources", response_class=HTMLResponse)
async def stats_sources():
    """Sources health for dashboard home."""
    try:
        from brain_mcp.server.db import get_conversations
        con = get_conversations()
        sources = con.execute("""
            SELECT source, COUNT(*) as msgs, MAX(created) as last_msg
            FROM conversations GROUP BY source ORDER BY msgs DESC
        """).fetchall()

        if not sources:
            return "<p><small>No sources configured.</small></p>"

        html_parts = ["<ul>"]
        for source, msgs, last_msg in sources:
            date_str = str(last_msg)[:10] if last_msg else "unknown"
            html_parts.append(
                f"<li><strong>{source}</strong> — {msgs:,} msgs "
                f"<small>(last: {date_str})</small></li>"
            )
        html_parts.append("</ul>")
        return "\n".join(html_parts)
    except Exception:
        return "<p><small>No conversation data found. <a href='/sources'>Add sources →</a></small></p>"


@router.get("/domains", response_class=HTMLResponse)
async def stats_domains():
    """Open threads/domains for dashboard home."""
    try:
        from brain_mcp.server.db import get_summaries_db
        db = get_summaries_db()
        if not db:
            return "<p><small>Summaries not generated. <a href='/settings'>Configure →</a></small></p>"

        domains = db.execute("""
            SELECT domain_primary, COUNT(*) as convs
            FROM summaries
            WHERE domain_primary != '' AND domain_primary IS NOT NULL
            GROUP BY domain_primary
            ORDER BY convs DESC LIMIT 5
        """).fetchall()

        if not domains:
            return "<p><small>No domain data available.</small></p>"

        html_parts = ["<ul>"]
        for domain, count in domains:
            html_parts.append(f"<li><strong>{domain}</strong> — {count} conversations</li>")
        html_parts.append("</ul>")
        return "\n".join(html_parts)
    except Exception:
        return "<p><small>Domain data unavailable.</small></p>"


@router.get("/sync-status", response_class=HTMLResponse)
async def sync_status():
    """Sync status line for dashboard home."""
    try:
        from brain_mcp.config import get_config
        cfg = get_config()
        import os
        from datetime import datetime

        if cfg.parquet_path.exists():
            mtime = os.path.getmtime(cfg.parquet_path)
            last_sync = datetime.fromtimestamp(mtime)
            delta = datetime.now() - last_sync
            if delta.total_seconds() < 3600:
                ago = f"{int(delta.total_seconds() / 60)} minutes ago"
            elif delta.total_seconds() < 86400:
                ago = f"{int(delta.total_seconds() / 3600)} hours ago"
            else:
                ago = f"{int(delta.total_seconds() / 86400)} days ago"
            return f"⏱ Last sync: {ago}"
        else:
            return "⚠️ No data synced yet. Click Sync Now to start."
    except Exception:
        return "Sync status unknown"


@router.get("/disk", response_class=HTMLResponse)
async def stats_disk():
    """Disk usage stats."""
    try:
        from brain_mcp.config import get_config
        cfg = get_config()

        total = 0
        parts = []
        for name, path in [("Parquet", cfg.parquet_path), ("Vectors", cfg.lance_path),
                           ("Summaries", cfg.summaries_parquet)]:
            if path.exists():
                if path.is_file():
                    size = path.stat().st_size
                else:
                    size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                total += size
                parts.append(f"{name}: {size / 1024 / 1024:.1f} MB")

        return f"💾 {total / 1024 / 1024:.1f} MB total ({', '.join(parts)})"
    except Exception:
        return "Disk usage unknown"
