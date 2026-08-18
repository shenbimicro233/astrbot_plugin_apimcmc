"""本地服务器状态卡的纯数据契约。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from ipaddress import IPv4Address, IPv6Address, ip_address


class ServerEdition(str, Enum):
    """支持的 Minecraft 版本类型。"""

    JAVA = "java"
    BEDROCK = "bedrock"


class CardStatus(str, Enum):
    """本地状态卡展示的状态。"""

    ONLINE = "online"
    OFFLINE = "offline"
    UNREACHABLE = "unreachable"


class AddressParseError(ValueError):
    """命令地址不是严格的 host[:port] 单个参数时抛出。"""


class EditionParseError(ValueError):
    """命令未选择受支持版本时抛出。"""


@dataclass(frozen=True)
class ParsedAddress:
    """应用版本特定默认值前已校验的命令地址。"""

    host: str
    port: int | None
    has_explicit_port: bool


@dataclass(frozen=True)
class QuerySource:
    """一次请求已完整解析的查询源设置。"""

    source: str
    mcmotdapi_host: str
    mcmotdapi_ssl: bool


@dataclass(frozen=True)
class QueryRequest:
    """完整且可哈希的上游查询契约。"""

    host: str
    port: int | None
    edition: ServerEdition
    enable_srv: bool
    source: QuerySource
    display_address: str


@dataclass(frozen=True)
class StatusCard:
    """单个服务器响应的仅渲染投影。"""

    address: str
    status: CardStatus
    edition: ServerEdition
    software: str | None
    protocol: str | None
    online: int | None
    maximum: int | None
    latency_ms: int | None
    icon: str

    @classmethod
    def unreachable(cls, request: QueryRequest) -> "StatusCard":
        """构建请求失败时使用的精简状态卡。"""

        return cls(
            address=request.display_address,
            status=CardStatus.UNREACHABLE,
            edition=request.edition,
            software=None,
            protocol=None,
            online=None,
            maximum=None,
            latency_ms=None,
            icon="",
        )


_MISSING_TEXT = {
    "",
    "--",
    "none",
    "null",
    "unknown",
    "unknown version",
    "未知",
    "未知版本",
}


def _is_space_or_control(character: str) -> bool:
    codepoint = ord(character)
    return character.isspace() or codepoint < 32 or 127 <= codepoint <= 159


def parse_server_edition(value: object) -> ServerEdition:
    """从命令参数中返回受支持的版本类型。"""

    if isinstance(value, ServerEdition):
        return value
    if not isinstance(value, str):
        raise EditionParseError("server edition must be a string")
    normalized = value.strip().lower()
    try:
        return ServerEdition(normalized)
    except ValueError as exc:
        raise EditionParseError("unsupported server edition") from exc


def format_display_address(host: str, port: int | None) -> str:
    """按非 URL 语义格式化规范化主机与可选端口。"""

    display_host = f"[{host}]" if ":" in host else host
    return f"{display_host}:{port}" if port is not None else display_host


def _parse_port(value: str) -> int:
    if not value or not value.isascii() or not value.isdecimal():
        raise AddressParseError("port must be decimal")
    port = int(value)
    if not 1 <= port <= 65535:
        raise AddressParseError("port must be within 1..65535")
    return port


def _normalize_host(host: str) -> str:
    if not host or len(host) > 253:
        raise AddressParseError("host is empty or too long")
    if any(_is_space_or_control(character) for character in host):
        raise AddressParseError("host contains whitespace or a control character")
    if any(character in host for character in "[]/?#@"):
        raise AddressParseError("host contains a reserved delimiter")

    try:
        parsed = ip_address(host)
    except ValueError:
        numeric_parts = host.split(".")
        if len(numeric_parts) == 4 and all(part.isdecimal() for part in numeric_parts):
            raise AddressParseError("invalid IPv4 address")
        if host.startswith(".") or host.endswith(".") or ".." in host:
            raise AddressParseError("domain host has invalid labels")
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise AddressParseError("domain host is not valid IDNA") from exc
        if any(not label or len(label) > 63 for label in ascii_host.split(".")):
            raise AddressParseError("domain host has invalid labels")
        return ascii_host.lower()

    if isinstance(parsed, IPv6Address):
        return parsed.compressed
    assert isinstance(parsed, IPv4Address)
    return str(parsed)


def parse_server_address(value: object) -> ParsedAddress:
    """解析严格的单个命令地址，不接受 URL 语法。"""

    if not isinstance(value, str):
        raise AddressParseError("address must be a string")
    token = value.strip()
    if not token or token != value:
        raise AddressParseError("address must be one non-empty token")
    if any(_is_space_or_control(character) for character in token):
        raise AddressParseError("address contains whitespace or a control character")
    if "://" in token or any(character in token for character in "/?#@"):
        raise AddressParseError("address must not contain URL syntax")

    if token.startswith("["):
        closing = token.find("]")
        if closing <= 1 or token.find("]", closing + 1) != -1:
            raise AddressParseError("invalid bracketed IPv6 address")
        raw_host = token[1:closing]
        if "%" in raw_host:
            raise AddressParseError("IPv6 zone identifiers are not supported")
        try:
            host = IPv6Address(raw_host).compressed
        except ValueError as exc:
            raise AddressParseError("bracketed host must be IPv6") from exc
        suffix = token[closing + 1 :]
        if not suffix:
            return ParsedAddress(host=host, port=None, has_explicit_port=False)
        if not suffix.startswith(":"):
            raise AddressParseError("invalid bracketed IPv6 suffix")
        return ParsedAddress(host=host, port=_parse_port(suffix[1:]), has_explicit_port=True)

    if "[" in token or "]" in token:
        raise AddressParseError("invalid bracket use")
    if "%" in token:
        raise AddressParseError("IPv6 zone identifiers are not supported")

    try:
        host = IPv6Address(token).compressed
    except ValueError:
        host = ""
    if host:
        return ParsedAddress(host=host, port=None, has_explicit_port=False)

    if ":" in token:
        raw_host, raw_port = token.rsplit(":", 1)
        if not raw_host or ":" in raw_host:
            raise AddressParseError("IPv6 addresses with ports require brackets")
        return ParsedAddress(
            host=_normalize_host(raw_host),
            port=_parse_port(raw_port),
            has_explicit_port=True,
        )

    return ParsedAddress(host=_normalize_host(token), port=None, has_explicit_port=False)


def normalize_latency_ms(value: object) -> int | None:
    """将有限的非负数延迟规范化为整数毫秒。"""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and math.isfinite(value) and value >= 0:
        return int(round(value))
    return None


def _meaningful_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    # 上游版本/软件字段绘制在固定单行中，折叠所有空白，避免意外换行产生多行文本。
    normalized = " ".join(value.split())
    if normalized.lower() in _MISSING_TEXT:
        return None
    return normalized


def _protocol_text(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    return _meaningful_text(value)


def _player_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def project_status_card(
    request: QueryRequest,
    server_data: Mapping[str, object] | None,
) -> StatusCard:
    """将规范化客户端字典投影为固定渲染器契约。"""

    if not isinstance(server_data, Mapping):
        return StatusCard.unreachable(request)
    reachable = server_data.get("reachable", True)
    if type(reachable) is not bool or not reachable:
        return StatusCard.unreachable(request)

    raw_status = server_data.get("status")
    if not isinstance(raw_status, str):
        return StatusCard.unreachable(request)
    try:
        status = CardStatus(raw_status.strip().lower())
    except ValueError:
        return StatusCard.unreachable(request)
    if status is CardStatus.UNREACHABLE:
        return StatusCard.unreachable(request)

    icon = server_data.get("icon")
    return StatusCard(
        address=request.display_address,
        status=status,
        edition=request.edition,
        software=_meaningful_text(server_data.get("software"))
        or _meaningful_text(server_data.get("version")),
        protocol=_protocol_text(server_data.get("protocol")),
        online=_player_count(server_data.get("online")),
        maximum=_player_count(server_data.get("max")),
        latency_ms=normalize_latency_ms(server_data.get("latency_ms")),
        icon=icon if isinstance(icon, str) else "",
    )


__all__ = [
    "AddressParseError",
    "CardStatus",
    "EditionParseError",
    "ParsedAddress",
    "QueryRequest",
    "QuerySource",
    "ServerEdition",
    "StatusCard",
    "format_display_address",
    "normalize_latency_ms",
    "parse_server_address",
    "parse_server_edition",
    "project_status_card",
]
