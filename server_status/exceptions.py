"""一次性服务器查询的稳定命令级异常定义。

异常类型独立成模块，避免业务代码从 server_status.service 导入异常
造成与查询服务实现耦合。
"""

from __future__ import annotations


class ServerQueryError(Exception):
    """稳定命令级服务器查询结果的基类。"""


class QueryUsageError(ServerQueryError):
    """命令参数无法组成受支持的请求。"""


class QueryConfigError(ServerQueryError):
    """必需的已存储群目标缺失或无效。"""


class QueryCooldownError(ServerQueryError):
    """发送者在配置的冷却期内已有接受的查询。"""


class QueryBusyError(ServerQueryError):
    """新的上游查询将超过全局并发上限。"""


class QueryClosedError(ServerQueryError):
    """插件正在终止，不再接受新请求。"""


__all__ = [
    "QueryBusyError",
    "QueryClosedError",
    "QueryConfigError",
    "QueryCooldownError",
    "QueryUsageError",
    "ServerQueryError",
]
