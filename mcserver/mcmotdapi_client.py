"""Minecraft 服务器 API 客户端，封装 mcmotdapi 的请求与数据解析。

与 McStatusClient 保持相同对外接口（get_server_info / close / async with），
响应归一化为相同结构化字典，下游 formatter / tracker 无感知。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from ipaddress import IPv6Address

import aiohttp
from yarl import URL

from ..server_status.model import normalize_latency_ms
from .base_client import (
    BaseMcClient,
    format_server_address,
    normalize_player_counts,
    normalize_server_host,
)


_MCMOTDAPI_SERVER_TYPE_MAP = {
    "java": "je",
    "bedrock": "be",
}


def _parse_api_port(value: str) -> int:
    """Validate an optional mcmotdapi endpoint port."""

    if not value.isascii() or not value.isdecimal():
        raise ValueError("mcmotdapi host port must be a number")
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("mcmotdapi host port must be between 1 and 65535")
    return port


def _parse_api_host(api_host: str) -> tuple[str, int | None]:
    """Split a bare mcmotdapi host into URL host and optional port."""

    if not isinstance(api_host, str):
        raise ValueError("mcmotdapi host must be a string")
    authority = api_host.strip()
    if not authority or any(character in authority for character in "/?#@"):
        raise ValueError("mcmotdapi host must be a bare host with an optional port")

    if authority.startswith("["):
        closing_bracket = authority.find("]")
        if closing_bracket == -1:
            raise ValueError("mcmotdapi IPv6 host is missing a closing bracket")
        host = authority[1:closing_bracket]
        suffix = authority[closing_bracket + 1 :]
        if suffix and not suffix.startswith(":"):
            raise ValueError("mcmotdapi IPv6 host has an invalid suffix")
        port = _parse_api_port(suffix[1:]) if suffix else None
        try:
            IPv6Address(host)
        except ValueError as exc:
            raise ValueError("mcmotdapi bracketed host must be IPv6") from exc
        return host, port

    try:
        IPv6Address(authority)
    except ValueError:
        host, separator, raw_port = authority.rpartition(":")
        if separator:
            if not host or ":" in host:
                raise ValueError("mcmotdapi IPv6 hosts with a port must use brackets")
            return host, _parse_api_port(raw_port)
        return authority, None
    return authority, None


def _build_api_url(api_host: str, use_ssl: bool) -> URL:
    """Build the mcmotdapi endpoint from a configured bare host and port.

    Args:
        api_host: Bare mcmotdapi host with an optional port.
        use_ssl: Whether to use HTTPS for the upstream request.

    Returns:
        The endpoint URL without query parameters.

    Raises:
        ValueError: If ``api_host`` contains URL path, query, or user-info
            delimiters.
    """
    host, port = _parse_api_host(api_host)
    return URL.build(
        scheme="https" if use_ssl else "http",
        host=host,
        port=port,
        path="/api/status",
    )


class McMotdApiClient(BaseMcClient):
    """Minecraft 服务器查询客户端 — mcmotdapi 源。"""

    def __init__(
        self,
        server_ip: str,
        server_port: int | None,
        server_type: str = "bedrock",
        server_name: str = "Minecraft服务器",
        api_host: str = "motd.minebbs.com",
        use_ssl: bool = True,
        enable_srv: bool = True,
    ):
        super().__init__(server_ip, server_port, server_type, server_name)
        self.api_host = api_host
        self.use_ssl = use_ssl
        self.enable_srv = enable_srv

    async def _fetch_raw_data(self) -> dict | None:
        """调用 mcmotdapi，返回原始 JSON 数据。"""
        allow_missing_port = self.server_type == "java" and self.enable_srv
        if not self.server_ip or (self.server_port is None and not allow_missing_port):
            return None

        stype = _MCMOTDAPI_SERVER_TYPE_MAP.get(self.server_type, "be")
        params = {
            "ip": normalize_server_host(self.server_ip),
            "stype": stype,
        }
        if self.server_port is not None:
            params["port"] = str(self.server_port)
        if self.server_type == "java":
            params["srv"] = "true" if self.enable_srv else "false"
        params["_"] = str(int(time.time() * 1000))

        try:
            api_url = _build_api_url(self.api_host, self.use_ssl)
            session = self._get_session()
            async with session.get(api_url, params=params) as response:
                if response.status != 200:
                    return None
                try:
                    data = await response.json()
                except (aiohttp.ClientError, TypeError, ValueError):
                    return None
                return data if isinstance(data, dict) else None
        except (aiohttp.ClientError, asyncio.TimeoutError, TypeError, ValueError):
            return None

    def _parse_raw_data(self, data: dict) -> dict | None:
        """将 mcmotdapi 原始 JSON 解析为结构化字典，与 McStatusClient 输出格式一致。"""
        status = data.get("status")
        if not isinstance(status, str) or status not in {"online", "offline"}:
            return None

        if status == "online":
            counts = normalize_player_counts(data.get("players"))
            if counts is None:
                return None
            online_players, max_players = counts
        else:
            online_players = 0
            max_players = 0

        server_status = status

        raw_host = data.get("host")
        host = (
            raw_host
            if isinstance(raw_host, str) and raw_host
            else format_server_address(self.server_ip, self.server_port)
        )

        # MOTD — 使用纯文本版本
        raw_motd = data.get("pureMotd")
        motd = raw_motd if isinstance(raw_motd, str) else ""

        # 版本
        raw_version = data.get("version")
        version = raw_version if isinstance(raw_version, str) and raw_version else "未知版本"
        protocol_raw = data.get("protocol")
        try:
            protocol = int(protocol_raw) if protocol_raw is not None else "未知"
        except (ValueError, TypeError):
            protocol = "未知"

        # 地图 — 基岩版有 levelname，Java 版无
        raw_map = data.get("levelname")
        server_map = raw_map if isinstance(raw_map, str) and raw_map else "未知"

        # 软件 — mcmotdapi 不返回此字段
        software = "未知"

        # 图标 — Java 版可能有
        raw_icon = data.get("icon")
        icon = raw_icon if isinstance(raw_icon, str) else ""

        return {
            "status": server_status,
            "name": host,
            "version": version,
            "protocol": protocol,
            "online": online_players,
            "max": max_players,
            "motd": motd,
            "id": "未知",
            "port": self.server_port,
            "icon": icon,
            "software": software,
            "map": server_map,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "latency_ms": normalize_latency_ms(data.get("delay")),
            "reachable": True,
        }
