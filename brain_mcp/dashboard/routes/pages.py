"""Dashboard HTML page routes."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Dashboard home page. Redirects to onboarding if not set up."""
    templates = request.app.state.templates

    # Check if onboarding is needed
    from brain_mcp.config import get_config
    try:
        cfg = get_config()
        if not cfg.parquet_path.exists():
            # Check onboarding state file too
            from brain_mcp.dashboard.routes.onboarding import _load_onboarding_state
            state = _load_onboarding_state()
            if not state.get("complete", False):
                return RedirectResponse(url="/onboarding", status_code=302)
    except Exception:
        pass

    return templates.TemplateResponse(request, "home.html", {
        "active_page": "home",
    })


@router.get("/onboarding", response_class=HTMLResponse)
async def onboarding_page(request: Request):
    """Onboarding wizard page."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "onboarding.html", {
        "active_page": "home",
    })


@router.get("/search", response_class=HTMLResponse)
async def search_page(request: Request):
    """Search page."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "search.html", {
        "active_page": "search",
    })


@router.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request):
    """Sources management page."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "sources.html", {
        "active_page": "sources",
    })


@router.get("/tools", response_class=HTMLResponse)
async def tools_page(request: Request):
    """Tool status page."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "tools.html", {
        "active_page": "tools",
    })


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Settings page."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "settings.html", {
        "active_page": "settings",
    })


@router.get("/conversation/{conv_id}", response_class=HTMLResponse)
async def conversation_page(request: Request, conv_id: str, highlight: str = ""):
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
            return templates.TemplateResponse(request, "conversation.html", {
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
                "content": content or "",
                "date": date_str,
                "word_count": word_count or 0,
                "has_code": bool(has_code),
                "msg_index": msg_index,
            })

        return templates.TemplateResponse(request, "conversation.html", {
            "active_page": "search",
            "conversation_id": conv_id,
            "title": title,
            "messages": messages,
            "source": source,
            "highlight": highlight,
            "message_count": len(messages),
        })

    except Exception as e:
        return templates.TemplateResponse(request, "conversation.html", {
            "active_page": "search",
            "conversation_id": conv_id,
            "title": "Error",
            "messages": [],
            "source": "",
            "highlight": highlight,
            "error": str(e),
        })
