"""
Background task runner for the dashboard.

Long-running operations (sync, embed, summarize) run as background tasks
within the FastAPI process. Tasks push progress updates to SSE subscribers.
"""

import asyncio
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import AsyncGenerator, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    id: str
    name: str
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0  # 0.0 to 1.0
    message: str = ""
    created: datetime = field(default_factory=datetime.now)
    started: Optional[datetime] = None
    finished: Optional[datetime] = None
    error: Optional[str] = None
    _cancel: threading.Event = field(default_factory=threading.Event)

    def cancel(self):
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "created": self.created.isoformat() if self.created else None,
            "started": self.started.isoformat() if self.started else None,
            "finished": self.finished.isoformat() if self.finished else None,
            "error": self.error,
        }


class TaskManager:
    """Simple in-memory task manager. Fine for local single-user."""

    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def create(self, name: str) -> Task:
        task = Task(id=str(uuid.uuid4())[:8], name=name)
        self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[Task]:
        return list(self._tasks.values())

    async def update(self, task_id: str, **kwargs):
        task = self._tasks.get(task_id)
        if not task:
            return
        for k, v in kwargs.items():
            setattr(task, k, v)
        # Notify SSE subscribers
        for queue in self._subscribers.get(task_id, []):
            try:
                queue.put_nowait(task.to_dict())
            except asyncio.QueueFull:
                pass

    def update_sync(self, task_id: str, **kwargs):
        """Thread-safe update from background threads."""
        task = self._tasks.get(task_id)
        if not task:
            return
        for k, v in kwargs.items():
            setattr(task, k, v)
        # Schedule async notification if we have a loop
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._notify_subscribers(task_id), self._loop
            )

    async def _notify_subscribers(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            return
        for queue in self._subscribers.get(task_id, []):
            try:
                queue.put_nowait(task.to_dict())
            except asyncio.QueueFull:
                pass

    async def subscribe(self, task_id: str) -> AsyncGenerator[dict, None]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._subscribers.setdefault(task_id, []).append(queue)
        try:
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield data
                    if data.get("status") in ("done", "failed", "cancelled"):
                        break
                except asyncio.TimeoutError:
                    # Send keepalive
                    yield {"keepalive": True}
        finally:
            if task_id in self._subscribers:
                try:
                    self._subscribers[task_id].remove(queue)
                except ValueError:
                    pass

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        """Set the event loop for thread-safe updates."""
        self._loop = loop


# Global singleton
task_manager = TaskManager()
