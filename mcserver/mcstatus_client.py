"""Minecraft 服务器 API 客户端，封装 mcstatus.io API 的请求与数据解析。

不依赖 AstrBot API，可独立测试。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from urllib.parse import quote

import aiohttp
from yarl import URL

from .base_client import BaseMcClient, format_server_address, normalize_player_counts


_MCSTATUS_BASE_URL = URL("https://api.mcstatus.io")


def _build_status_url(server_type: str, server_ip: str, server_port: int | None) -> URL:
    """Build a revision-2 mcstatus URL with an encoded address path segment.

    Args:
        server_type: Requested Minecraft edition.
        server_ip: Configured domain, IPv4 address, or IPv6 literal.
        server_port: Configured Minecraft port, omitted for host-only SRV
            paths.

    Returns:
        The request URL without query parameters.
    """
    address = format_server_address(server_ip, server_port)
    encoded_type = quote(server_type, safe="")
    encoded_address = quote(address, safe=":")
    return _MCSTATUS_BASE_URL.with_path(
        f"/v2/status/{encoded_type}/{encoded_address}",
        encoded=True,
    )


class McStatusClient(BaseMcClient):
    """Minecraft 服务器查询客户端 — mcstatus.io 源。"""

    async def _fetch_raw_data(self) -> dict | None:
        """调用 mcstatus.io API，返回原始 JSON 数据。"""
        if not self.server_ip:
            return None

        params = {"timeout": "5"}
        if self.server_type == "java":
            params["query"] = "true"

        try:
            api_url = _build_status_url(
                self.server_type,
                self.server_ip,
                self.server_port,
            )
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
        """将 mcstatus.io API 原始 JSON 解析为结构化字典。"""
        online = data.get("online")
        if not isinstance(online, bool):
            return None

        if online:
            counts = normalize_player_counts(data.get("players"))
            if counts is None:
                return None
            online_players, max_players = counts
        else:
            online_players = 0
            max_players = 0

        server_status = "online" if online else "offline"

        host = data.get("host")
        server_name = (
            host
            if isinstance(host, str) and host
            else format_server_address(self.server_ip, self.server_port)
        )

        motd_info = data.get("motd", {})
        motd_clean = ""
        if isinstance(motd_info, dict):
            raw_motd = motd_info.get("clean") or motd_info.get("raw")
            motd_clean = raw_motd if isinstance(raw_motd, str) else ""
        elif isinstance(motd_info, str):
            motd_clean = motd_info

        version_info = data.get("version", {})
        if isinstance(version_info, dict):
            raw_version = version_info.get("name_clean")
            version = raw_version if isinstance(raw_version, str) and raw_version else "未知版本"
            protocol = version_info.get("protocol", "未知")
        else:
            version = str(version_info) if version_info else "未知版本"
            protocol = "未知"

        map_info = data.get("map", {})
        raw_map = map_info.get("name") if isinstance(map_info, dict) else None
        server_map = raw_map if isinstance(raw_map, str) and raw_map else "未知"

        return {
            "status": server_status,
            "name": server_name,
            "version": version,
            "protocol": protocol,
            "online": online_players,
            "max": max_players,
            "motd": motd_clean,
            "id": data.get("id", "未知"),
            "port": data.get("port", self.server_port),
            "icon": data.get("icon", ""),
            "software": data.get("software", "未知"),
            "map": server_map,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
