"""Minecraft 服务器 API 客户端，封装 mcstatus.io API 的请求与数据解析。

不依赖 AstrBot API，可独立测试。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Optional

import aiohttp


class McStatusClient:
    """Minecraft 服务器查询客户端 — mcstatus.io 源。"""

    def __init__(
        self,
        server_ip: str,
        server_port: int,
        server_type: str = "bedrock",
        server_name: str = "Minecraft服务器",
    ):
        """初始化客户端。

        Args:
            server_ip: 服务器 IP 或域名。
            server_port: 服务器端口。
            server_type: 服务器类型，"java" 或 "bedrock"。
            server_name: 显示用服务器名称。
        """
        self.server_ip = server_ip
        self.server_port = server_port
        self.server_type = server_type
        self.server_name = server_name
        self._session: aiohttp.ClientSession | None = None
        self._headers = {"User-Agent": "MinecraftServerMonitor/1.0 (AstrBot Plugin)"}
        self._timeout = aiohttp.ClientTimeout(total=10)

    async def __aenter__(self) -> McStatusClient:
        """支持 async with 语法，自动管理 session 生命周期。"""
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

    async def get_server_info(self) -> Optional[dict]:
        """获取并解析服务器信息，返回结构化字典，失败时返回 None。"""
        data = await self._fetch_raw_data()
        if data is None:
            return None
        return self._parse_raw_data(data)

    async def _fetch_raw_data(self) -> Optional[dict]:
        """调用 mcstatus.io API，返回原始 JSON 数据。"""
        if not self.server_ip or not self.server_port:
            return None

        api_url = (
            f"https://api.mcstatus.io/v2/status/{self.server_type}/"
            f"{self.server_ip}:{self.server_port}"
        )

        try:
            session = self._get_session()
            async with session.get(api_url) as response:
                if response.status != 200:
                    return None
                try:
                    return await response.json()
                except json.JSONDecodeError:
                    return None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    def _parse_raw_data(self, data: dict) -> dict:
        """将 mcstatus.io API 原始 JSON 解析为结构化字典。"""
        online = data.get("online", False)
        server_status = "online" if online else "offline"

        hostname = data.get("hostname", "")
        server_name = hostname if hostname else f"{self.server_ip}:{self.server_port}"

        motd_info = data.get("motd", {})
        motd_clean = ""
        if isinstance(motd_info, dict):
            motd_clean = motd_info.get("clean", "") or motd_info.get("raw", "")
        elif isinstance(motd_info, str):
            motd_clean = motd_info

        version_info = data.get("version", {})
        if isinstance(version_info, dict):
            version = version_info.get("name", "未知版本")
            protocol = version_info.get("protocol", "未知")
        else:
            version = str(version_info) if version_info else "未知版本"
            protocol = "未知"

        players_info = data.get("players", {})
        if isinstance(players_info, dict):
            online_players = players_info.get("online", 0)
            max_players = players_info.get("max", 0)
            player_list = players_info.get("list", [])
        else:
            online_players = 0
            max_players = 0
            player_list = []

        map_info = data.get("map", {})
        server_map = map_info.get("name", "未知") if isinstance(map_info, dict) else "未知"

        return {
            "status": server_status,
            "name": server_name,
            "version": version,
            "protocol": protocol,
            "online": online_players,
            "max": max_players,
            "players": player_list,
            "motd": motd_clean,
            "id": data.get("id", "未知"),
            "port": data.get("port", self.server_port),
            "icon": data.get("icon", ""),
            "software": data.get("software", "未知"),
            "map": server_map,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
