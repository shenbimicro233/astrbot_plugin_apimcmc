"""Web API 包导出。

统一对外入口。调用方式：
  from .web import WebApiHandler
"""

from __future__ import annotations

from .api import WebApiConfig, WebApiHandler

__all__ = ["WebApiConfig", "WebApiHandler"]
