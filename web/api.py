"""Web API 路由处理器。

与 main.py 解耦，集中处理来自 WebUI 的 HTTP 请求。
业务状态由 MonitorService、ConfigManager、RunLogBuffer 维护。
调用方式：from .web.api import WebApiHandler
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from astrbot.api.web import error_response, json_response, request

from ..mcserver.formatter import CUSTOM_FIELD_ORDER
from ..monitor.run_log import DEFAULT_RUN_LOG_MAX, RunLogBuffer
from ..monitor.service import MonitorService
from ..utils.group_config import DEFAULT_GROUP_CONFIG, ConfigManager

RunLogFn = Callable[[str, str, str | None], None]

ALLOWED_SERVER_TYPES = {"java", "bedrock"}
ALLOWED_API_SOURCES = {"", "mcstatus", "mcmotdapi"}
ALLOWED_CUSTOM_FIELDS = set(CUSTOM_FIELD_ORDER) | {"hitokoto"}


@dataclass
class WebApiConfig:
    """Web API 处理器配置。"""

    plugin_name: str
    check_interval: int = 10


class WebApiHandler:
    """封装 Minecraft 监控插件的 Web API 处理器。"""

    def __init__(
        self,
        config: WebApiConfig,
        config_mgr: ConfigManager,
        monitor: MonitorService,
        run_logs: RunLogBuffer,
        run_log: RunLogFn,
    ):
        """初始化 Web API 处理器。

        Args:
            config: Web API 配置（插件标识、检查间隔）。
            config_mgr: 群配置管理器。
            monitor: 监控服务实例。
            run_logs: WebUI 运行日志缓冲区。
            run_log: 同时写缓冲区和 AstrBot logger 的日志函数。
        """
        self.plugin_name = config.plugin_name
        self.check_interval = config.check_interval
        self.config_mgr = config_mgr
        self.monitor = monitor
        self.run_logs = run_logs
        self._run_log = run_log

    @staticmethod
    def _parse_group_id(value: object) -> str:
        if isinstance(value, bool):
            raise ValueError("group_id 必须是正整数群号")
        if isinstance(value, int):
            group_id = str(value)
        elif isinstance(value, str):
            group_id = value.strip()
        else:
            raise ValueError("group_id 必须是正整数群号")
        if (
            not group_id
            or not group_id.isascii()
            or not group_id.isdigit()
            or int(group_id) <= 0
        ):
            raise ValueError("group_id 必须是正整数群号")
        return group_id

    @staticmethod
    def _parse_port(value: object) -> int:
        if isinstance(value, bool):
            raise ValueError("server_port 必须是 1 到 65535 的整数")
        if isinstance(value, int):
            port = value
        elif isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
            port = int(value.strip())
        else:
            raise ValueError("server_port 必须是 1 到 65535 的整数")
        if not 1 <= port <= 65535:
            raise ValueError("server_port 必须是 1 到 65535 的整数")
        return port

    @staticmethod
    def _parse_bool(
        body: dict,
        key: str,
        *,
        default: bool | None,
        allow_none: bool = True,
    ) -> bool | None:
        if key not in body:
            return default
        value = body[key]
        if value is None and allow_none:
            return None
        if type(value) is not bool:
            raise ValueError(f"{key} 必须是布尔值")
        return value

    @staticmethod
    def _parse_string(body: dict, key: str, default: str = "") -> str:
        value = body.get(key, default)
        if not isinstance(value, str):
            raise ValueError(f"{key} 必须是字符串")
        return value.strip()

    @classmethod
    def validate_config_payload(cls, body: object) -> dict:
        """校验并规范化 WebUI 的群配置请求。"""
        if not isinstance(body, dict):
            raise ValueError("请求体必须是 JSON 对象")

        server_ip = cls._parse_string(body, "server_ip")
        if not server_ip:
            raise ValueError("server_ip 不能为空")

        server_type = cls._parse_string(
            body,
            "server_type",
            DEFAULT_GROUP_CONFIG["server_type"],
        ).lower()
        if server_type not in ALLOWED_SERVER_TYPES:
            raise ValueError("server_type 必须是 java 或 bedrock")

        api_source = cls._parse_string(body, "api_source").lower()
        if api_source not in ALLOWED_API_SOURCES:
            raise ValueError("api_source 必须为空、mcstatus 或 mcmotdapi")

        custom_fields = body.get("custom_fields")
        if custom_fields is not None:
            if not isinstance(custom_fields, list) or any(
                not isinstance(field, str) or field not in ALLOWED_CUSTOM_FIELDS
                for field in custom_fields
            ):
                raise ValueError("custom_fields 包含不支持的字段")
            custom_fields = list(dict.fromkeys(custom_fields))

        return {
            "group_id": cls._parse_group_id(body.get("group_id")),
            "name": cls._parse_string(
                body,
                "name",
                DEFAULT_GROUP_CONFIG["name"],
            ) or DEFAULT_GROUP_CONFIG["name"],
            "server_ip": server_ip,
            "server_port": cls._parse_port(
                body.get("server_port", DEFAULT_GROUP_CONFIG["server_port"])
            ),
            "server_type": server_type,
            "enabled": cls._parse_bool(
                body,
                "enabled",
                default=True,
                allow_none=False,
            ),
            "api_source": api_source,
            "mcmotdapi_host": cls._parse_string(body, "mcmotdapi_host"),
            "mcmotdapi_ssl": cls._parse_bool(
                body,
                "mcmotdapi_ssl",
                default=None,
            ),
            "simple_mode": cls._parse_bool(body, "simple_mode", default=None),
            "custom_mode": cls._parse_bool(body, "custom_mode", default=None),
            "custom_fields": custom_fields,
        }

    def register(self, context) -> None:
        """向 AstrBot 注册所有 Web API 路由。"""
        prefix = f"/{self.plugin_name}"
        apis = [
            (f"{prefix}/configs", self.api_get_configs, ["GET"], "获取所有群的独立配置"),
            (f"{prefix}/configs/save", self.api_save_config, ["POST"], "新增或更新一个群的配置"),
            (f"{prefix}/configs/delete", self.api_delete_config, ["POST"], "删除指定群的配置"),
            (f"{prefix}/stats", self.api_get_stats, ["GET"], "获取运行时状态"),
            (f"{prefix}/logs", self.api_get_logs, ["GET"], "获取监控运行日志（增量）"),
            (f"{prefix}/logs/clear", self.api_clear_logs, ["POST"], "清空监控运行日志"),
        ]
        for path, handler, methods, desc in apis:
            context.register_web_api(path, handler, methods, desc)

    async def api_get_configs(self):
        """获取所有群的独立配置。"""
        try:
            return json_response(self.config_mgr.get_all())
        except Exception as e:
            self._run_log("ERROR", f"WebUI: 获取配置失败: {e}")
            return error_response("获取配置失败", status_code=500)

    async def api_save_config(self):
        """新增或更新一个群的配置。"""
        try:
            body = await request.json(default={})
            data = self.validate_config_payload(body)
        except ValueError as e:
            return error_response(str(e), status_code=400)

        try:
            is_new = data["group_id"] not in self.config_mgr.get_all()
            cfg = self.config_mgr.save_group(data)
            await self.monitor.rebuild_single_group_state(cfg.group_id)
            self._run_log(
                "INFO",
                f"WebUI: 已{'添加' if is_new else '更新'}群 {cfg.group_id} 的配置",
                cfg.group_id,
            )
            return json_response({"ok": True, "config": cfg.to_dict()})
        except Exception as e:
            self._run_log("ERROR", f"WebUI: 保存配置失败: {e}")
            return error_response("保存配置失败", status_code=500)

    async def api_delete_config(self):
        """删除指定群的配置。"""
        try:
            body = await request.json(default={})
            if not isinstance(body, dict):
                raise ValueError("请求体必须是 JSON 对象")
            group_id = self._parse_group_id(body.get("group_id"))
        except ValueError as e:
            return error_response(str(e), status_code=400)

        try:
            ok = self.config_mgr.delete_group(group_id)
            if not ok:
                return error_response("配置不存在", status_code=404)
            await self.monitor.rebuild_single_group_state(group_id)
            self.monitor.pop_group_session(group_id)
            self._run_log("INFO", f"WebUI: 已删除群 {group_id} 的配置", group_id)
            return json_response({"ok": True})
        except Exception as e:
            self._run_log("ERROR", f"WebUI: 删除配置失败: {e}")
            return error_response("删除配置失败", status_code=500)

    async def api_get_stats(self):
        """获取运行时状态。"""
        return json_response({
            "monitor_running": self.monitor.is_running(),
            "configured_groups": len(self.config_mgr.get_all()),
            "enabled_groups": len(self.config_mgr.get_enabled_groups()),
            "check_interval": self.monitor.check_interval,
            "last_check_at": self.monitor.last_check_at,
            "last_round_summary": self.monitor.last_round_summary,
        })

    async def api_get_logs(self):
        """获取监控运行日志（增量）。"""
        try:
            since_id = int(request.query.get("since_id", 0) or 0)
            limit = int(request.query.get("limit", 100) or 100)
        except (TypeError, ValueError):
            return error_response("since_id 和 limit 必须是整数", status_code=400)
        if since_id < 0 or not 1 <= limit <= DEFAULT_RUN_LOG_MAX:
            return error_response(
                f"since_id 必须非负，limit 必须在 1 到 {DEFAULT_RUN_LOG_MAX} 之间",
                status_code=400,
            )

        logs, next_id = self.run_logs.list_since(since_id=since_id, limit=limit)
        return json_response({
            "ok": True,
            "logs": logs,
            "next_id": next_id,
            "monitor_running": self.monitor.is_running(),
            "check_interval": self.monitor.check_interval,
            "enabled_groups": len(self.config_mgr.get_enabled_groups()),
            "last_check_at": self.monitor.last_check_at,
            "last_round_summary": self.monitor.last_round_summary,
        })

    async def api_clear_logs(self):
        """清空监控运行日志。"""
        try:
            self.run_logs.clear()
            self._run_log("INFO", "WebUI: 运行日志已清空")
            return json_response({"ok": True})
        except Exception as e:
            self._run_log("ERROR", f"WebUI: 清空日志失败: {e}")
            return error_response("清空日志失败", status_code=500)
