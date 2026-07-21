"""utils 包导出。

统一对外入口，避免直接依赖内部文件。调用方式：
  from .utils import send_to_group, build_group_session, resolve_platform_id, GroupTarget
  from .utils.group_config import ConfigManager, DEFAULT_GROUP_CONFIG
  from .utils.hitokoto import get_hitokoto
"""

from __future__ import annotations

from .notifier import (
    GroupTarget,
    build_group_session,
    resolve_platform_id,
    send_to_group,
)

__all__ = [
    "build_group_session",
    "resolve_platform_id",
    "send_to_group",
    "GroupTarget",
]
