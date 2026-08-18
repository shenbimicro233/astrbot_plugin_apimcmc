"""带隔离准入控制的一次性服务器查询。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..mcserver import create_mc_client
from .model import (
    AddressParseError,
    EditionParseError,
    QueryRequest,
    QuerySource,
    ServerEdition,
    StatusCard,
    format_display_address,
    parse_server_address,
    parse_server_edition,
    project_status_card,
)


DEFAULT_SINGLE_FLIGHT = True
DEFAULT_COOLDOWN_SECONDS = 5
DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_SOURCE = "local"
DEFAULT_MCMOTDAPI_HOST = "motd.minebbs.com"
DEFAULT_MCMOTDAPI_SSL = True
_VALID_SOURCES = frozenset({"mcstatus", "mcmotdapi", "local"})


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


@dataclass(frozen=True)
class ServerQuerySettings:
    """严格规范化的全局资源控制配置。"""

    single_flight: bool = DEFAULT_SINGLE_FLIGHT
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY

    @classmethod
    def from_config(cls, config: Mapping[str, object] | object) -> "ServerQuerySettings":
        """仅读取已声明的三个设置，并安全恢复默认值。"""

        getter = getattr(config, "get", None)
        if not callable(getter):
            return cls()

        raw_single_flight = getter("server_query_single_flight", DEFAULT_SINGLE_FLIGHT)
        raw_cooldown = getter("server_query_cooldown_seconds", DEFAULT_COOLDOWN_SECONDS)
        raw_concurrency = getter("server_query_max_concurrency", DEFAULT_MAX_CONCURRENCY)

        single_flight = (
            raw_single_flight if type(raw_single_flight) is bool else DEFAULT_SINGLE_FLIGHT
        )
        cooldown_seconds = (
            raw_cooldown
            if type(raw_cooldown) is int and raw_cooldown >= 0
            else DEFAULT_COOLDOWN_SECONDS
        )
        max_concurrency = (
            raw_concurrency
            if type(raw_concurrency) is int and 1 <= raw_concurrency <= 32
            else DEFAULT_MAX_CONCURRENCY
        )
        return cls(
            single_flight=single_flight,
            cooldown_seconds=cooldown_seconds,
            max_concurrency=max_concurrency,
        )


def normalize_query_source(
    source: object,
    mcmotdapi_host: object,
    mcmotdapi_ssl: object,
) -> QuerySource:
    """在不修改已存储配置的情况下规范化查询源设置。"""

    normalized_source = str(source).strip().lower()
    if normalized_source not in _VALID_SOURCES:
        normalized_source = "mcstatus"
    host = str(mcmotdapi_host).strip() if isinstance(mcmotdapi_host, str) else ""
    return QuerySource(
        source=normalized_source,
        mcmotdapi_host=host or DEFAULT_MCMOTDAPI_HOST,
        mcmotdapi_ssl=(mcmotdapi_ssl if type(mcmotdapi_ssl) is bool else DEFAULT_MCMOTDAPI_SSL),
    )


class ServerQueryService:
    """在不触及监控状态的情况下解析并执行独立服务器查询。"""

    def __init__(
        self,
        config_mgr: Any,
        *,
        resolve_effective_config: Callable[[Any], tuple[object, object, object, object]],
        global_source: QuerySource | None = None,
        is_query_config_valid: Callable[[Any], bool] | None = None,
        settings: ServerQuerySettings | None = None,
        client_factory: Callable[..., Any] = create_mc_client,
        clock: Callable[[], float] = time.monotonic,
        logger: Callable[[str, str], None] | None = None,
    ) -> None:
        self._config_mgr = config_mgr
        self._resolve_effective_config = resolve_effective_config
        self._is_query_config_valid = is_query_config_valid
        self.settings = settings or ServerQuerySettings()
        source = global_source or QuerySource(
            source=DEFAULT_SOURCE,
            mcmotdapi_host=DEFAULT_MCMOTDAPI_HOST,
            mcmotdapi_ssl=DEFAULT_MCMOTDAPI_SSL,
        )
        self.global_source = normalize_query_source(
            source.source,
            source.mcmotdapi_host,
            source.mcmotdapi_ssl,
        )
        self._client_factory = client_factory
        self._clock = clock
        self._logger = logger

        self._cooldowns: dict[tuple[str, str], float] = {}
        self._flights: dict[QueryRequest, asyncio.Task[StatusCard]] = {}
        self._tasks: set[asyncio.Task[StatusCard]] = set()
        self._active_queries = 0
        self._closed = False

    @property
    def active_queries(self) -> int:
        """返回正在运行的上游查询数量，供诊断和测试使用。"""

        return self._active_queries

    @staticmethod
    def _normalized_group_port(value: object) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value if 1 <= value <= 65535 else None

    @staticmethod
    def _group_enable_srv(cfg: Any) -> bool:
        value = getattr(cfg, "enable_srv", True)
        return value if type(value) is bool else True

    def _source_for_group(self, cfg: Any) -> QuerySource:
        try:
            source, host, ssl, _enable_srv = self._resolve_effective_config(cfg)
        except Exception:
            return self.global_source
        return normalize_query_source(source, host, ssl)

    def _request_from_group(self, cfg: Any, edition: ServerEdition) -> QueryRequest:
        identity_checker = getattr(cfg, "has_query_identity", None)
        if callable(identity_checker) and not identity_checker():
            raise QueryConfigError("stored group identity is invalid")
        raw_host = getattr(cfg, "server_ip", "")
        try:
            parsed = parse_server_address(raw_host)
        except AddressParseError as exc:
            raise QueryConfigError("stored server host is invalid") from exc
        if parsed.has_explicit_port:
            raise QueryConfigError("stored server host includes a port")

        port = self._normalized_group_port(getattr(cfg, "server_port", None))
        enable_srv = self._group_enable_srv(cfg) if edition is ServerEdition.JAVA else False
        if port is None and not (edition is ServerEdition.JAVA and enable_srv):
            raise QueryConfigError("stored server target needs a port")

        return QueryRequest(
            host=parsed.host,
            port=port,
            edition=edition,
            enable_srv=enable_srv,
            source=self._source_for_group(cfg),
            display_address=format_display_address(parsed.host, port),
        )

    def _get_group_config(self, group_id: str) -> Any | None:
        getter = getattr(self._config_mgr, "get", None)
        return getter(group_id) if callable(getter) else None

    def resolve_request(
        self,
        group_id: str,
        server_type: object = "",
        address: object = "",
    ) -> QueryRequest:
        """将一种命令形式解析为完整且不可变的请求。"""

        normalized_type = server_type.strip() if isinstance(server_type, str) else ""
        normalized_address = address.strip() if isinstance(address, str) else ""
        if not isinstance(address, str) or normalized_address != address:
            raise QueryUsageError("address must be a single token")

        cfg = self._get_group_config(str(group_id))
        if normalized_address:
            try:
                edition = parse_server_edition(normalized_type)
                parsed = parse_server_address(normalized_address)
            except (AddressParseError, EditionParseError) as exc:
                raise QueryUsageError("invalid explicit target") from exc

            if edition is ServerEdition.JAVA:
                port = parsed.port
                enable_srv = not parsed.has_explicit_port
            else:
                port = parsed.port if parsed.has_explicit_port else 19132
                enable_srv = False
            source = self._source_for_group(cfg) if cfg is not None else self.global_source
            return QueryRequest(
                host=parsed.host,
                port=port,
                edition=edition,
                enable_srv=enable_srv,
                source=source,
                display_address=format_display_address(parsed.host, port),
            )

        if not isinstance(server_type, str):
            raise QueryUsageError("server type must be a string")
        if cfg is None:
            raise QueryConfigError("group configuration is missing")

        if normalized_type:
            try:
                edition = parse_server_edition(normalized_type)
            except EditionParseError as exc:
                raise QueryUsageError("invalid requested edition") from exc
        else:
            try:
                edition = parse_server_edition(getattr(cfg, "server_type", ""))
            except EditionParseError as exc:
                raise QueryConfigError("stored server edition is invalid") from exc
            if self._is_query_config_valid is not None and not self._is_query_config_valid(cfg):
                raise QueryConfigError("stored server configuration is invalid")

        return self._request_from_group(cfg, edition)

    def _log(self, level: str, message: str) -> None:
        if self._logger is not None:
            self._logger(level, message)

    async def _perform_query(self, request: QueryRequest) -> StatusCard:
        try:
            async with self._client_factory(
                api_source=request.source.source,
                server_ip=request.host,
                server_port=request.port,
                server_type=request.edition.value,
                mcmotdapi_host=request.source.mcmotdapi_host,
                mcmotdapi_ssl=request.source.mcmotdapi_ssl,
                enable_srv=request.enable_srv,
            ) as client:
                server_data = await client.get_server_info()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log(
                "WARN",
                f"查询请求失败（{request.source.source}，{type(exc).__name__}）",
            )
            return StatusCard.unreachable(request)

        try:
            card = project_status_card(request, server_data)
        except Exception as exc:
            self._log(
                "WARN",
                f"查询响应投影失败（{request.source.source}，{type(exc).__name__}）",
            )
            return StatusCard.unreachable(request)
        if card.status.value == "unreachable":
            self._log(
                "WARN",
                f"查询请求未得到有效响应（{request.source.source}，InvalidResponse）",
            )
        return card

    def _finalize_flight(self, request: QueryRequest, task: asyncio.Task[StatusCard]) -> None:
        """即使任务启动前取消，也释放其准入状态。"""

        self._tasks.discard(task)
        self._active_queries = max(0, self._active_queries - 1)
        if self._flights.get(request) is task:
            self._flights.pop(request, None)

    def _start_flight(self, request: QueryRequest) -> asyncio.Task[StatusCard]:
        self._active_queries += 1
        task = asyncio.create_task(self._perform_query(request))
        self._tasks.add(task)
        task.add_done_callback(lambda completed: self._finalize_flight(request, completed))
        if self.settings.single_flight:
            self._flights[request] = task
        return task

    def _prune_expired_cooldowns(self, now: float) -> None:
        """丢弃不再能拒绝请求的发送者状态。"""

        cooldown = self.settings.cooldown_seconds
        if cooldown <= 0:
            self._cooldowns.clear()
            return
        for sender_key, accepted_at in tuple(self._cooldowns.items()):
            if now - accepted_at >= cooldown:
                self._cooldowns.pop(sender_key, None)

    async def query(
        self,
        request: QueryRequest,
        *,
        group_id: object,
        sender_id: object,
    ) -> StatusCard:
        """通过准入检查后，执行或加入恰好一个上游查询。"""

        if self._closed:
            raise QueryClosedError("service is closing")

        sender_key = str(group_id), str(sender_id)
        now = self._clock()
        self._prune_expired_cooldowns(now)
        if self.settings.cooldown_seconds > 0:
            previous = self._cooldowns.get(sender_key)
            if previous is not None and now - previous < self.settings.cooldown_seconds:
                raise QueryCooldownError("sender cooldown is active")

        existing = self._flights.get(request) if self.settings.single_flight else None
        if existing is not None:
            if self.settings.cooldown_seconds > 0:
                self._cooldowns[sender_key] = now
            return await asyncio.shield(existing)

        if self._active_queries >= self.settings.max_concurrency:
            raise QueryBusyError("global upstream capacity is exhausted")

        if self.settings.cooldown_seconds > 0:
            self._cooldowns[sender_key] = now
        return await asyncio.shield(self._start_flight(request))

    async def close(self) -> None:
        """拒绝新请求，并仅取消本服务的查询任务。"""

        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ = [
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_MAX_CONCURRENCY",
    "DEFAULT_SINGLE_FLIGHT",
    "QueryBusyError",
    "QueryClosedError",
    "QueryConfigError",
    "QueryCooldownError",
    "QueryUsageError",
    "ServerQueryError",
    "ServerQueryService",
    "ServerQuerySettings",
    "normalize_query_source",
]
