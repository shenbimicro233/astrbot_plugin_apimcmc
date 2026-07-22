"""监控服务：运行时状态、定时检查与通知。

调用方式：from monitor.service import MonitorService
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from astrbot.api.star import Context

from ..mcserver import create_mc_client
from ..mcserver.formatter import format_server_info, format_server_info_simple
from ..utils import GroupTarget, build_group_session, send_to_group
from ..utils.group_config import ConfigManager, GroupConfig
from ..utils.hitokoto import get_hitokoto
from .tracker import ChangeTracker

RunLogFn = Callable[[str, str, str | None], None]


@dataclass
class MonitorConfig:
    """监控服务配置。"""

    check_interval: int = 10
    api_source: str = "mcstatus"
    mcmotdapi_host: str = "motd.minebbs.com"
    mcmotdapi_ssl: bool = True
    simple_mode: bool = False


class MonitorService:
    """封装 client/tracker/session 缓存与监控循环。"""

    def __init__(
        self,
        context: Context,
        config_mgr: ConfigManager,
        *,
        config: MonitorConfig | None = None,
        run_log: RunLogFn | None = None,
    ):
        self.context = context
        self.config_mgr = config_mgr
        cfg = config or MonitorConfig()
        self.check_interval = int(cfg.check_interval) if cfg.check_interval else 10
        self.api_source = cfg.api_source
        self.mcmotdapi_host = cfg.mcmotdapi_host
        self.mcmotdapi_ssl = cfg.mcmotdapi_ssl
        self.simple_mode = cfg.simple_mode
        self._run_log_fn = run_log

        self.task: asyncio.Task | None = None
        self._auto_start_task: asyncio.Task | None = None
        self._stopped = False

        self._group_clients: dict = {}
        self._group_trackers: dict[str, ChangeTracker] = {}
        self._group_sessions: dict[str, str] = {}

        self.last_check_at: str = ""
        self.last_round_summary: str = ""

        self.rebuild_all_group_state()

    # ── 日志 ──

    def _log(self, level: str, msg: str, group_id: str | None = None) -> None:
        if self._run_log_fn:
            self._run_log_fn(level, msg, group_id)

    def _log_buffer_only(self, level: str, msg: str, group_id: str | None = None) -> None:
        """send_to_group 已打 logger 时，仅写入缓冲。
        由 main 注入的 buffer_only 回调处理。
        """
        if self._run_log_buffer_only:
            self._run_log_buffer_only(level, msg, group_id)

    def set_run_log(
        self,
        run_log: RunLogFn | None,
        buffer_only: RunLogFn | None = None,
    ) -> None:
        self._run_log_fn = run_log
        self._run_log_buffer_only = buffer_only

    @staticmethod
    def _fmt_api_source(source: str, host: str = "") -> str:
        if source == "mcmotdapi":
            return f"mcmotdapi({host or 'motd.minebbs.com'})"
        return "mcstatus.io"

    @staticmethod
    def _resolve_effective_cfg(
        cfg: GroupConfig,
        default_source: str,
        default_host: str,
        default_ssl: bool | None,
    ) -> tuple[str, str, bool | None]:
        """合并 per-group 配置与全局默认值。

        Returns:
            (effective_source, effective_host, effective_ssl)
        """
        source = cfg.api_source if cfg.api_source else default_source
        host = cfg.mcmotdapi_host if cfg.mcmotdapi_host else default_host
        ssl = default_ssl if cfg.mcmotdapi_ssl is None else cfg.mcmotdapi_ssl
        return source, host, ssl

    # ── 运行时状态 ──

    async def _pop_client(self, group_id: str) -> None:
        """关闭并移除指定群的 client。"""
        client = self._group_clients.pop(group_id, None)
        if client:
            await client.close()

    def rebuild_all_group_state(self) -> None:
        configured = set(self.config_mgr.get_all().keys())
        existing = set(self._group_clients.keys())
        for gid in existing - configured:
            self._group_clients.pop(gid, None)
            self._group_trackers.pop(gid, None)

        for gid, cfg in self.config_mgr.get_enabled_groups().items():
            self._set_client(gid, cfg)
            if gid not in self._group_trackers:
                self._group_trackers[gid] = ChangeTracker()

    async def rebuild_single_group_state(self, group_id: str) -> None:
        await self._pop_client(group_id)
        cfg = self.config_mgr.get(group_id)
        if cfg and cfg.enabled and cfg.is_valid():
            self._set_client(group_id, cfg)
            if group_id not in self._group_trackers:
                self._group_trackers[group_id] = ChangeTracker()
        else:
            self._group_trackers.pop(group_id, None)

    def _set_client(self, group_id: str, cfg: GroupConfig) -> None:
        effective_source, effective_host, effective_ssl = self._resolve_effective_cfg(
            cfg, self.api_source, self.mcmotdapi_host, self.mcmotdapi_ssl,
        )
        source_display = self._fmt_api_source(effective_source, effective_host)
        self._log("INFO", f"群 {group_id} 已初始化客户端 | API: {source_display}", group_id)
        self._group_clients[group_id] = create_mc_client(
            api_source=effective_source,
            server_ip=cfg.server_ip,
            server_port=cfg.server_port,
            server_type=cfg.server_type,
            server_name=cfg.name,
            mcmotdapi_host=effective_host,
            mcmotdapi_ssl=effective_ssl,
        )

    def cache_group_session(self, group_id: str | None, umo: str | None) -> str | None:
        if not group_id or not umo:
            return None
        self._group_sessions[str(group_id)] = str(umo)
        return str(umo)

    def pop_group_session(self, group_id: str) -> None:
        self._group_sessions.pop(group_id, None)

    def reset_tracker(self, group_id: str) -> bool:
        tracker = self._group_trackers.get(group_id)
        if not tracker:
            return False
        tracker.reset()
        return True

    def has_tracker(self, group_id: str) -> bool:
        return group_id in self._group_trackers

    def is_running(self) -> bool:
        return bool(self.task and not self.task.done())

    # ── 启停 ──

    def schedule_auto_start(self) -> None:
        self._auto_start_task = asyncio.create_task(self._delayed_auto_start())

    async def _delayed_auto_start(self) -> None:
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            return
        if self._stopped:
            return
        if not self.is_running():
            self.start()
            self._log("INFO", "🚀 自动启动服务器监控任务")

    def start(self) -> bool:
        """启动监控循环。已在运行返回 False。"""
        if self.is_running():
            return False
        self._stopped = False
        self.task = asyncio.create_task(self._monitor_loop())
        return True

    def stop(self) -> bool:
        """停止监控循环。未运行返回 False。"""
        if not self.is_running():
            return False
        # 先标记停止，再取消任务，避免循环误判
        self._stopped = True
        self.task.cancel()
        self.task = None
        return True

    async def terminate(self) -> None:
        """插件卸载时清理任务、缓存与网络连接。"""
        self._stopped = True
        if self._auto_start_task and not self._auto_start_task.done():
            self._auto_start_task.cancel()
            self._auto_start_task = None
            self._log("INFO", "延迟自动启动任务已取消")
        if self.task and not self.task.done():
            self.task.cancel()
            self.task = None
            self._log("INFO", "定时监控任务已取消")

        for client in self._group_clients.values():
            await client.close()

        self._group_clients.clear()
        self._group_trackers.clear()
        self._group_sessions.clear()

    # ── 监控循环 ──

    async def _monitor_loop(self) -> None:
        self._log(
            "INFO",
            f"监控循环已启动 | 间隔 {self.check_interval}s | "
            f"当前启用群数: {len(self.config_mgr.get_enabled_groups())}",
        )
        while not self._stopped:
            loop_start = time.monotonic()

            enabled = self.config_mgr.get_enabled_groups()
            if not enabled:
                self._log("INFO", "本轮监控：无已启用的群配置，跳过")
                self.last_check_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.last_round_summary = "无已启用的群配置"
                await self._sleep_with_stop_check(loop_start)
                continue

            gids = list(enabled.keys())
            self._log("INFO", f"本轮监控开始 | 共 {len(gids)} 个群: {', '.join(gids)}")
            results = await asyncio.gather(
                *[self.check_one_group(gid) for gid in gids],
                return_exceptions=True,
            )

            ok = changed = failed = 0
            for gid, result in zip(gids, results):
                if isinstance(result, Exception):
                    failed += 1
                    self._log("ERROR", f"群 {gid} 检查异常: {result}", gid)
                elif result == "changed":
                    changed += 1
                    ok += 1
                elif result == "failed":
                    failed += 1
                else:
                    ok += 1

            summary = f"成功 {ok} | 有变化并通知 {changed} | 失败 {failed}"
            self.last_check_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.last_round_summary = summary
            self._log("INFO", f"本轮监控结束 | {summary}")

            await self._sleep_with_stop_check(loop_start)

        self._log("INFO", "监控循环已退出")

    async def _sleep_with_stop_check(self, loop_start: float) -> None:
        """补偿本轮检测耗时后睡眠，避免时间漂移积累。中途被停止则提前退出。"""
        elapsed = time.monotonic() - loop_start
        sleep_time = max(0, self.check_interval - elapsed)
        if sleep_time > 0 and not self._stopped:
            await asyncio.sleep(sleep_time)

    async def check_one_group(self, group_id: str) -> str:
        """检查单个群。返回: ok / changed / failed / skipped。"""
        try:
            client = self._group_clients.get(group_id)
            tracker = self._group_trackers.get(group_id)
            if not client or not tracker:
                self._log("WARN", f"群 {group_id} 缺少 client/tracker，跳过", group_id)
                return "skipped"

            cfg = self.config_mgr.get(group_id)
            target = f"{cfg.server_ip}:{cfg.server_port}" if cfg else "?"
            name = cfg.name if cfg else ""
            effective_source, effective_host, _ = self._resolve_effective_cfg(
                cfg, self.api_source, self.mcmotdapi_host, self.mcmotdapi_ssl,
            )
            source_display = self._fmt_api_source(effective_source, effective_host)
            self._log(
                "INFO",
                f"正在检查群 {group_id} | {name} ({target}) | API: {source_display}",
                group_id,
            )

            server_data = await client.get_server_info()
            if server_data is None:
                self._log("WARN", f"群 {group_id} 的服务器请求失败，跳过", group_id)
                return "failed"

            should_send, change_msg = tracker.check_changes(server_data)
            if not should_send:
                self._log("INFO", f"群 {group_id} 状态无变化", group_id)
                return "ok"

            change_one_line = change_msg.replace("\n", " | ")
            self._log(
                "CHANGED",
                f"群 {group_id} 检测到变化: {change_one_line}",
                group_id,
            )

            server_type = cfg.server_type if cfg else "bedrock"

            # 根据简化模式选择消息格式
            effective_simple = cfg.simple_mode if cfg.simple_mode is not None else self.simple_mode
            if effective_simple:
                status_line = format_server_info_simple(server_data, server_type)
                final_msg = f"🔔 服务器状态变化：{change_msg}\n\n{status_line}"
            else:
                full_status = format_server_info(server_data, server_type)
                final_msg = f"🔔 服务器状态变化：\n{change_msg}\n\n📊 当前状态：\n{full_status}"

            if cfg and cfg.use_hitokoto:
                hitokoto = await get_hitokoto()
                if hitokoto:
                    final_msg += f"\n\n💬 {hitokoto}"

            session = self._group_sessions.get(group_id)
            if not session:
                session = build_group_session(self.context, group_id)
                self._group_sessions[group_id] = session

            target = GroupTarget(
                context=self.context,
                group_id=group_id,
                session=session,
            )
            sent = await send_to_group(
                target,
                final_msg,
                run_log=self._log_buffer_only,
            )
            return "changed" if sent else "failed"

        except Exception as e:
            self._log("ERROR", f"检查群 {group_id} 时出错: {e}", group_id)
            return "failed"
