"""Minecraft 服务器监控插件 — AstrBot 主入口。

仅负责：插件注册、生命周期、命令、Web API 路由与依赖组装。
业务逻辑分布在：
  - mcserver.*          服务器查询与格式化
  - monitor.tracker     状态变化检测
  - monitor.run_log     WebUI 运行日志缓冲
  - monitor.service     定时监控服务
  - utils.group_config  每群配置持久化
  - utils.notifier      群消息发送
  - utils.hitokoto      一言

版本: v1.0.0
"""

from __future__ import annotations

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .mcserver import create_mc_client
from .mcserver.formatter import format_server_info, format_server_info_simple
from .monitor.run_log import RunLogBuffer
from .monitor.service import MonitorConfig, MonitorService
from .utils import build_group_session
from .utils.group_config import ConfigManager
from .utils.hitokoto import get_hitokoto
from .web import WebApiConfig, WebApiHandler

PLUGIN_NAME = "astrbot_plugin_minecraft_monitor_plus"
PLUGIN_VERSION = "1.0.0"


@register(
    "astrbot_plugin_minecraft_monitor_plus",
    "shenbimicro233",
    "Minecraft服务器监控插件，支持为每个群独立配置服务器监控。通过WebUI管理每群配置，定时检测服务器状态变化并通过机器人发送通知。",
    PLUGIN_VERSION,
)
class MyPlugin(Star):
    """Minecraft 服务器监控插件。"""

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}

        self.check_interval = self.config.get("check_interval", 10)
        self.enable_auto = self.config.get("enable_auto_monitor", False)

        # API 源配置
        self.api_source = self.config.get("api_source", "mcstatus")
        self.mcmotdapi_host = self.config.get("mcmotdapi_host", "motd.minebbs.com")
        self.mcmotdapi_ssl = self.config.get("mcmotdapi_ssl", True)
        self.simple_mode = self.config.get("simple_mode", False)

        self._run_logs = RunLogBuffer()
        self.config_mgr = ConfigManager()
        self.monitor = MonitorService(
            context,
            self.config_mgr,
            config=MonitorConfig(
                check_interval=self.check_interval,
                api_source=self.api_source,
                mcmotdapi_host=self.mcmotdapi_host,
                mcmotdapi_ssl=self.mcmotdapi_ssl,
                simple_mode=self.simple_mode,
            ),
        )
        self.monitor.set_run_log(self._run_log, buffer_only=self._run_log_buffer_only)

        self._web_api = WebApiHandler(
            config=WebApiConfig(
                plugin_name=PLUGIN_NAME,
                check_interval=self.check_interval,
            ),
            config_mgr=self.config_mgr,
            monitor=self.monitor,
            run_logs=self._run_logs,
            run_log=self._run_log,
        )
        self._web_api.register(context)

    # ──────────────────────────────
    # 日志
    # ──────────────────────────────

    def _run_log(self, level: str, msg: str, group_id: str | None = None) -> None:
        """写入 WebUI 缓冲 + AstrBot logger。"""
        self._run_logs.append(level, msg, group_id)
        level_key = str(level).lower()
        if level_key == "changed":
            level_key = "info"
        elif level_key == "warn":
            level_key = "warning"
        log_fn = getattr(logger, level_key, logger.info)
        log_fn(msg)

    def _run_log_buffer_only(
        self, level: str, msg: str, group_id: str | None = None
    ) -> None:
        """仅写入缓冲（notifier 内部已打 logger）。"""
        self._run_logs.append(level, msg, group_id)

    # ──────────────────────────────
    # 生命周期
    # ──────────────────────────────

    async def initialize(self):
        """插件初始化完成后调用，可执行异步启动逻辑。"""
        self._run_log(
            "INFO",
            "Minecraft服务器监控插件已加载 | 使用 /start_server_monitor 启动监控 | 在 WebUI 插件页配置每群监控",
        )

        if self.enable_auto:
            self.monitor.schedule_auto_start()

    async def terminate(self):
        """插件停用/重载前调用，仅做资源清理，不删除持久化数据。"""
        self._run_log("INFO", "正在清理 Minecraft 监控插件...")
        await self.monitor.terminate()
        self._run_logs.clear()
        logger.info("Minecraft 监控插件已完全卸载")

    # ──────────────────────────────
    # 命令
    # ──────────────────────────────

    def _cache_event_session(self, event: AstrMessageEvent) -> None:
        group_id = event.get_group_id()
        umo = getattr(event, "unified_msg_origin", None)
        self.monitor.cache_group_session(group_id, umo)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("start_server_monitor")
    async def start_server_monitor_task(self, event: AstrMessageEvent):
        """启动 Minecraft 服务器监控任务。"""
        self._cache_event_session(event)
        if not self.monitor.start():
            yield event.plain_result("服务器监控任务已经在运行中")
            return
        self._run_log("INFO", "启动服务器监控任务")
        yield event.plain_result(
            f"✅ 服务器监控任务已启动，每{self.check_interval}秒检查一次所有已配置服务器的状态"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("stop_server_monitor")
    async def stop_server_monitor_task(self, event: AstrMessageEvent):
        """停止 Minecraft 服务器监控任务。"""
        self._cache_event_session(event)
        if self.monitor.stop():
            self._run_log("INFO", "停止服务器监控任务")
            yield event.plain_result("✅ 服务器监控任务已停止")
        else:
            yield event.plain_result("❌ 监控任务未在运行")

    @filter.command("查询")
    async def get_server_status(self, event: AstrMessageEvent):
        """查询当前群配置的 Minecraft 服务器状态。"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 该命令仅在群聊中使用")
            return

        self._cache_event_session(event)

        cfg = self.config_mgr.get(group_id)
        if not cfg:
            yield event.plain_result(
                "❌ 当前群未配置监控，请管理员在 WebUI 插件页面「Minecraft服务器监控」中添加配置"
            )
            return

        try:
            effective_source = cfg.api_source if cfg.api_source else self.api_source
            effective_host = cfg.mcmotdapi_host if cfg.mcmotdapi_host else self.mcmotdapi_host
            effective_ssl = self.mcmotdapi_ssl if cfg.mcmotdapi_ssl is None else cfg.mcmotdapi_ssl
            async with create_mc_client(
                api_source=effective_source,
                server_ip=cfg.server_ip,
                server_port=cfg.server_port,
                server_type=cfg.server_type,
                server_name=cfg.name,
                mcmotdapi_host=effective_host,
                mcmotdapi_ssl=effective_ssl,
            ) as client:
                server_data = await client.get_server_info()
            if server_data is None:
                yield event.plain_result("❌ 获取服务器信息失败，请检查 IP/端口/服务器类型")
                return

            # 根据简化模式选择格式
            effective_simple = cfg.simple_mode if cfg.simple_mode is not None else self.simple_mode
            if effective_simple:
                result = format_server_info_simple(server_data, cfg.server_type)
            else:
                result = format_server_info(server_data, cfg.server_type)

            # 显示使用的 API 源
            effective_source = cfg.api_source if cfg.api_source else self.api_source
            effective_host = cfg.mcmotdapi_host if cfg.mcmotdapi_host else self.mcmotdapi_host
            if effective_source == "mcmotdapi":
                source_tag = f"mcmotdapi({effective_host or 'motd.minebbs.com'})"
            else:
                source_tag = "mcstatus.io"
            result = f"🔍 API: {source_tag}\n\n{result}"

            if cfg.use_hitokoto:
                hitokoto = await get_hitokoto()
                if hitokoto:
                    result += f"\n\n💬 {hitokoto}"

            yield event.plain_result(result)
        except Exception as e:
            logger.warning(f"查询服务器状态时出错: {e}")
            yield event.plain_result(f"❌ 查询服务器时出错: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置监控")
    async def reset_monitor(self, event: AstrMessageEvent):
        """重置当前群的服务器状态缓存，下次检测视为首次检测。"""
        group_id = event.get_group_id()
        self._cache_event_session(event)
        if group_id and self.monitor.reset_tracker(group_id):
            self._run_log("INFO", f"群 {group_id} 的监控状态缓存已重置", group_id)
            yield event.plain_result("✅ 当前群的监控状态缓存已重置，下次检测将视为首次检测")
        else:
            msg = "✅ 当前群未配置监控，无需重置" if not group_id else "❌ 当前群未配置监控"
            yield event.plain_result(msg)
