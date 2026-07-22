"""Minecraft 服务器查询客户端工厂。

提供 create_mc_client() 工厂函数，根据 api_source 配置
返回对应的客户端实例，统一对外接口。

支持的源：
  - "mcstatus"   (默认) : McStatusClient    → mcstatus.io API
  - "mcmotdapi"          : McMOTDAPIClient  → mcmotdapi
"""

from __future__ import annotations

from .mcstatus_client import McStatusClient
from .mcmotdapi_client import McMotdApiClient


def create_mc_client(
    api_source: str,
    server_ip: str,
    server_port: int,
    server_type: str = "bedrock",
    server_name: str = "Minecraft服务器",
    mcmotdapi_host: str = "motd.minebbs.com",
    mcmotdapi_ssl: bool = True,
) -> McStatusClient | McMotdApiClient:
    """根据 api_source 创建对应的 Minecraft 服务器查询客户端。

    Args:
        api_source: API 源名称 ("mcstatus" 或 "mcmotdapi")。
        server_ip: 服务器 IP 或域名。
        server_port: 服务器端口。
        server_type: 服务器类型，"java" 或 "bedrock"。
        server_name: 显示用服务器名称。
        mcmotdapi_host: mcmotdapi 服务地址（含端口）。
        mcmotdapi_ssl: mcmotdapi 是否使用 HTTPS。

    Returns:
        客户端实例，实现了 get_server_info() / close() / async with 协议。
    """
    if api_source == "mcmotdapi":
        return McMotdApiClient(
            server_ip=server_ip,
            server_port=server_port,
            server_type=server_type,
            server_name=server_name,
            api_host=mcmotdapi_host,
            use_ssl=mcmotdapi_ssl,
        )
    # 默认使用 mcstatus.io
    return McStatusClient(
        server_ip=server_ip,
        server_port=server_port,
        server_type=server_type,
        server_name=server_name,
    )
