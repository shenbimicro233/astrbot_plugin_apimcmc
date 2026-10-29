"""群消息发送与 session 构造。

依赖 AstrBot Context / MessageChain。
调用方式与其它子模块一致：from utils.notifier import send_to_group, build_group_session
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot.api.star import Context

if TYPE_CHECKING:
    from .group_config import ConfigManager

# AstrBot MessageType 枚举值是 "GroupMessage"（不是 GROUP_MESSAGE / group）
_GROUP_MESSAGE_TYPE = "GroupMessage"

RunLogFn = Callable[[str, str, str | None], None]

_AIOCQHTTP_PLATFORM = "aiocqhttp"
_QQ_OFFICIAL_PLATFORMS = frozenset(
    {
        "qq_official",
        "qq_official_webhook",
        # 兼容早期文档或部署中曾使用的旧名称。
        "qqofficial",
    }
)


@dataclass
class GroupTarget:
    """发送群消息的目标信息。"""

    context: Context
    group_id: str
    session: str | None = None
    platform_id: str | None = None


def get_available_platforms(context: Context | None) -> list[dict[str, str]]:
    """返回当前 AstrBot 可用的平台实例描述。"""

    platforms: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for platform in _get_platform_instances(context):
        meta = getattr(platform, "meta", None)
        if not callable(meta):
            continue
        try:
            metadata = meta()
            platform_id = getattr(metadata, "id", "")
            platform_name = getattr(metadata, "name", "")
        except Exception:
            continue

        if not isinstance(platform_id, str) or not platform_id:
            continue
        if platform_id in seen_ids:
            continue

        normalized_name = platform_name if isinstance(platform_name, str) else ""
        display = f"{platform_id} ({normalized_name})" if normalized_name else platform_id
        platforms.append(
            {
                "id": platform_id,
                "name": normalized_name,
                "display": display,
            }
        )
        seen_ids.add(platform_id)

    return platforms


def _get_platform_instances(context: Context | None) -> list[object]:
    """返回平台实例列表，并隔离旧版或异常的平台管理器。"""

    if context is None:
        return []

    try:
        platform_manager = getattr(context, "platform_manager", None)
        if platform_manager is None:
            return []

        instances = getattr(platform_manager, "platform_insts", None) or []
        if not instances and callable(getattr(platform_manager, "get_insts", None)):
            try:
                instances = platform_manager.get_insts() or []
            except Exception:
                return []
        return list(instances)
    except Exception:
        return []


def _normalize_platform_name(value: object) -> str:
    """规范化平台类型名称，用于兼容大小写和连字符差异。"""

    if not isinstance(value, str):
        return ""
    return value.strip().lower().replace("-", "_")


def is_qq_official_platform_name(value: object) -> bool:
    """判断平台类型名称是否属于 QQ 官方适配器。"""

    return _normalize_platform_name(value) in _QQ_OFFICIAL_PLATFORMS


def _platform_type_key(platform: dict[str, str]) -> str:
    """返回用于平台类型匹配的规范化名称。"""

    raw_name = platform.get("name") or platform.get("id") or ""
    return _normalize_platform_name(raw_name)


def _is_numeric_group_id(group_id: str | None) -> bool:
    """判断群标识是否为传统纯数字群号。"""

    value = str(group_id or "")
    return bool(value) and value.isascii() and value.isdigit()


def _select_auto_platform(
    platforms: list[dict[str, str]],
    group_id: str | None,
) -> str | None:
    """按群标识形态选择默认平台，未命中时返回首个可用平台。"""

    if not platforms:
        return None

    if group_id:
        if _is_numeric_group_id(group_id):
            for platform in platforms:
                if _platform_type_key(platform) == _AIOCQHTTP_PLATFORM:
                    return platform["id"]
        else:
            for platform in platforms:
                if _platform_type_key(platform) in _QQ_OFFICIAL_PLATFORMS:
                    return platform["id"]

    # Webchat 等无关平台不应在常见群标识路径中抢占路由。
    known_types = {_AIOCQHTTP_PLATFORM, *_QQ_OFFICIAL_PLATFORMS}
    for platform in platforms:
        if _platform_type_key(platform) in known_types:
            return platform["id"]
    return platforms[0]["id"]


def resolve_platform_id(
    context: Context,
    group_id: str | None = None,
    config_mgr: "ConfigManager | None" = None,
    platform_id: str | None = None,
) -> str:
    """解析消息应使用的平台实例 ID。

    优先级依次为调用方显式平台、每群配置、按群标识自动匹配、首个可用平台。
    数字群号优先 `aiocqhttp`，非数字 `group_openid` 优先 QQ 官方平台。
    """

    if platform_id:
        return str(platform_id)

    if config_mgr and group_id:
        config = config_mgr.get(str(group_id))
        configured_platform = getattr(config, "platform_id", None) if config else None
        if configured_platform:
            return str(configured_platform)

    selected = _select_auto_platform(get_available_platforms(context), group_id)
    # 兼容未暴露 platform_manager 的旧 AstrBot 环境。
    return selected or _AIOCQHTTP_PLATFORM


def build_group_session(
    context: Context,
    group_id: str,
    platform_id: str | None = None,
) -> str:
    """构造 `{platform_id}:GroupMessage:{group_id}` 会话字符串。"""

    pid = platform_id or resolve_platform_id(context, group_id=group_id)
    return f"{pid}:{_GROUP_MESSAGE_TYPE}:{group_id}"


def _parse_group_session(session: str | None) -> tuple[str, str] | None:
    """仅接受完整群聊 UMO，并返回平台实例 ID 与最终 session_id。"""

    if not isinstance(session, str):
        return None
    platform_id, separator, remainder = session.partition(":")
    message_type, separator, session_id = remainder.partition(":")
    if (
        not platform_id
        or ":" in platform_id
        or not separator
        or message_type != _GROUP_MESSAGE_TYPE
        or not session_id
    ):
        return None
    return platform_id, session_id


def _session_platform_id(session: str | None) -> str | None:
    """仅接受完整群聊 UMO，并返回其平台实例 ID。"""

    parsed = _parse_group_session(session)
    return parsed[0] if parsed else None


def _emit_log(
    msg: str,
    level: str,
    group_id: str | None,
    run_log: RunLogFn | None,
) -> None:
    """输出 AstrBot 日志，并可选写入 WebUI 运行日志缓冲。"""
    level_key = str(level).lower()
    if level_key == "warn":
        level_key = "warning"
    log_fn = getattr(logger, level_key, logger.info)
    log_fn(msg)
    if run_log:
        run_log(level, msg, group_id)


async def _remember_qq_official_group_scene(
    context: Context,
    session: str,
    group_id: str,
    run_log: RunLogFn | None,
) -> None:
    """在发送前为 QQ 官方群会话补写 scene，且不阻断原发送链路。"""

    parsed = _parse_group_session(session)
    if parsed is None:
        return
    platform_id, session_id = parsed

    for platform in _get_platform_instances(context):
        meta = getattr(platform, "meta", None)
        if not callable(meta):
            continue
        try:
            metadata = meta()
        except Exception:
            continue
        if getattr(metadata, "id", None) != platform_id:
            continue

        platform_name = _normalize_platform_name(getattr(metadata, "name", None))
        if platform_name not in _QQ_OFFICIAL_PLATFORMS:
            return

        remember_scene = getattr(platform, "remember_session_scene", None)
        if not callable(remember_scene):
            _emit_log(
                f"平台 {platform_id} 缺少 remember_session_scene，"
                "无法预置 QQ 官方群 scene；继续尝试发送",
                "WARNING",
                group_id,
                run_log,
            )
            return

        try:
            result = remember_scene(session_id, "group")
            if inspect.isawaitable(result):
                await result
        except Exception:
            _emit_log(
                "预置 QQ 官方群 scene 失败；继续尝试发送",
                "WARNING",
                group_id,
                run_log,
            )
        return

    _emit_log(
        "未找到对应的平台实例，无法预置 QQ 官方群 scene；继续尝试发送",
        "WARNING",
        group_id,
        run_log,
    )


async def send_to_group(
    target: GroupTarget,
    message: str,
    *,
    run_log: RunLogFn | None = None,
    config_mgr: "ConfigManager | None" = None,
) -> bool:
    """主动向群发送消息。

    显式平台与缓存会话同平台时保留完整缓存 UMO；平台冲突时使用配置平台和
    群标识重建会话。没有缓存时按群标识形态选择平台并主动构造会话。
    """

    group_id = str(target.group_id or "")
    session = target.session if isinstance(target.session, str) and target.session else None
    if not group_id and not session:
        _emit_log("❌ 目标群标识未配置，无法发送通知", "ERROR", group_id, run_log)
        return False

    try:
        configured_platform = target.platform_id
        if not configured_platform and config_mgr and group_id:
            config = config_mgr.get(group_id)
            configured_platform = getattr(config, "platform_id", None) if config else None
        configured_platform = str(configured_platform) if configured_platform else None

        cached_platform = _session_platform_id(session)
        if configured_platform:
            if session and cached_platform == configured_platform:
                umo = session
            else:
                umo = build_group_session(target.context, group_id, configured_platform)
        elif session and cached_platform:
            umo = session
        else:
            pid = resolve_platform_id(
                target.context,
                group_id=group_id,
                config_mgr=config_mgr,
            )
            umo = build_group_session(target.context, str(group_id), pid)

        try:
            await _remember_qq_official_group_scene(
                target.context,
                umo,
                group_id,
                run_log,
            )
        except Exception:
            # 兼容性预置不能改变既有发送行为。
            _emit_log(
                "预置 QQ 官方群 scene 时出现意外错误；继续尝试发送",
                "WARNING",
                group_id,
                run_log,
            )

        chain = MessageChain([Plain(text=message)])
        sent = await target.context.send_message(umo, chain)
        platform_label = _session_platform_id(umo) or "unknown"
        if not sent:
            _emit_log(
                f"未找到会话对应的平台 {platform_label}，通知未发送",
                "ERROR",
                group_id,
                run_log,
            )
            return False
        _emit_log(
            f"✅ 已发送群通知（平台 {platform_label}）",
            "INFO",
            group_id,
            run_log,
        )
        return True
    except Exception as exc:
        # 异常正文可能包含平台会话 ID，只记录类型以保留可诊断性。
        error_type = type(exc).__name__
        _emit_log(
            f"发送通知时出错（{error_type}）；请检查平台适配器日志",
            "ERROR",
            group_id,
            run_log,
        )
        return False
