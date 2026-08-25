"""Web API 路由处理器。

与 main.py 解耦，集中处理来自 WebUI 的 HTTP 请求。
业务状态由 MonitorService、ConfigManager、RunLogBuffer 维护。
调用方式：from .web.api import WebApiHandler
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from astrbot.api.web import error_response, json_response, request

from ..utils.template import (
    DEFAULT_OFFLINE_TEMPLATE,
    DEFAULT_ONLINE_TEMPLATE,
    FIELD_REGISTRY,
    LEGACY_CUSTOM_FIELD_KEYS,
    SAMPLE_HITOKOTO,
    SAMPLE_OFFLINE_SERVER_DATA,
    SAMPLE_SERVER_DATA,
    SAMPLE_SERVER_TYPE,
    build_template_from_fields,
    render_template,
    validate_template,
)
from ..monitor.run_log import DEFAULT_RUN_LOG_MAX, RunLogBuffer
from ..monitor.service import MonitorService
from ..utils.group_config import (
    DEFAULT_GROUP_CONFIG,
    ConfigManager,
    is_srv_port_optional,
)

RunLogFn = Callable[[str, str, str | None], None]

ALLOWED_SERVER_TYPES = {"java", "bedrock"}
ALLOWED_API_SOURCES = {"", "mcstatus", "mcmotdapi", "local"}
ALLOWED_MESSAGE_FORMATS = {"full", "simple", "custom"}
# 保留旧调用方导入名，但字段集合仍由模板注册表派生。
ALLOWED_CUSTOM_FIELDS = set(LEGACY_CUSTOM_FIELD_KEYS)


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
        if not group_id or not group_id.isascii() or not group_id.isdigit() or int(group_id) <= 0:
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

    @classmethod
    def _parse_optional_port(
        cls,
        value: object,
        *,
        allow_empty: bool,
    ) -> int | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            if allow_empty:
                return None
            raise ValueError("server_port 必须是 1 到 65535 的整数")
        return cls._parse_port(value)

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

    @staticmethod
    def _parse_raw_string(body: dict, key: str, default: str = "") -> str:
        """解析模板文本时保留首尾空白和换行。"""

        value = body.get(key, default)
        if not isinstance(value, str):
            raise ValueError(f"{key} 必须是字符串")
        return value

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
            raise ValueError("api_source 必须为空、mcstatus、mcmotdapi 或 local")

        enable_srv = cls._parse_bool(
            body,
            "enable_srv",
            default=True,
            allow_none=False,
        )
        assert enable_srv is not None
        port_value = body.get("server_port")
        if is_srv_port_optional(server_type, enable_srv):
            if port_value is not None and not (
                isinstance(port_value, str) and not port_value.strip()
            ):
                raise ValueError("server_port 必须留空")
            server_port = None
        else:
            if port_value is None:
                port_value = DEFAULT_GROUP_CONFIG["server_port"]
            server_port = cls._parse_optional_port(
                port_value,
                allow_empty=False,
            )

        raw_format = body.get("message_format", None)
        if raw_format is not None and (
            not isinstance(raw_format, str) or raw_format not in ALLOWED_MESSAGE_FORMATS
        ):
            raise ValueError("message_format 必须是 full、simple 或 custom")

        # 兼容旧版 WebUI/调用方的三字段请求；转换后仍只写入新模板字段。
        if "message_format" not in body and any(
            key in body for key in ("custom_mode", "custom_fields", "simple_mode")
        ):
            raw_custom = body.get("custom_mode")
            raw_fields = body.get("custom_fields")
            if raw_custom is not None and type(raw_custom) is not bool:
                raise ValueError("custom_mode 必须是布尔值")
            if raw_fields is not None and (
                not isinstance(raw_fields, list)
                or any(
                    not isinstance(field, str) or field not in LEGACY_CUSTOM_FIELD_KEYS
                    for field in raw_fields
                )
            ):
                raise ValueError("custom_fields 包含不支持的字段")
            fields = list(dict.fromkeys(raw_fields or []))
            if raw_custom is True and fields:
                raw_format = "custom"
                message_template = build_template_from_fields(fields)
            else:
                raw_simple = body.get("simple_mode")
                if raw_simple is not None and type(raw_simple) is not bool:
                    raise ValueError("simple_mode 必须是布尔值")
                raw_format = (
                    "simple" if raw_simple is True else ("full" if raw_simple is False else None)
                )
                message_template = ""
            offline_template = ""
        else:
            message_template = cls._parse_raw_string(body, "message_template")
            offline_template = cls._parse_raw_string(body, "offline_template")

        if raw_format == "custom":
            for template in (message_template, offline_template):
                unknown = validate_template(template)
                if unknown:
                    raise ValueError(f"模板包含未知占位符: {', '.join(unknown)}")
        elif "message_template" in body or "offline_template" in body:
            for template in (message_template, offline_template):
                unknown = validate_template(template)
                if unknown:
                    raise ValueError(f"模板包含未知占位符: {', '.join(unknown)}")

        return {
            "group_id": cls._parse_group_id(body.get("group_id")),
            "name": cls._parse_string(
                body,
                "name",
                DEFAULT_GROUP_CONFIG["name"],
            )
            or DEFAULT_GROUP_CONFIG["name"],
            "server_ip": server_ip,
            "server_port": server_port,
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
            "enable_srv": enable_srv,
            "message_format": raw_format,
            "message_template": message_template,
            "offline_template": offline_template,
        }

    def register(self, context) -> None:
        """向 AstrBot 注册所有 Web API 路由。"""
        prefix = f"/{self.plugin_name}"
        apis = [
            (f"{prefix}/configs", self.api_get_configs, ["GET"], "获取所有群的独立配置"),
            (f"{prefix}/fields", self.api_get_fields, ["GET"], "获取模板字段元数据"),
            (f"{prefix}/configs/save", self.api_save_config, ["POST"], "新增或更新一个群的配置"),
            (f"{prefix}/configs/preview", self.api_preview_config, ["POST"], "预览监控消息模板"),
            (f"{prefix}/configs/delete", self.api_delete_config, ["POST"], "删除指定群的配置"),
            (f"{prefix}/stats", self.api_get_stats, ["GET"], "获取运行时状态"),
            (f"{prefix}/logs", self.api_get_logs, ["GET"], "获取监控运行日志（增量）"),
            (f"{prefix}/logs/clear", self.api_clear_logs, ["POST"], "清空监控运行日志"),
        ]
        for path, handler, methods, desc in apis:
            context.register_web_api(path, handler, methods, desc)

    async def api_get_fields(self):
        """返回前端动态渲染变量按钮所需的字段元数据。"""

        return json_response(
            {
                "fields": [
                    {
                        "key": field.key,
                        "label": field.label,
                        "description": field.description,
                        "preview": field.preview,
                    }
                    for field in FIELD_REGISTRY
                ],
                "default_online_template": DEFAULT_ONLINE_TEMPLATE,
                "default_offline_template": DEFAULT_OFFLINE_TEMPLATE,
            }
        )

    async def api_preview_config(self):
        """使用固定样例调用真实模板渲染器，返回在线和离线预览。"""

        try:
            body = await request.json(default={})
            if not isinstance(body, dict):
                raise ValueError("请求体必须是 JSON 对象")
            server_type = self._parse_string(body, "server_type", SAMPLE_SERVER_TYPE).lower()
            if server_type not in ALLOWED_SERVER_TYPES:
                raise ValueError("server_type 必须是 java 或 bedrock")
            online_template = (
                self._parse_raw_string(body, "message_template") or DEFAULT_ONLINE_TEMPLATE
            )
            offline_template = (
                self._parse_raw_string(body, "offline_template") or DEFAULT_OFFLINE_TEMPLATE
            )
            for template in (online_template, offline_template):
                unknown = validate_template(template)
                if unknown:
                    raise ValueError(f"模板包含未知占位符: {', '.join(unknown)}")
            return json_response(
                {
                    "online": render_template(
                        online_template, SAMPLE_SERVER_DATA, server_type, SAMPLE_HITOKOTO
                    ),
                    "offline": render_template(
                        offline_template,
                        SAMPLE_OFFLINE_SERVER_DATA,
                        server_type,
                        SAMPLE_HITOKOTO,
                    ),
                }
            )
        except ValueError as exc:
            return error_response(str(exc), status_code=400)

    async def api_get_configs(self):
        """获取所有群的独立配置。"""
        try:
            return json_response(self.config_mgr.get_all())
        except Exception as e:
            self._run_log("ERROR", f"WebUI: 获取配置失败: {e}", None)
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
            self._run_log("ERROR", f"WebUI: 保存配置失败: {e}", None)
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
            self._run_log("ERROR", f"WebUI: 删除配置失败: {e}", None)
            return error_response("删除配置失败", status_code=500)

    async def api_get_stats(self):
        """获取运行时状态。"""
        return json_response(
            {
                "monitor_running": self.monitor.is_running(),
                "configured_groups": len(self.config_mgr.get_all()),
                "enabled_groups": len(self.config_mgr.get_enabled_groups()),
                "check_interval": self.monitor.check_interval,
                "last_check_at": self.monitor.last_check_at,
                "last_round_summary": self.monitor.last_round_summary,
            }
        )

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
        return json_response(
            {
                "ok": True,
                "logs": logs,
                "next_id": next_id,
                "monitor_running": self.monitor.is_running(),
                "check_interval": self.monitor.check_interval,
                "enabled_groups": len(self.config_mgr.get_enabled_groups()),
                "last_check_at": self.monitor.last_check_at,
                "last_round_summary": self.monitor.last_round_summary,
            }
        )

    async def api_clear_logs(self):
        """清空监控运行日志。"""
        try:
            self.run_logs.clear()
            self._run_log("INFO", "WebUI: 运行日志已清空", None)
            return json_response({"ok": True})
        except Exception as e:
            self._run_log("ERROR", f"WebUI: 清空日志失败: {e}", None)
            return error_response("清空日志失败", status_code=500)
