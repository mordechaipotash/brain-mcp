"""Dashboard search API endpoints.

Provides:
- /api/search         — Search with 3 modes: semantic, keyword, summaries
- /api/search/recent  — Recent search history
- /api/conversation/{id} — Full conversation viewer
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["search"])

# Simple file-based search history (last 10)
SEARCH_HISTORY_PATH = Path.home() / ".brain-mcp" / "search_history.json"


def _load_search_history() -> list[dict]:
    """Load recent searches from JSON file."""
    try:
        if SEARCH_HISTORY_PATH.exists():
            data = json.loads(SEARCH_HISTORY_PATH.read_text())
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _save_search(query: str, mode: str, result_count: int, duration_ms: int):
    """Save a search to history (keep last 10)."""
    try:
        history = _load_search_history()
        history.insert(0, {
            "query": query,
            "mode": mode,
            "result_count": result_count,
            "duration_ms": duration_ms,
            "timestamp": datetime.now().isoformat(),
        })
        history = history[:10]
        SEARCH_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        SEARCH_HISTORY_PATH.write_text(json.dumps(history, indent=2))
    except Exception:
        pass  # Non-critical


def _similarity_class(score: float) -> str:
    """Return CSS class for similarity score coloring."""
    if score >= 0.8:
        return "sim-high"
    elif score >= 0.6:
        return "sim-med"
    return "sim-low"


def _similarity_dot(score: float) -> str:
    """Return colored dot emoji for similarity score."""
    if score >= 0.8:
        return "🟢"
    elif score >= 0.6:
        return "🟡"
    return "🔴"


def _truncate(text: str, max_len: int = 250) -> str:
    """Truncate text to max length with ellipsis."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "..."


# ═══════════════════════════════════════════════════════════════════
# SEARCH ENDPOINT — returns HTML partial for htmx
# ═══════════════════════════════════════════════════════════════════

@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = Query("", description="Search query"),
    mode: str = Query("semantic", description="Search mode: semantic, keyword, summaries"),
    source: str = Query("", description="Filter by source"),
    role: str = Query("", description="Filter by role (user/assistant)"),
    date_from: str = Query("", description="Start date YYYY-MM-DD"),
    date_to: str = Query("", description="End date YYYY-MM-DD"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Search conversations/embeddings/summaries. Returns HTML partial."""
    templates = request.app.state.templates

    if not q.strip():
        return templates.TemplateResponse("partials/search_results.html", {
            "request": request,
            "results": [],
            "query": "",
            "mode": mode,
            "duration_ms": 0,
            "total_results": 0,
            "empty": True,
        })

    start = time.time()
    results = []

    try:
        if mode == "semantic":
            results = _search_semantic(q, source, role, date_from, date_to, limit, offset)
        elif mode == "keyword":
            results = _search_keyword(q, source, role, date_from, date_to, limit, offset)
        elif mode == "summaries":
            results = _search_summaries(q, source, date_from, date_to, limit, offset)
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        return templates.TemplateResponse("partials/search_results.html", {
            "request": request,
            "results": [],
            "query": q,
            "mode": mode,
            "duration_ms": duration_ms,
            "total_results": 0,
            "error": str(e),
            "empty": False,
        })

    duration_ms = int((time.time() - start) * 1000)

    # Save to search history
    _save_search(q, mode, len(results), duration_ms)

    return templates.TemplateResponse("partials/search_results.html", {
        "request": request,
        "results": results,
        "query": q,
        "mode": mode,
        "duration_ms": duration_ms,
        "total_results": len(results),
        "has_more": len(results) == limit,
        "offset": offset,
        "limit": limit,
        "empty": False,
    })


def _search_semantic(
    query: str, source: str, role: str,
    date_from: str, date_to: str,
    limit: int, offset: int,
) -> list[dict]:
    """Vector search via LanceDB embeddings."""
    from brain_mcp.server.db import get_embedding, get_lance_db

    embedding = get_embedding(query)
    if embedding is None:
        raise ValueError("Embedding model not available. Install sentence-transformers.")

    db = get_lance_db()
    if not db:
        raise ValueError("Vector database not found. Run the embed pipeline first.")

    tbl = db.open_table("message")

    # Build where filter
    filters = []
    if source:
        # Lance message table doesn't have source, but conversation_id prefix may indicate it
        pass  # We'll filter post-query from conversation data
    if role:
        filters.append(f"role = '{role}'")

    search_query = tbl.search(embedding).limit(limit + offset)
    if filters:
        search_query = search_query.where(" AND ".join(filters))

    df = search_query.to_pandas()

    results = []
    for _, row in df.iterrows():
        sim = 1 / (1 + row.get("_distance", 0))
        content = row.get("content", "")
        conv_id = row.get("conversation_id", "")
        title = row.get("conversation_title", "Untitled")
        year = int(row.get("year", 0))
        month = int(row.get("month", 0))
        created = row.get("created_at", None)

        # Format date
        if created is not None:
            try:
                date_str = str(created)[:10]
            except Exception:
                date_str = f"{year}-{month:02d}" if year else "unknown"
        else:
            date_str = f"{year}-{month:02d}" if year else "unknown"

        # Apply date filters post-query
        if date_from and date_str < date_from:
            continue
        if date_to and date_str > date_to:
            continue

        results.append({
            "similarity": round(sim, 3),
            "similarity_class": _similarity_class(sim),
            "similarity_dot": _similarity_dot(sim),
            "content": content,
            "content_preview": _truncate(content),
            "conversation_id": conv_id,
            "conversation_title": title or "Untitled",
            "date": date_str,
            "role": row.get("role", ""),
            "source": _guess_source(conv_id),
            "type": "message",
        })

    # Apply offset (lance search doesn't support true offset easily)
    if offset > 0:
        results = results[offset:]

    return results[:limit]


def _search_keyword(
    query: str, source: str, role: str,
    date_from: str, date_to: str,
    limit: int, offset: int,
) -> list[dict]:
    """Keyword search via DuckDB full-text over parquet."""
    from brain_mcp.server.db import get_conversations

    con = get_conversations()

    # Build WHERE clause
    conditions = ["content ILIKE ?"]
    params = [f"%{query}%"]

    if source:
        conditions.append("source = ?")
        params.append(source)
    if role:
        conditions.append("role = ?")
        params.append(role)
    if date_from:
        conditions.append("CAST(created AS DATE) >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("CAST(created AS DATE) <= ?")
        params.append(date_to)

    where = " AND ".join(conditions)

    sql = f"""
        SELECT source, conversation_id, conversation_title, content, role,
               created, year, month
        FROM conversations
        WHERE {where}
        ORDER BY created DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    rows = con.execute(sql, params).fetchall()

    results = []
    for row in rows:
        src, conv_id, title, content, msg_role, created, year, month = row
        date_str = str(created)[:10] if created else f"{year}-{month:02d}"

        results.append({
            "similarity": None,
            "similarity_class": "",
            "similarity_dot": "",
            "content": content,
            "content_preview": _truncate(content),
            "conversation_id": conv_id,
            "conversation_title": title or "Untitled",
            "date": date_str,
            "role": msg_role,
            "source": src,
            "type": "message",
        })

    return results


def _search_summaries(
    query: str, source: str,
    date_from: str, date_to: str,
    limit: int, offset: int,
) -> list[dict]:
    """Search v6 structured summaries via LanceDB vectors."""
    from brain_mcp.server.db import get_embedding, get_summaries_lance

    embedding = get_embedding(query)
    if embedding is None:
        raise ValueError("Embedding model not available.")

    sdb = get_summaries_lance()
    if not sdb:
        raise ValueError("Summaries not generated yet. Go to Settings to generate them.")

    tbl = sdb.open_table("summary")

    # Build where filter
    filters = []
    if source:
        filters.append(f"source = '{source}'")

    search_query = tbl.search(embedding).limit(limit + offset)
    if filters:
        search_query = search_query.where(" AND ".join(filters))

    df = search_query.to_pandas()

    results = []
    for _, row in df.iterrows():
        sim = 1 / (1 + row.get("_distance", 0))
        conv_id = row.get("conversation_id", "")
        title = row.get("title", "Untitled")
        summary = row.get("summary", "")
        domain = row.get("domain_primary", "")
        importance = row.get("importance", "")
        thinking_stage = row.get("thinking_stage", "")
        src = row.get("source", "")

        # Parse JSON fields safely
        from brain_mcp.server.db import parse_json_field
        open_questions = parse_json_field(row.get("open_questions", ""))
        decisions = parse_json_field(row.get("decisions", ""))
        concepts = parse_json_field(row.get("concepts", ""))

        results.append({
            "similarity": round(sim, 3),
            "similarity_class": _similarity_class(sim),
            "similarity_dot": _similarity_dot(sim),
            "content": summary,
            "content_preview": _truncate(summary, 300),
            "conversation_id": conv_id,
            "conversation_title": title or "Untitled",
            "date": "",  # Summaries don't have dates directly
            "role": "",
            "source": src,
            "type": "summary",
            "domain": domain,
            "importance": importance,
            "thinking_stage": thinking_stage,
            "open_questions": open_questions[:3],
            "decisions": decisions[:3],
            "concepts": concepts[:5],
        })

    if offset > 0:
        results = results[offset:]

    return results[:limit]


def _guess_source(conversation_id: str) -> str:
    """Guess source from conversation_id prefix."""
    if not conversation_id:
        return "unknown"
    cid = conversation_id.lower()
    if cid.startswith("cc_"):
        return "claude-code"
    elif cid.startswith("chatgpt"):
        return "chatgpt"
    elif cid.startswith("cb_") or cid.startswith("clawdbot"):
        return "clawdbot"
    elif cid.startswith("cd_"):
        return "claude-desktop"
    return "unknown"


# ═══════════════════════════════════════════════════════════════════
# SEARCH HISTORY
# ═══════════════════════════════════════════════════════════════════

@router.get("/search/recent", response_class=HTMLResponse)
async def search_recent(request: Request, limit: int = Query(10, ge=1, le=20)):
    """Return recent search history as HTML partial."""
    templates = request.app.state.templates
    history = _load_search_history()[:limit]
    return templates.TemplateResponse("partials/search_history.html", {
        "request": request,
        "history": history,
    })


# ═══════════════════════════════════════════════════════════════════
# CONVERSATION VIEWER
# ═══════════════════════════════════════════════════════════════════

@router.get("/conversation/{conv_id}", response_class=HTMLResponse)
async def view_conversation(request: Request, conv_id: str, highlight: str = ""):
    """Full conversation viewer page."""
    from brain_mcp.server.db import get_conversations
    templates = request.app.state.templates

    try:
        con = get_conversations()
        rows = con.execute("""
            SELECT source, conversation_title, role, content, created,
                   word_count, has_code, msg_index
            FROM conversations
            WHERE conversation_id = ?
            ORDER BY msg_index ASC
        """, [conv_id]).fetchall()

        if not rows:
            return templates.TemplateResponse("conversation.html", {
                "request": request,
                "active_page": "search",
                "conversation_id": conv_id,
                "title": "Not Found",
                "messages": [],
                "source": "",
                "highlight": highlight,
                "error": f"Conversation {conv_id} not found.",
            })

        messages = []
        title = rows[0][1] or "Untitled"
        source = rows[0][0]

        for row in rows:
            _, _, role, content, created, word_count, has_code, msg_index = row
            date_str = str(created)[:19] if created else ""
            messages.append({
                "role": role,
                "content": content,
                "date": date_str,
                "word_count": word_count,
                "has_code": bool(has_code),
                "msg_index": msg_index,
            })

        return templates.TemplateResponse("conversation.html", {
            "request": request,
            "active_page": "search",
            "conversation_id": conv_id,
            "title": title,
            "messages": messages,
            "source": source,
            "highlight": highlight,
            "message_count": len(messages),
        })

    except Exception as e:
        return templates.TemplateResponse("conversation.html", {
            "request": request,
            "active_page": "search",
            "conversation_id": conv_id,
            "title": "Error",
            "messages": [],
            "source": "",
            "highlight": highlight,
            "error": str(e),
        })


# ═══════════════════════════════════════════════════════════════════
# AVAILABLE FILTER VALUES
# ═══════════════════════════════════════════════════════════════════

@router.get("/search/filters", response_class=HTMLResponse)
async def search_filters(request: Request):
    """Return available filter values (sources, roles)."""
    try:
        from brain_mcp.server.db import get_conversations
        con = get_conversations()
        sources = con.execute(
            "SELECT DISTINCT source FROM conversations ORDER BY source"
        ).fetchall()
        sources = [s[0] for s in sources if s[0]]
    except Exception:
        sources = []

    return HTMLResponse(json.dumps({
        "sources": sources,
        "roles": ["user", "assistant"],
    }))
