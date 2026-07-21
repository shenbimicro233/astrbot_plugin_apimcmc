"""Web API 路由处理器。

与 main.py 解耦，集中处理来自 WebUI 的 HTTP 请求。
业务状态由 MonitorService、ConfigManager、RunLogBuffer 维护。
调用方式：from .web.api import WebApiHandler
"""

from __future__ import annotations

from typing import Callable

from astrbot.api.web import json_response, request

from ..monitor.run_log import RunLogBuffer
from ..monitor.service import MonitorService
from ..utils.group_config import DEFAULT_GROUP_CONFIG, ConfigManager

RunLogFn = Callable[[str, str, str | None], None]


class WebApiHandler:
    """封装 Minecraft 监控插件的 Web API 处理器。"""

    def __init__(
        self,
        plugin_name: str,
        check_interval: int,
        config_mgr: ConfigManager,
        monitor: MonitorService,
        run_logs: RunLogBuffer,
        run_log: RunLogFn,
    ):
        """初始化 Web API 处理器。

        Args:
            plugin_name: 插件英文标识，用于生成路由前缀。
            check_interval: 监控检查间隔（秒）。
            config_mgr: 群配置管理器。
            monitor: 监控服务实例。
            run_logs: WebUI 运行日志缓冲区。
            run_log: 同时写缓冲区和 AstrBot logger 的日志函数。
        """
        self.plugin_name = plugin_name
        self.check_interval = check_interval
        self.config_mgr = config_mgr
        self.monitor = monitor
        self.run_logs = run_logs
        self._run_log = run_log

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
        return json_response(self.config_mgr.get_all())

    async def api_save_config(self):
        """新增或更新一个群的配置。"""
        try:
            body = await request.json(default={})
            data = {
                "group_id": str(body.get("group_id", "")).strip(),
                "name": str(body.get("name", DEFAULT_GROUP_CONFIG["name"])).strip(),
                "server_ip": str(body.get("server_ip", "")).strip(),
                "server_port": int(
                    body.get("server_port", DEFAULT_GROUP_CONFIG["server_port"])
                ),
                "server_type": str(
                    body.get("server_type", DEFAULT_GROUP_CONFIG["server_type"])
                ).strip(),
                "enabled": bool(body.get("enabled", True)),
                "use_hitokoto": bool(
                    body.get("use_hitokoto", DEFAULT_GROUP_CONFIG["use_hitokoto"])
                ),
            }
            if not data["group_id"] or not data["server_ip"]:
                return json_response({"ok": False, "error": "缺少必填字段"})

            is_new = data["group_id"] not in self.config_mgr.get_all()
            cfg = self.config_mgr.save_group(data)
            self.monitor.rebuild_single_group_state(cfg.group_id)
            self._run_log(
                "INFO",
                f"WebUI: 已{'添加' if is_new else '更新'}群 {cfg.group_id} 的配置",
                cfg.group_id,
            )
            return json_response({"ok": True, "config": cfg.to_dict()})
        except Exception as e:
            return json_response({"ok": False, "error": str(e)})

    async def api_delete_config(self):
        """删除指定群的配置。"""
        body = await request.json(default={})
        group_id = str(body.get("group_id", "")).strip()
        if not group_id:
            return json_response({"ok": False, "error": "缺少 group_id"})

        ok = self.config_mgr.delete_group(group_id)
        self.monitor.rebuild_single_group_state(group_id)
        self.monitor.pop_group_session(group_id)
        if ok:
            self._run_log("INFO", f"WebUI: 已删除群 {group_id} 的配置", group_id)
        return json_response({"ok": ok})

    async def api_get_stats(self):
        """获取运行时状态。"""
        return json_response({
            "monitor_running": self.monitor.is_running(),
            "configured_groups": len(self.config_mgr.get_all()),
            "enabled_groups": len(self.config_mgr.get_enabled_groups()),
            "check_interval": self.check_interval,
            "last_check_at": self.monitor.last_check_at,
            "last_round_summary": self.monitor.last_round_summary,
        })

    async def api_get_logs(self):
        """获取监控运行日志（增量）。"""
        try:
            since_id = int(request.query.get("since_id", 0) or 0)
        except (TypeError, ValueError):
            since_id = 0
        try:
            limit = int(request.query.get("limit", 100) or 100)
        except (TypeError, ValueError):
            limit = 100

        logs, next_id = self.run_logs.list_since(since_id=since_id, limit=limit)
        return json_response({
            "ok": True,
            "logs": logs,
            "next_id": next_id,
            "monitor_running": self.monitor.is_running(),
            "check_interval": self.check_interval,
            "enabled_groups": len(self.config_mgr.get_enabled_groups()),
            "last_check_at": self.monitor.last_check_at,
            "last_round_summary": self.monitor.last_round_summary,
        })

    async def api_clear_logs(self):
        """清空监控运行日志。"""
        self.run_logs.clear()
        self._run_log("INFO", "WebUI: 运行日志已清空")
        return json_response({"ok": True})
