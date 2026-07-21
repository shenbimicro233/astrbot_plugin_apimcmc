"""监控运行日志环形缓冲（供 WebUI 增量拉取，不落盘）。

调用方式：from monitor.run_log import RunLogBuffer
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Deque

DEFAULT_RUN_LOG_MAX = 300


class RunLogBuffer:
    """内存环形日志，id 单调递增，支持 since_id 增量拉取。"""

    def __init__(self, maxlen: int = DEFAULT_RUN_LOG_MAX):
        self._items: Deque[dict] = deque(maxlen=maxlen)
        self._next_id = 1
        self._maxlen = maxlen

    def append(self, level: str, msg: str, group_id: str | None = None) -> dict:
        entry = {
            "id": self._next_id,
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": (level or "INFO").upper(),
            "msg": str(msg),
            "group_id": str(group_id) if group_id else None,
        }
        self._next_id += 1
        self._items.append(entry)
        return entry

    def clear(self) -> None:
        self._items.clear()

    def list_since(self, since_id: int = 0, limit: int = 100) -> tuple[list[dict], int]:
        limit = max(1, min(int(limit or 100), self._maxlen))
        since_id = max(0, int(since_id or 0))
        items = [e for e in self._items if e["id"] > since_id]
        if len(items) > limit:
            items = items[-limit:]
        next_id = self._items[-1]["id"] + 1 if self._items else self._next_id
        return items, next_id
