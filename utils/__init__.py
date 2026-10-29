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
    get_available_platforms,
    resolve_platform_id,
    send_to_group,
)
from .qq_official_reply import (
    get_event_sender_id,
    image_result,
    is_qq_official_event,
    send_image_result,
    text_result,
)

__all__ = [
    "build_group_session",
    "get_event_sender_id",
    "get_available_platforms",
    "image_result",
    "is_qq_official_event",
    "resolve_platform_id",
    "send_image_result",
    "send_to_group",
    "GroupTarget",
    "text_result",
]
