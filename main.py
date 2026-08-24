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

版本: v1.2.6
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .monitor.run_log import RunLogBuffer
from .monitor.service import MonitorConfig, MonitorService
from .player_stats.renderer import PlayerStatsRenderer, normalize_text_style
from .player_stats.store import PlayerStatsStore
from .server_status.model import QuerySource
from .server_status.renderer import ServerStatusRenderer
from .server_status.service import (
    QueryBusyError,
    QueryClosedError,
    QueryConfigError,
    QueryCooldownError,
    QueryUsageError,
    ServerQueryService,
    ServerQuerySettings,
)
from .utils.group_config import CONFIG_DATA_DIR, ConfigManager
from .web import WebApiConfig, WebApiHandler

PLUGIN_NAME = "astrbot_plugin_minecraft_monitor_plus"
PLUGIN_VERSION = "1.2.6"


@register(
    "astrbot_plugin_minecraft_monitor_plus",
    "shenbimicro233",
    "Minecraft服务器监控插件，支持为每个群独立配置服务器监控。通过WebUI管理每群配置，定时检测服务器状态变化并通过机器人发送通知。",
    PLUGIN_VERSION,
)
class MyPlugin(Star):
    """Minecraft 服务器监控插件。"""

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}

        self.check_interval = self.config.get("check_interval", 10)
        self.enable_auto = self.config.get("enable_auto_monitor", False)

        # API 源配置
        self.api_source = self.config.get("api_source", "local")
        self.mcmotdapi_host = self.config.get("mcmotdapi_host", "motd.minebbs.com")
        self.mcmotdapi_ssl = self.config.get("mcmotdapi_ssl", True)
        self.simple_mode = self.config.get("simple_mode", False)
        self.statistics_fonts = self.config.get("statistics_fonts", [])
        self.statistics_text_style = normalize_text_style(
            self.config.get("statistics_text_style", "monocraft")
        )

        self._run_logs = RunLogBuffer()
        self.config_mgr = ConfigManager()
        self.stats_store = PlayerStatsStore(
            CONFIG_DATA_DIR / "player_statistics.json",
            logger=self._run_log,
        )
        self.stats_renderer = PlayerStatsRenderer(
            CONFIG_DATA_DIR / "statistics_temp",
            font_candidates=self.statistics_fonts,
            text_style=self.statistics_text_style,
            logger=self._run_log,
        )
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
            stats_store=self.stats_store,
        )
        self.check_interval = self.monitor.check_interval
        self.monitor.set_run_log(self._run_log, buffer_only=self._run_log_buffer_only)
        self.server_query_service = ServerQueryService(
            self.config_mgr,
            resolve_effective_config=self.monitor.resolve_effective_config,
            is_query_config_valid=self.monitor.is_query_config_valid,
            global_source=QuerySource(
                source=self.api_source,
                mcmotdapi_host=self.mcmotdapi_host,
                mcmotdapi_ssl=self.mcmotdapi_ssl,
            ),
            settings=ServerQuerySettings.from_config(self.config),
            logger=self._run_log,
        )
        self.server_status_renderer = ServerStatusRenderer(
            CONFIG_DATA_DIR / "server_status_temp",
            font_candidates=self.statistics_fonts,
            logger=self._run_log,
        )

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

    def _run_log_buffer_only(self, level: str, msg: str, group_id: str | None = None) -> None:
        """仅写入缓冲（notifier 内部已打 logger）。"""
        self._run_logs.append(level, msg, group_id)

    # ──────────────────────────────
    # 生命周期
    # ──────────────────────────────

    async def _prepare_player_stats(self) -> None:
        """Reconcile bounded statistics and renderer resources at startup."""

        try:
            configured_groups = set(self.config_mgr.get_all())
            configured_targets = {
                group_id: target
                for group_id in configured_groups
                if (cfg := self.config_mgr.get(group_id)) is not None
                and self.monitor.is_query_config_valid(cfg)
                and (target := self.monitor.get_target_signature(group_id)) is not None
            }
            removed = await asyncio.to_thread(
                self.stats_store.retain_current_targets,
                configured_targets,
            )
            if removed:
                self._run_log("INFO", f"已清理 {removed} 个失效群的玩家统计")
        except Exception as exc:
            self._run_log("WARN", f"清理失效群玩家统计失败: {exc}")

        try:
            await asyncio.to_thread(self.stats_renderer.cleanup_stale)
        except Exception as exc:
            self._run_log("WARN", f"清理过期玩家统计图片失败: {exc}")

        if self.stats_renderer.requires_bundled_monocraft and not await asyncio.to_thread(
            self.stats_renderer.has_bundled_monocraft
        ):
            self._run_log(
                "WARN",
                "当前统计图文字样式需要内置 Monocraft，但字体不可用，将使用备用字体",
            )

        requires_unifont = getattr(
            self.stats_renderer,
            "requires_bundled_unifont",
            getattr(self.stats_renderer, "requires_bundled_fusion_pixel", False),
        )
        has_unifont = getattr(
            self.stats_renderer,
            "has_bundled_unifont",
            getattr(self.stats_renderer, "has_bundled_fusion_pixel", lambda: False),
        )
        if requires_unifont and not await asyncio.to_thread(has_unifont):
            self._run_log(
                "WARN",
                "普通中文统计图需要内置 Unifont 显示全部文字，但字体不可用，将使用备用字体",
            )

    async def _prepare_server_status(self) -> None:
        """Clean only expired server-status images and validate its shared font."""

        try:
            await asyncio.to_thread(self.server_status_renderer.cleanup_stale)
        except Exception as exc:
            self._run_log("WARN", f"清理过期查询图片失败: {type(exc).__name__}")

        has_unifont = getattr(
            self.server_status_renderer,
            "has_bundled_unifont",
            getattr(self.server_status_renderer, "has_bundled_fusion_pixel", lambda: False),
        )
        if not await asyncio.to_thread(has_unifont):
            self._run_log(
                "WARN",
                "查询图片需要内置 Unifont，但字体不可用，将使用备用字体",
            )

    async def initialize(self):
        """插件初始化完成后调用，可执行异步启动逻辑。"""
        await self._prepare_player_stats()
        await self._prepare_server_status()
        self._run_log(
            "INFO",
            "Minecraft服务器监控插件已加载 | 使用 /start_server_monitor 启动监控 | 在 WebUI 插件页配置每群监控",
        )

        if self.enable_auto:
            self.monitor.schedule_auto_start()

    async def terminate(self):
        """插件停用/重载前调用，仅做资源清理，不删除持久化数据。"""
        self._run_log("INFO", "正在清理 Minecraft 监控插件...")
        await self.server_query_service.close()
        try:
            await asyncio.to_thread(self.server_status_renderer.cleanup_all)
        except Exception as exc:
            self._run_log("WARN", f"清理查询图片失败: {type(exc).__name__}")
        await self.monitor.terminate()
        try:
            await asyncio.to_thread(self.stats_renderer.cleanup_all)
        except Exception as exc:
            self._run_log("WARN", f"清理玩家统计图片失败: {exc}")
        self._run_logs.clear()
        logger.info("Minecraft 监控插件已完全卸载")

    # ──────────────────────────────
    # 命令
    # ──────────────────────────────

    def _cache_event_session(self, event: AstrMessageEvent) -> None:
        """缓存群会话，供监控任务主动发送通知。"""
        group_id = event.get_group_id()
        if not group_id:
            return
        group_id = str(group_id)
        umo = getattr(event, "unified_msg_origin", None)
        self.monitor.cache_group_session(group_id, umo)

    @staticmethod
    def _has_valid_server_query_arity(event: AstrMessageEvent) -> bool:
        """拒绝 AstrBot 可选参数过滤器忽略的多余原始参数。"""

        get_message = getattr(event, "get_message_str", None)
        if not callable(get_message):
            return True
        message = get_message()
        if not isinstance(message, str):
            return False
        tokens = message.strip().split()
        for index, token in enumerate(tokens):
            if token.lstrip("/") == "查询":
                return len(tokens[index + 1 :]) <= 2
        return True

    @staticmethod
    def _server_query_sender_id(event: AstrMessageEvent) -> str:
        get_sender_id = getattr(event, "get_sender_id", None)
        if callable(get_sender_id):
            return str(get_sender_id())
        return ""

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

    @filter.permission_type(filter.PermissionType.MEMBER)
    @filter.command("查询")
    async def query_server(
        self,
        event: AstrMessageEvent,
        server_type: str = "",
        address: str = "",
    ):
        """查询服务器状态并返回本地生成的状态图片。"""

        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 该命令仅在群聊中使用")
            return
        if not self._has_valid_server_query_arity(event):
            yield event.plain_result("❌ 用法：/查询 [java|bedrock] [地址]")
            return

        group_id = str(group_id)

        try:
            request = self.server_query_service.resolve_request(
                group_id,
                server_type,
                address,
            )
        except QueryUsageError:
            yield event.plain_result("❌ 用法：/查询 [java|bedrock] [地址]")
            return
        except QueryConfigError:
            yield event.plain_result("❌ 当前群的服务器配置无效，请管理员检查配置")
            return

        try:
            card = await self.server_query_service.query(
                request,
                group_id=group_id,
                sender_id=self._server_query_sender_id(event),
            )
        except QueryCooldownError:
            yield event.plain_result("⏳ 查询过于频繁，请稍后再试")
            return
        except QueryBusyError:
            yield event.plain_result("⏳ 当前查询请求较多，请稍后再试")
            return
        except QueryClosedError:
            return
        except asyncio.CancelledError:
            raise

        try:
            image_path = await asyncio.to_thread(self.server_status_renderer.render, card)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._run_log(
                "WARN",
                f"生成群 {group_id} 的查询图片失败: {type(exc).__name__}",
                group_id,
            )
            yield event.plain_result("❌ 生成查询图片失败，请稍后重试")
            return
        yield event.image_result(str(image_path))

    @filter.permission_type(filter.PermissionType.MEMBER)
    @filter.command("在线统计")
    async def get_player_statistics(self, event: AstrMessageEvent):
        """返回当前群服务器最近 24 小时的玩家在线人数统计图。"""

        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 该命令仅在群聊中使用")
            return
        group_id = str(group_id)

        cfg = self.config_mgr.get(group_id)
        is_query_config_valid = getattr(self.monitor, "is_query_config_valid", None)
        if not cfg or not (
            is_query_config_valid(cfg) if callable(is_query_config_valid) else cfg.is_valid()
        ):
            yield event.plain_result(
                "❌ 当前群未配置监控，请管理员在 WebUI 插件页面「Minecraft服务器监控」中添加配置"
            )
            return

        try:
            target_signature = self.monitor.get_target_signature(group_id)
            if target_signature is None:
                yield event.plain_result("❌ 当前群的服务器配置无效，请管理员检查配置")
                return
            now = datetime.now().astimezone()
            series = await asyncio.to_thread(
                self.stats_store.get_window,
                group_id,
                target_signature,
            )
            if series is None or not series.observations:
                yield event.plain_result(
                    "📊 最近24小时暂无在线人数统计，请先启动服务器监控并等待成功查询"
                )
                return

            image_path = await asyncio.to_thread(
                self.stats_renderer.render,
                series,
                server_type=cfg.server_type,
                now=now,
            )
            yield event.image_result(str(image_path))
        except Exception as exc:
            self._run_log(
                "WARN",
                f"生成群 {group_id} 的玩家统计图失败: {type(exc).__name__}",
                group_id,
            )
            yield event.plain_result("❌ 生成在线统计图失败，请稍后重试")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置监控")
    async def reset_monitor(self, event: AstrMessageEvent):
        """重置当前群的服务器状态缓存，下次检测视为首次检测。"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("❌ 该命令仅在群聊中使用")
            return
        group_id = str(group_id)
        if self.monitor.reset_tracker(group_id):
            self._run_log("INFO", f"群 {group_id} 的监控状态缓存已重置", group_id)
            yield event.plain_result("✅ 当前群的监控状态缓存已重置，下次检测将视为首次检测")
        else:
            yield event.plain_result("❌ 当前群未配置监控")
