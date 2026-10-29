"""QQ 官方命令回复的统一构造与发送入口。

官方群聊只有 Markdown 文本消息会解析用户艾特，图文回复则需要先上传素材，
再以单条 Markdown 消息发送。本模块集中封装这些差异，命令处理器只消费稳定
的文本与图片回复接口。日志只记录异常类型，不记录发送者 OpenID、完整 UMO
或媒体地址。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import At, Image, Plain

from .notifier import is_qq_official_platform_name
from .qq_official_media import delete_markdown_image, upload_markdown_image

LogFn = Callable[[str, str], None]


def _event_platform_name(event: AstrMessageEvent) -> str:
    """读取事件平台类型，并兼容不同 AstrBot 版本的事件属性。"""

    get_platform_name = getattr(event, "get_platform_name", None)
    if callable(get_platform_name):
        try:
            platform_name = get_platform_name()
        except Exception:
            return ""
        if isinstance(platform_name, str):
            return platform_name

    platform_meta = getattr(event, "platform_meta", None)
    for candidate in (
        getattr(platform_meta, "name", None),
        getattr(event, "platform_name", None),
    ):
        if isinstance(candidate, str):
            return candidate

    get_platform_id = getattr(event, "get_platform_id", None)
    if callable(get_platform_id):
        try:
            platform_id = get_platform_id()
        except Exception:
            return ""
        if isinstance(platform_id, str):
            return platform_id
    return ""


def is_qq_official_event(event: AstrMessageEvent) -> bool:
    """判断事件是否来自 QQ 官方 WebSocket、Webhook 或历史适配器。"""

    return is_qq_official_platform_name(_event_platform_name(event))


def get_event_sender_id(event: AstrMessageEvent) -> str:
    """提取事件发送者 ID；缺失或宿主异常时返回空字符串。"""

    get_sender_id = getattr(event, "get_sender_id", None)
    if not callable(get_sender_id):
        return ""
    try:
        sender_id = get_sender_id()
    except Exception:
        return ""
    if sender_id is None:
        return ""
    return str(sender_id)


def text_result(
    event: AstrMessageEvent,
    message: str,
    *,
    mention: bool = False,
) -> object:
    """构造统一文本回复；官bot始终艾特，其他平台按调用方保留旧行为。"""

    sender_id = get_event_sender_id(event)
    official = is_qq_official_event(event)
    if not sender_id or (not official and not mention):
        return event.plain_result(message)
    return event.chain_result([At(qq=sender_id), Plain(message)]).use_markdown(official)


def image_result(
    event: AstrMessageEvent,
    image_path: str | Path,
    *,
    mention: bool = False,
) -> object:
    """构造非官方或缺少发送者时使用的单条图片回复。"""

    sender_id = get_event_sender_id(event)
    path = str(image_path)
    if not sender_id or not mention:
        return event.image_result(path)
    return event.chain_result([At(qq=sender_id), Image.fromFileSystem(path)]).use_markdown(False)


def _markdown_image_chain(
    sender_id: str,
    caption: str,
    media_url: str,
) -> MessageChain:
    """构造同时包含艾特、说明文字与图片素材的 Markdown 消息。"""

    content = f"{caption}\n![图片]({media_url})" if caption else f"![图片]({media_url})"
    chain = MessageChain(
        [
            At(qq=sender_id),
            Plain(content),
        ]
    )
    chain.use_markdown_ = True
    return chain


def _markdown_text_chain(sender_id: str, caption: str) -> MessageChain:
    """构造两段式回退中的 Markdown 文本消息。"""

    chain = MessageChain(
        [
            At(qq=sender_id),
            Plain(caption),
        ]
    )
    chain.use_markdown_ = True
    return chain


def _image_only_chain(image_path: str) -> MessageChain:
    """构造两段式回退中的无艾特图片消息。"""

    chain = MessageChain([Image.fromFileSystem(image_path)])
    chain.use_markdown_ = False
    return chain


def _emit_log(log: LogFn | None, level: str, message: str) -> None:
    """隔离日志回调异常，避免日志失败影响命令回复。"""

    if log is None:
        return
    try:
        log(level, message)
    except Exception:
        return


async def _delete_material(
    event: AstrMessageEvent,
    image_id: str,
    log: LogFn | None,
) -> None:
    """清理已上传素材；失败不改变命令结果。"""

    try:
        await delete_markdown_image(getattr(event, "bot", None), image_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _emit_log(
            log,
            "WARN",
            f"QQ 官方图片素材清理失败（{type(exc).__name__}）",
        )


async def _send_split_fallback(
    event: AstrMessageEvent,
    image_path: str,
    *,
    sender_id: str,
    caption: str,
    log: LogFn | None,
) -> None:
    """发送 Markdown 文本和无艾特图片两条消息，单条失败不影响另一条。"""

    try:
        await event.send(_markdown_text_chain(sender_id, caption))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _emit_log(
            log,
            "WARN",
            f"QQ 官方回退文本发送失败（{type(exc).__name__}）",
        )

    try:
        await event.send(_image_only_chain(image_path))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _emit_log(
            log,
            "WARN",
            f"QQ 官方回退图片发送失败（{type(exc).__name__}）",
        )


async def send_image_result(
    event: AstrMessageEvent,
    image_path: str | Path,
    *,
    caption: str,
    log: LogFn | None = None,
) -> bool:
    """发送 QQ 官方图片回复。

    Returns:
        非官方或无发送者事件返回 ``False``，交由调用方使用 ``image_result``；
        官方事件无论合并发送成功还是进入两段式回退均返回 ``True``。
    """

    sender_id = get_event_sender_id(event)
    if not sender_id or not is_qq_official_event(event):
        return False

    path = str(image_path)
    try:
        uploaded = await upload_markdown_image(getattr(event, "bot", None), path)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _emit_log(
            log,
            "WARN",
            f"QQ 官方图片上传失败（{type(exc).__name__}），回退为文本和图片两条消息",
        )
        await _send_split_fallback(
            event,
            path,
            sender_id=sender_id,
            caption=caption,
            log=log,
        )
        return True

    try:
        await event.send(_markdown_image_chain(sender_id, caption, uploaded.url))
    except asyncio.CancelledError:
        await _delete_material(event, uploaded.image_id, log)
        raise
    except Exception as exc:
        _emit_log(
            log,
            "WARN",
            f"QQ 官方图文消息发送失败（{type(exc).__name__}），回退为文本和图片两条消息",
        )
        await _delete_material(event, uploaded.image_id, log)
        await _send_split_fallback(
            event,
            path,
            sender_id=sender_id,
            caption=caption,
            log=log,
        )
        return True

    await _delete_material(event, uploaded.image_id, log)
    _emit_log(log, "INFO", "QQ 官方图片已合并为单条 Markdown 图文消息")
    return True


__all__ = [
    "get_event_sender_id",
    "image_result",
    "is_qq_official_event",
    "send_image_result",
    "text_result",
]
