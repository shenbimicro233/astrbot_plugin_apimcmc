"""群消息发送与 session 构造。

依赖 AstrBot Context / MessageChain。
调用方式与其它子模块一致：from utils.notifier import send_to_group, build_group_session
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain
from astrbot.api.star import Context

# AstrBot MessageType 枚举值是 "GroupMessage"（不是 GROUP_MESSAGE / group）
_GROUP_MESSAGE_TYPE = "GroupMessage"

RunLogFn = Callable[[str, str, str | None], None]


@dataclass
class GroupTarget:
    """发送群消息的目标信息。"""

    context: Context
    group_id: str
    session: str | None = None
    platform_id: str | None = None


def resolve_platform_id(context: Context) -> str:
    """解析 aiocqhttp 平台实例的真实 ID（可能是 default / napcat 等）。"""
    pm = getattr(context, "platform_manager", None)
    if pm is None:
        return "aiocqhttp"

    insts = getattr(pm, "platform_insts", None) or []
    if not insts and hasattr(pm, "get_insts"):
        insts = pm.get_insts() or []

    pid = _first_matching_platform_id(insts, name="aiocqhttp")
    if pid:
        return pid

    pid = _first_matching_platform_id(insts)
    if pid:
        return pid

    return "aiocqhttp"


def _safe_meta_id(platform) -> str:
    """安全地获取平台实例的 meta id，失败返回空字符串。"""
    try:
        meta = platform.meta()
    except Exception:
        return ""
    return str(getattr(meta, "id", "") or "")


def _first_matching_platform_id(platforms, name: str | None = None) -> str:
    """从平台列表中查找第一个匹配 name（不区分大小写）的平台 ID；name 为 None 时返回第一个有效 ID。"""
    for platform in platforms:
        try:
            meta = platform.meta()
        except Exception:
            continue
        meta_name = getattr(meta, "name", "") or ""
        meta_id = getattr(meta, "id", "") or ""
        if name is None:
            if meta_id:
                return str(meta_id)
        elif meta_name == name and meta_id:
            return str(meta_id)
    return ""


def build_group_session(
    context: Context,
    group_id: str,
    platform_id: str | None = None,
) -> str:
    """构造群聊 unified_msg_origin：{platform_id}:GroupMessage:{group_id}"""
    pid = platform_id or resolve_platform_id(context)
    return f"{pid}:{_GROUP_MESSAGE_TYPE}:{group_id}"


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


async def send_to_group(
    target: GroupTarget,
    message: str,
    *,
    run_log: RunLogFn | None = None,
) -> bool:
    """主动向群发送消息。

    Args:
        target: 群目标信息（包含 context、group_id、可选 session/platform_id）。
        message: 要发送的纯文本消息。
        run_log: 可选回调 (level, msg, group_id)，用于写入 WebUI 运行日志缓冲。
                 传入时由本函数打 AstrBot logger；回调只负责缓冲即可。
    """
    group_id = target.group_id
    if not group_id and not target.session:
        _emit_log("❌ 目标群号未配置，无法发送通知", "ERROR", group_id, run_log)
        return False

    try:
        umo = target.session or build_group_session(
            target.context, str(group_id), target.platform_id
        )
        chain = MessageChain([Plain(text=message)])
        await target.context.send_message(umo, chain)
        _emit_log(f"✅ 已发送通知到会话 {umo}", "INFO", group_id, run_log)
        return True
    except Exception as e:
        _emit_log(f"发送通知时出错: {e}", "ERROR", group_id, run_log)
        return False
