"""Minecraft 服务器 API 客户端基类，提供共享的 aiohttp session 管理。"""

from __future__ import annotations

import aiohttp


def normalize_server_host(server_ip: str) -> str:
    """Remove IPv6 URL brackets from a configured server host.

    The configured target may use either raw or bracketed IPv6. Query-string
    parameters carry the raw address, whereas route addresses add brackets
    again when needed.
    """

    if server_ip.startswith("[") and server_ip.endswith("]"):
        return server_ip[1:-1]
    return server_ip


def format_server_address(server_ip: str, server_port: int | None) -> str:
    """Return an unambiguous host-and-port address for an upstream route.

    Args:
        server_ip: Configured domain, IPv4 address, or IPv6 literal.
        server_port: Configured Minecraft port, when specified.

    Returns:
        An address suitable for use as one upstream path segment.

    Raw IPv6 must be bracketed, while an already bracketed IPv6 literal must
    not receive a second pair of brackets.
    """
    host = normalize_server_host(server_ip)
    if ":" in host:
        host = f"[{host}]"
    return f"{host}:{server_port}" if server_port is not None else host


def normalize_player_counts(players: object) -> tuple[int, int] | None:
    """Normalize an online response's aggregate player counts.

    Args:
        players: The parsed upstream ``players`` object.

    Returns:
        A non-negative ``(online, maximum)`` pair, or ``None`` when the
        required online count is malformed.

    A missing capacity is represented as zero because both APIs can omit it.
    """
    if not isinstance(players, dict):
        return None

    online = players.get("online")
    if isinstance(online, bool) or not isinstance(online, int) or online < 0:
        return None

    maximum = players.get("max")
    if maximum is None:
        return online, 0
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        return None
    return online, maximum


class BaseMcClient:
    """共享 aiohttp session 管理的基类。

    子类只需实现 _fetch_raw_data() 和 _parse_raw_data(data) 方法。
    """

    def __init__(
        self,
        server_ip: str,
        server_port: int | None,
        server_type: str = "bedrock",
        server_name: str = "Minecraft服务器",
    ):
        self.server_ip = server_ip
        self.server_port = server_port
        self.server_type = server_type
        self.server_name = server_name
        self._session: aiohttp.ClientSession | None = None
        self._headers = {"User-Agent": "MinecraftServerMonitor/1.0 (AstrBot Plugin)"}
        self._timeout = aiohttp.ClientTimeout(total=10)

    async def __aenter__(self) -> BaseMcClient:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    def _get_session(self) -> aiohttp.ClientSession:
        """懒创建可复用的 aiohttp session。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._headers,
                timeout=self._timeout,
            )
        return self._session

    async def close(self) -> None:
        """关闭内部持有的 aiohttp session。"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def get_server_info(self) -> dict | None:
        """获取并解析服务器信息，返回结构化字典，失败时返回 None。"""
        try:
            data = await self._fetch_raw_data()
            if not isinstance(data, dict):
                return None
            parsed = self._parse_raw_data(data)
        except Exception:
            # External transport and normalization share one None boundary.
            return None
        return parsed if isinstance(parsed, dict) else None

    async def _fetch_raw_data(self) -> dict | None:
        """子类需实现：调用 API 返回原始 JSON 数据。"""
        raise NotImplementedError

    def _parse_raw_data(self, data: dict) -> dict | None:
        """子类需实现：将原始 JSON 解析为结构化字典。"""
        raise NotImplementedError
