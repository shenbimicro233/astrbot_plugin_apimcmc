"""Minecraft 服务器 API 客户端基类，提供共享的 aiohttp session 管理。"""

from __future__ import annotations

import aiohttp


class BaseMcClient:
    """共享 aiohttp session 管理的基类。

    子类只需实现 _fetch_raw_data() 和 _parse_raw_data(data) 方法。
    """

    def __init__(
        self,
        server_ip: str,
        server_port: int,
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
        data = await self._fetch_raw_data()
        if data is None:
            return None
        return self._parse_raw_data(data)

    async def _fetch_raw_data(self) -> dict | None:
        """子类需实现：调用 API 返回原始 JSON 数据。"""
        raise NotImplementedError

    def _parse_raw_data(self, data: dict) -> dict:
        """子类需实现：将原始 JSON 解析为结构化字典。"""
        raise NotImplementedError
