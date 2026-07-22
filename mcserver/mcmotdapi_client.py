"""Minecraft 服务器 API 客户端，封装 mcmotdapi 的请求与数据解析。

与 McStatusClient 保持相同对外接口（get_server_info / close / async with），
响应归一化为相同结构化字典，下游 formatter / tracker 无感知。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

import aiohttp


_MCMOTDAPI_SERVER_TYPE_MAP = {
    "java": "je",
    "bedrock": "be",
}


class McMotdApiClient:
    """Minecraft 服务器查询客户端 — mcmotdapi 源。"""

    def __init__(
        self,
        server_ip: str,
        server_port: int,
        server_type: str = "bedrock",
        server_name: str = "Minecraft服务器",
        api_host: str = "motd.minebbs.com",
        use_ssl: bool = True,
    ):
        """初始化客户端。

        Args:
            server_ip: 服务器 IP 或域名。
            server_port: 服务器端口。
            server_type: 服务器类型，"java" 或 "bedrock"。
            server_name: 显示用服务器名称。
            api_host: mcmotdapi 服务地址（含端口）。
            use_ssl: 是否使用 HTTPS。
        """
        self.server_ip = server_ip
        self.server_port = server_port
        self.server_type = server_type
        self.server_name = server_name
        self.api_host = api_host
        self.use_ssl = use_ssl
        self._session: aiohttp.ClientSession | None = None
        self._headers = {"User-Agent": "MinecraftServerMonitor/1.0 (AstrBot Plugin)"}
        self._timeout = aiohttp.ClientTimeout(total=10)

    async def __aenter__(self) -> McMotdApiClient:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._headers,
                timeout=self._timeout,
            )
        return self._session

    async def close(self) -> None:
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
        """调用 mcmotdapi，返回原始 JSON 数据。"""
        if not self.server_ip or not self.server_port:
            return None

        scheme = "https" if self.use_ssl else "http"
        stype = _MCMOTDAPI_SERVER_TYPE_MAP.get(self.server_type, "be")
        api_url = (
            f"{scheme}://{self.api_host}/api/status"
            f"?ip={self.server_ip}&port={self.server_port}&stype={stype}"
        )

        try:
            session = self._get_session()
            async with session.get(api_url) as response:
                if response.status != 200:
                    return None
                try:
                    return await response.json()
                except (ValueError, TypeError):
                    return None
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    def _parse_raw_data(self, data: dict) -> dict:
        """将 mcmotdapi 原始 JSON 解析为结构化字典，与 McStatusClient 输出格式一致。"""
        status = data.get("status", "offline")
        server_status = "online" if status == "online" else "offline"

        host = data.get("host", f"{self.server_ip}:{self.server_port}")

        # MOTD — 使用纯文本版本
        motd = data.get("pureMotd", "")

        # 版本
        version = data.get("version", "未知版本")
        protocol_raw = data.get("protocol")
        try:
            protocol = int(protocol_raw) if protocol_raw is not None else "未知"
        except (ValueError, TypeError):
            protocol = "未知"

        # 玩家
        players_info = data.get("players", {})
        if isinstance(players_info, dict):
            try:
                online_players = int(players_info.get("online", 0))
            except (ValueError, TypeError):
                online_players = 0
            try:
                max_players = int(players_info.get("max", 0))
            except (ValueError, TypeError):
                max_players = 0

            # sample 是逗号分隔的字符串，如 "EchterPhysiker, PassTheMayo" 或 "无"
            sample = players_info.get("sample", "")
            if isinstance(sample, str) and sample.strip() and sample != "无":
                player_list = [p.strip() for p in sample.split(",") if p.strip()]
            else:
                player_list = []
        else:
            online_players = 0
            max_players = 0
            player_list = []

        # 地图 — 基岩版有 levelname，Java 版无
        server_map = data.get("levelname", "未知")

        # 软件 — mcmotdapi 不返回此字段
        software = "未知"

        # 图标 — Java 版可能有
        icon = data.get("icon", "")

        return {
            "status": server_status,
            "name": host,
            "version": version,
            "protocol": protocol,
            "online": online_players,
            "max": max_players,
            "players": player_list,
            "motd": motd,
            "id": "未知",
            "port": self.server_port,
            "icon": icon,
            "software": software,
            "map": server_map,
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
