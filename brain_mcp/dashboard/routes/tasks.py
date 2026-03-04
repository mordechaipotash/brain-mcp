"""Dashboard background task routes with SSE streaming."""

import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

router = APIRouter(tags=["tasks"])


@router.get("/{task_id}")
async def get_task(task_id: str):
    """Poll task status."""
    from brain_mcp.dashboard.tasks import task_manager

    task = task_manager.get(task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    return task.to_dict()


@router.get("/{task_id}/stream")
async def task_stream(task_id: str):
    """SSE stream of task progress updates."""
    from brain_mcp.dashboard.tasks import task_manager

    task = task_manager.get(task_id)
    if not task:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    async def event_generator():
        async for data in task_manager.subscribe(task_id):
            if data.get("keepalive"):
                yield f": keepalive\n\n"
            else:
                yield f"data: {json.dumps(data)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("")
async def list_tasks():
    """List all tasks."""
    from brain_mcp.dashboard.tasks import task_manager
    return [t.to_dict() for t in task_manager.list_tasks()]
