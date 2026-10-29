"""QQ 官方 Markdown 图片素材上传。

QQ 官方群聊只有 Markdown 消息能解析用户艾特。为了让艾特和查询结果图片
位于同一条消息中，这里按官方素材管理接口上传图片并取得 ``qqbot-media://``
引用。上传流程仅复用宿主 QQ 客户端已有的鉴权 HTTP 层，不修改宿主代码。
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp

MAX_MARKDOWN_IMAGE_BYTES = 10 * 1024 * 1024

_PREPARE_PATH = "/v2/media-images/prepare"
_COMPLETE_PATH = "/v2/media-images/complete"
_IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class QQOfficialMediaError(RuntimeError):
    """QQ 官方 Markdown 图片素材操作失败。"""


@dataclass(frozen=True, slots=True)
class QQOfficialMarkdownImage:
    """已完成上传、可直接写入 Markdown 的 QQ 图片素材。"""

    image_id: str
    url: str


class _QQOfficialMediaRoute:
    """兼容 qq-botpy ``BotHttp.request`` 的最小 Route 实现。"""

    DOMAIN = "api.sgroup.qq.com"
    SANDBOX_DOMAIN = "sandbox.api.sgroup.qq.com"
    SCHEME = "https"

    def __init__(self, method: str, path: str) -> None:
        self.method = method
        self.path = path
        self.is_sandbox = False

    @property
    def url(self) -> str:
        domain = self.SANDBOX_DOMAIN if self.is_sandbox else self.DOMAIN
        return f"{self.SCHEME}://{domain}{self.path}"


def _read_and_hash_image(
    image_path: Path,
) -> tuple[bytes, str, str]:
    """一次读取图片并计算官方素材接口要求的摘要。"""

    data = image_path.read_bytes()
    if not data:
        raise QQOfficialMediaError("image is empty")
    if len(data) > MAX_MARKDOWN_IMAGE_BYTES:
        raise QQOfficialMediaError("image exceeds the 10 MB limit")
    return (
        data,
        hashlib.md5(data, usedforsecurity=False).hexdigest(),
        hashlib.sha1(data, usedforsecurity=False).hexdigest(),
    )


def _image_content_type(image_path: Path) -> str:
    content_type = _IMAGE_CONTENT_TYPES.get(image_path.suffix.lower())
    if content_type is None:
        raise QQOfficialMediaError("unsupported image format")
    return content_type


def _authenticated_http(bot: object) -> Any:
    api = getattr(bot, "api", None)
    http = getattr(api, "_http", None)
    if http is None or not callable(getattr(http, "request", None)):
        raise QQOfficialMediaError("QQ authenticated HTTP client is unavailable")
    return http


async def _request_api(
    bot: object,
    method: str,
    path: str,
    payload: dict[str, object],
) -> dict[str, object]:
    http = _authenticated_http(bot)
    try:
        response = await http.request(
            _QQOfficialMediaRoute(method, path),
            json=payload,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise QQOfficialMediaError(f"QQ media API request failed ({type(exc).__name__})") from None
    if not isinstance(response, dict):
        raise QQOfficialMediaError("QQ media API returned an invalid response")
    return response


def _normalize_etag(value: object, fallback_md5: str) -> str:
    if not isinstance(value, str):
        return fallback_md5
    candidate = value.strip().strip('"').lower()
    if len(candidate) == 32 and all(character in "0123456789abcdef" for character in candidate):
        return candidate
    return fallback_md5


async def _put_image(
    presigned_url: str,
    content_type: str,
    image_bytes: bytes,
    fallback_md5: str,
) -> str:
    """使用预签名 URL 直传图片，不携带 QQ API 鉴权头。"""

    if not presigned_url.startswith(("https://", "http://")):
        raise QQOfficialMediaError("invalid presigned upload URL")

    timeout = aiohttp.ClientTimeout(total=60, sock_connect=10, sock_read=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.put(
                presigned_url,
                data=image_bytes,
                headers={"Content-Type": content_type},
            ) as response:
                status = response.status
                etag = response.headers.get("ETag")
                await response.read()
    except asyncio.CancelledError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise QQOfficialMediaError(
            f"presigned image upload failed ({type(exc).__name__})"
        ) from None

    if status not in {200, 201, 204}:
        raise QQOfficialMediaError(f"presigned image upload returned HTTP {status}")
    return _normalize_etag(etag, fallback_md5)


async def upload_markdown_image(
    bot: object,
    image_path: str | Path,
) -> QQOfficialMarkdownImage:
    """上传本地图片并返回可写入 QQ Markdown 的素材引用。"""

    path = Path(image_path)
    content_type = _image_content_type(path)
    try:
        image_bytes, md5, sha1 = await asyncio.to_thread(_read_and_hash_image, path)
    except asyncio.CancelledError:
        raise
    except QQOfficialMediaError:
        raise
    except OSError as exc:
        raise QQOfficialMediaError(f"failed to read image ({type(exc).__name__})") from None

    prepared = await _request_api(
        bot,
        "POST",
        _PREPARE_PATH,
        {
            "file_name": path.name,
            "file_size": len(image_bytes),
            "content_type": content_type,
            "md5": md5,
            "sha1": sha1,
        },
    )
    upload_id = prepared.get("upload_id")
    presigned_url = prepared.get("presigned_url")
    if not isinstance(upload_id, str) or not upload_id:
        raise QQOfficialMediaError("prepare response is missing upload_id")
    if not isinstance(presigned_url, str) or not presigned_url:
        raise QQOfficialMediaError("prepare response is missing presigned_url")

    uploaded_md5 = await _put_image(
        presigned_url,
        content_type,
        image_bytes,
        md5,
    )
    completed = await _request_api(
        bot,
        "POST",
        _COMPLETE_PATH,
        {
            "upload_id": upload_id,
            "md5": uploaded_md5,
        },
    )
    image_id = completed.get("image_id")
    media_url = completed.get("url")
    if not isinstance(image_id, str) or not image_id:
        raise QQOfficialMediaError("complete response is missing image_id")
    if not isinstance(media_url, str) or not media_url.startswith("qqbot-media://image/v1/"):
        raise QQOfficialMediaError("complete response contains an invalid media URL")
    return QQOfficialMarkdownImage(image_id=image_id, url=media_url)


async def delete_markdown_image(bot: object, image_id: str) -> None:
    """发送成功后尽力删除素材，避免占用单机器人 100 张配额。"""

    if not isinstance(image_id, str) or not image_id:
        raise QQOfficialMediaError("image_id is required")
    encoded_image_id = quote(image_id, safe="")
    await _request_api(
        bot,
        "DELETE",
        f"/v2/media-images/{encoded_image_id}/delete",
        {},
    )


__all__ = [
    "MAX_MARKDOWN_IMAGE_BYTES",
    "QQOfficialMarkdownImage",
    "QQOfficialMediaError",
    "delete_markdown_image",
    "upload_markdown_image",
]
