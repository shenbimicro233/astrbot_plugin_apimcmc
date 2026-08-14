"""Minecraft 服务器本地直连客户端，基于 mcstatus 库。

与 HTTP API 客户端保持相同的对外接口（get_server_info / close / async with），
响应归一化为相同结构化字典，不包含玩家名单。
"""

from __future__ import annotations

import base64
from datetime import datetime
from ipaddress import ip_address

import dns.exception
from mcstatus import BedrockServer, JavaServer
from mcstatus._net.dns import async_resolve_mc_srv

from .base_client import BaseMcClient, format_server_address, normalize_server_host

_DEFAULT_JAVA_PORT = 25565
_TIMEOUT = 10
_TRIES = 1
_UNKNOWN = "未知"
_UNKNOWN_VERSION = "未知版本"


def _icon_to_string(raw_icon: object) -> str:
    """Normalize an optional Java icon to the string shape used elsewhere."""

    if isinstance(raw_icon, bytes) and raw_icon:
        return "data:image/png;base64," + base64.b64encode(raw_icon).decode("ascii")
    return raw_icon if isinstance(raw_icon, str) else ""


class LocalClient(BaseMcClient):
    """Minecraft 服务器查询客户端 —— 本地直连源。"""

    def __init__(
        self,
        server_ip: str,
        server_port: int | None,
        server_type: str = "bedrock",
        server_name: str = "Minecraft服务器",
        enable_srv: bool = True,
    ):
        super().__init__(server_ip, server_port, server_type, server_name)
        self.enable_srv = enable_srv

    async def _resolve_java_target(self, host: str) -> tuple[str, int]:
        """Return the effective Java connection target under the SRV policy."""

        if self.enable_srv:
            try:
                ip_address(host)
            except ValueError:
                # Domains resolve _minecraft._tcp first even with a legacy
                # configured port; DNS failure falls back below.
                try:
                    return await async_resolve_mc_srv(host, lifetime=_TIMEOUT)
                except dns.exception.DNSException:
                    pass
        return host, self.server_port or _DEFAULT_JAVA_PORT

    async def _fetch_raw_data(self) -> dict | None:
        """Query the server locally and return the normalized dictionary."""

        host = normalize_server_host(self.server_ip)
        if not host:
            return None
        if self.server_type == "java":
            target_host, target_port = await self._resolve_java_target(host)
            server = JavaServer(target_host, target_port, timeout=_TIMEOUT)
            return self._parse_raw_data(await server.async_status(tries=_TRIES))
        if self.server_port is None:
            return None
        server = BedrockServer(host, self.server_port, timeout=_TIMEOUT)
        return self._parse_raw_data(await server.async_status(tries=_TRIES))

    def _java_status_payload(self, status: object) -> dict:
        players = getattr(status, "players", None)
        version = getattr(status, "version", None)
        motd = getattr(status, "motd", None)
        icon = getattr(status, "icon", None)
        if players is None or version is None:
            raise ValueError("invalid Java status response")
        version_name = getattr(version, "name", None)
        version_protocol = getattr(version, "protocol", None)
        motd_plain = getattr(motd, "to_plain", None)
        return {
            "status": "online",
            "name": format_server_address(self.server_ip, self.server_port),
            "version": version_name or _UNKNOWN_VERSION,
            "protocol": version_protocol if version_protocol is not None else _UNKNOWN,
            "online": players.online,
            "max": players.max,
            "motd": motd_plain() if callable(motd_plain) else "",
            "id": _UNKNOWN,
            "port": self.server_port,
            "icon": _icon_to_string(icon),
            "software": _UNKNOWN,
            "map": _UNKNOWN,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _bedrock_status_payload(self, status: object) -> dict:
        players = getattr(status, "players", None)
        version = getattr(status, "version", None)
        motd = getattr(status, "motd", None)
        if players is None or version is None:
            raise ValueError("invalid Bedrock status response")
        version_name = getattr(version, "name", None)
        version_protocol = getattr(version, "protocol", None)
        motd_plain = getattr(motd, "to_plain", None)
        return {
            "status": "online",
            "name": format_server_address(self.server_ip, self.server_port),
            "version": version_name or _UNKNOWN_VERSION,
            "protocol": version_protocol if version_protocol is not None else _UNKNOWN,
            "online": players.online,
            "max": players.max,
            "motd": motd_plain() if callable(motd_plain) else "",
            "id": _UNKNOWN,
            "port": self.server_port,
            "icon": "",
            "software": getattr(version, "brand", "") or _UNKNOWN,
            "map": getattr(status, "map_name", "") or _UNKNOWN,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _parse_raw_data(self, data: object) -> dict | None:
        """Convert a raw mcstatus response into the normalized dictionary."""

        if self.server_type == "java":
            return self._java_status_payload(data)
        if self.server_type == "bedrock":
            return self._bedrock_status_payload(data)
        return None

    def _offline_data(self) -> dict:
        """Map transport, DNS, and normalization failures to offline state."""

        return {
            "status": "offline",
            "name": format_server_address(self.server_ip, self.server_port),
            "version": _UNKNOWN_VERSION,
            "protocol": _UNKNOWN,
            "online": 0,
            "max": 0,
            "motd": "",
            "id": _UNKNOWN,
            "port": self.server_port,
            "icon": "",
            "software": _UNKNOWN,
            "map": _UNKNOWN,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    async def get_server_info(self) -> dict | None:
        """Query the server, mapping failures to an offline observation."""

        try:
            return await self._fetch_raw_data()
        except (OSError, TimeoutError, dns.exception.DNSException, ValueError):
            return self._offline_data()
