"""Bounded atomic JSON persistence for player-count observations."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .model import (
    PlayerCountObservation,
    PlayerCountWindowSeries,
    WINDOW_DURATION,
    WINDOW_SLOT_COUNT,
    normalize_count,
    slot_for_datetime,
    target_id_for_signature,
    to_local_datetime,
)

SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
DEFAULT_FILENAME = "player_statistics.json"
_TARGET_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")

LogFn = Callable[[str, str], None]


class PlayerStatsStore:
    """Keep one bounded rolling 24-hour series per configured group."""

    def __init__(
        self,
        file_path: str | Path | None = None,
        *,
        logger: logging.Logger | LogFn | None = None,
    ) -> None:
        self.file_path = Path(file_path) if file_path is not None else self._default_path()
        self._logger = logger
        self._lock = threading.RLock()
        self._groups: dict[str, PlayerCountWindowSeries] = {}
        self._load()

    @staticmethod
    def _default_path() -> Path:
        try:
            from ..utils.group_config import CONFIG_DATA_DIR
        except (ImportError, ModuleNotFoundError, ValueError):
            try:
                # 独立工具和测试可能将 ``player_stats`` 作为顶层包导入，
                # 而不是通过 AstrBot 插件包导入，此时上面的相对导入没有父包。
                from utils.group_config import CONFIG_DATA_DIR
            except (ImportError, ModuleNotFoundError, ValueError):
                return Path(DEFAULT_FILENAME)
        return CONFIG_DATA_DIR / DEFAULT_FILENAME

    def _log(self, level: str, message: str) -> None:
        if callable(self._logger) and not isinstance(self._logger, logging.Logger):
            self._logger(level.upper(), message)
            return
        target = (
            self._logger
            if isinstance(self._logger, logging.Logger)
            else logging.getLogger(__name__)
        )
        method = "warning" if level.lower() == "warn" else level.lower()
        getattr(target, method, target.info)(message)

    @staticmethod
    def _normalize_group_id(group_id: object) -> str:
        normalized = str(group_id).strip()
        if not normalized:
            raise ValueError("group_id cannot be empty")
        return normalized

    @staticmethod
    def _normalize_target_id(target: str | Sequence[object]) -> str:
        if isinstance(target, str):
            normalized = target.strip().lower()
            if not _TARGET_ID_PATTERN.fullmatch(normalized):
                raise ValueError("target_id must be a SHA-256 hex digest")
            return normalized
        return target_id_for_signature(target)

    def _load(self) -> None:
        with self._lock:
            try:
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
                if not self.file_path.exists():
                    self._groups = {}
                    return
                with self.file_path.open("r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, json.JSONDecodeError, UnicodeError) as exc:
                self._log("error", f"加载玩家统计数据失败，已使用空数据: {exc}")
                self._groups = {}
                return

            if not isinstance(raw, Mapping):
                self._log("error", "玩家统计数据根节点不是对象，已使用空数据")
                self._groups = {}
                return
            raw_version = raw.get("schema_version")
            if type(raw_version) is not int or raw_version not in (
                SCHEMA_VERSION,
                LEGACY_SCHEMA_VERSION,
            ):
                self._log("error", "玩家统计数据版本不受支持，已使用空数据")
                self._groups = {}
                return

            raw_groups = raw.get("groups")
            if not isinstance(raw_groups, Mapping):
                self._log("error", "玩家统计数据 groups 字段无效，已使用空数据")
                self._groups = {}
                return

            loaded: dict[str, PlayerCountWindowSeries] = {}
            for raw_group_id, raw_series in raw_groups.items():
                try:
                    group_id = self._normalize_group_id(raw_group_id)
                    if raw_version == LEGACY_SCHEMA_VERSION:
                        series = self._decode_legacy_series(group_id, raw_series)
                    else:
                        series = self._decode_series(group_id, raw_series)
                    loaded[group_id] = series
                except (TypeError, ValueError) as exc:
                    self._log("warn", f"跳过无效玩家统计数据组 {raw_group_id!r}: {exc}")
            if raw_version == LEGACY_SCHEMA_VERSION and loaded:
                self._log(
                    "info",
                    "已把旧版玩家统计迁移到滚动 24 小时窗口，将在下次写入时保存为新格式",
                )
            self._groups = loaded

    def reload(self) -> None:
        """Reload the committed file, retaining valid sibling records."""

        self._load()

    @staticmethod
    def _prune_window(series: PlayerCountWindowSeries) -> PlayerCountWindowSeries:
        """Retain observations inside the exact final 24 hours, capped at 96 slots."""

        if not series.observations:
            return series
        cutoff = series.observations[-1].observed_at - WINDOW_DURATION
        retained = tuple(
            observation for observation in series.observations if observation.observed_at >= cutoff
        )[-WINDOW_SLOT_COUNT:]
        if len(retained) == len(series.observations):
            return series
        return PlayerCountWindowSeries(series.group_id, series.target_id, retained)

    def _decode_series(
        self,
        group_id: str,
        payload: object,
    ) -> PlayerCountWindowSeries:
        if not isinstance(payload, Mapping):
            raise TypeError("group series must be an object")

        target_id = self._normalize_target_id(str(payload.get("target_id", "")))
        raw_slots = payload.get("slots")
        if not isinstance(raw_slots, Mapping):
            raise TypeError("series slots must be an object")

        observations: list[PlayerCountObservation] = []
        seen_slots: set[int] = set()
        for raw_slot, raw_observation in raw_slots.items():
            try:
                if isinstance(raw_slot, bool):
                    raise TypeError("slot key must be an integer")
                slot = int(str(raw_slot))
                if slot in seen_slots:
                    raise ValueError("duplicate normalized slot")
                if not isinstance(raw_observation, Mapping):
                    raise TypeError("observation must be an object")
                observation = PlayerCountObservation.from_mapping(
                    raw_observation,
                    slot=slot,
                )
                observations.append(observation)
                seen_slots.add(slot)
            except (TypeError, ValueError) as exc:
                self._log(
                    "warn",
                    f"跳过组 {group_id} 的无效玩家统计槽 {raw_slot!r}: {exc}",
                )

        return self._prune_window(
            PlayerCountWindowSeries(
                group_id=group_id,
                target_id=target_id,
                observations=observations,
            )
        )

    def _decode_legacy_series(
        self,
        group_id: str,
        payload: object,
    ) -> PlayerCountWindowSeries:
        """Decode a schema-v1 local-day series into absolute time slots."""

        if not isinstance(payload, Mapping):
            raise TypeError("group series must be an object")

        target_id = self._normalize_target_id(str(payload.get("target_id", "")))
        raw_date = payload.get("date")
        if not isinstance(raw_date, str):
            raise TypeError("series date must be a string")
        raw_buckets = payload.get("buckets")
        if not isinstance(raw_buckets, Mapping):
            raise TypeError("series buckets must be an object")

        by_bucket: dict[int, PlayerCountObservation] = {}
        for raw_bucket, raw_observation in raw_buckets.items():
            try:
                if isinstance(raw_bucket, bool):
                    raise TypeError("bucket key must be an integer")
                bucket = int(str(raw_bucket))
                if bucket in by_bucket:
                    raise ValueError("duplicate normalized bucket")
                if not isinstance(raw_observation, Mapping):
                    raise TypeError("observation must be an object")
                observation = PlayerCountObservation.from_mapping(
                    raw_observation,
                    bucket=bucket,
                )
                if observation.observed_at.date().isoformat() != raw_date:
                    raise ValueError("observation date does not match series date")
                by_bucket[bucket] = observation
            except (TypeError, ValueError) as exc:
                self._log(
                    "warn",
                    f"跳过组 {group_id} 的无效玩家统计桶 {raw_bucket!r}: {exc}",
                )

        # 夏令时回拨可能将两个墙上时钟时间槽映射到同一绝对时间槽；保留第一个（墙上时钟最早的）样本。
        by_slot: dict[int, PlayerCountObservation] = {}
        for bucket in sorted(by_bucket):
            observation = by_bucket[bucket]
            if observation.slot in by_slot:
                self._log(
                    "warn",
                    f"跳过组 {group_id} 重复的绝对时间槽 {observation.slot}",
                )
                continue
            by_slot[observation.slot] = observation

        return self._prune_window(
            PlayerCountWindowSeries(
                group_id=group_id,
                target_id=target_id,
                observations=by_slot.values(),
            )
        )

    @staticmethod
    def _serialize(groups: Mapping[str, PlayerCountWindowSeries]) -> dict[str, Any]:
        raw_groups: dict[str, Any] = {}
        for group_id, series in sorted(groups.items()):
            raw_groups[group_id] = {
                "target_id": series.target_id,
                "slots": {
                    str(observation.slot): observation.to_dict()
                    for observation in series.observations
                },
            }
        return {"schema_version": SCHEMA_VERSION, "groups": raw_groups}

    def _persist(self, groups: Mapping[str, PlayerCountWindowSeries]) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.file_path.with_suffix(self.file_path.suffix + ".tmp")
        try:
            with temp_file.open("w", encoding="utf-8") as handle:
                json.dump(
                    self._serialize(groups),
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.flush()
                os.fsync(handle.fileno())
            temp_file.replace(self.file_path)
        except Exception:
            temp_file.unlink(missing_ok=True)
            raise

    @staticmethod
    def _count_from_server_data(
        server_data: Mapping[str, object],
        primary: str,
        aliases: tuple[str, ...],
    ) -> int:
        value: object = None
        found = False
        for key in (primary, *aliases):
            if key in server_data:
                value = server_data[key]
                found = True
                break
        if not found:
            raise ValueError(f"{primary} count is required")
        return normalize_count(value, primary)

    @staticmethod
    def _online_state_from_server_data(server_data: Mapping[str, object]) -> bool:
        """Project the normalized monitor status without breaking legacy callers."""

        if "status" not in server_data:
            return True
        raw_status = server_data["status"]
        if not isinstance(raw_status, str):
            raise TypeError("status must be online or offline")
        status = raw_status.strip().lower()
        if status == "online":
            return True
        if status == "offline":
            return False
        raise ValueError("status must be online or offline")

    def record(
        self,
        group_id: object,
        target: str | Sequence[object],
        server_data: Mapping[str, object],
        observed_at: datetime | str | None = None,
    ) -> bool:
        """Persist the first valid observation in a group/target absolute slot."""

        if not isinstance(server_data, Mapping):
            raise TypeError("server_data must be an object")
        normalized_group_id = self._normalize_group_id(group_id)
        target_id = self._normalize_target_id(target)
        local_time = to_local_datetime(observed_at)
        slot = slot_for_datetime(local_time)
        observation = PlayerCountObservation(
            slot=slot,
            observed_at=local_time,
            online=self._count_from_server_data(
                server_data,
                "online",
                ("online_players",),
            ),
            capacity=self._count_from_server_data(
                server_data,
                "max",
                ("capacity", "max_players"),
            ),
            server_online=self._online_state_from_server_data(server_data),
        )

        with self._lock:
            current = self._groups.get(normalized_group_id)
            if current is None or current.target_id != target_id:
                observations: list[PlayerCountObservation] = [observation]
            else:
                if current.observation_at(slot) is not None:
                    return False
                observations = [*current.observations, observation]

            updated_series = self._prune_window(
                PlayerCountWindowSeries(
                    group_id=normalized_group_id,
                    target_id=target_id,
                    observations=observations,
                )
            )
            updated_groups = dict(self._groups)
            updated_groups[normalized_group_id] = updated_series
            self._persist(updated_groups)
            self._groups = updated_groups
            return True

    def get_window(
        self,
        group_id: object,
        target: str | Sequence[object] | None = None,
    ) -> PlayerCountWindowSeries | None:
        """Return the rolling window series when group and target match."""

        normalized_group_id = self._normalize_group_id(group_id)
        target_id = self._normalize_target_id(target) if target is not None else None
        with self._lock:
            series = self._groups.get(normalized_group_id)
            if series is None:
                return None
            if target_id is not None and series.target_id != target_id:
                return None
            return series

    def get_day(
        self,
        group_id: object,
        target: str | Sequence[object] | None = None,
        day: object = None,
        *,
        now: datetime | str | None = None,
    ) -> PlayerCountWindowSeries | None:
        """Compatibility alias returning the rolling window (day/now ignored)."""

        return self.get_window(group_id, target)

    def delete_group(self, group_id: object) -> bool:
        """Atomically delete one group's statistics without touching other data."""

        normalized_group_id = self._normalize_group_id(group_id)
        with self._lock:
            if normalized_group_id not in self._groups:
                return False
            updated_groups = dict(self._groups)
            del updated_groups[normalized_group_id]
            self._persist(updated_groups)
            self._groups = updated_groups
            return True

    def retain_groups(self, group_ids: Iterable[object]) -> int:
        """Atomically remove statistics for groups no longer configured."""

        retained = {self._normalize_group_id(group_id) for group_id in group_ids}
        with self._lock:
            removed = set(self._groups) - retained
            if not removed:
                return 0
            updated_groups = {
                group_id: series
                for group_id, series in self._groups.items()
                if group_id in retained
            }
            self._persist(updated_groups)
            self._groups = updated_groups
            return len(removed)

    def retain_current_targets(
        self,
        targets: Mapping[str, str | Sequence[object]],
    ) -> int:
        """Atomically remove series that do not match configured active targets."""

        normalized_targets = {
            self._normalize_group_id(group_id): self._normalize_target_id(target)
            for group_id, target in targets.items()
        }
        with self._lock:
            updated_groups = {
                group_id: series
                for group_id, series in self._groups.items()
                if normalized_targets.get(group_id) == series.target_id
            }
            removed = len(self._groups) - len(updated_groups)
            if not removed:
                return 0
            self._persist(updated_groups)
            self._groups = updated_groups
            return removed

    def snapshot(self) -> dict[str, PlayerCountWindowSeries]:
        with self._lock:
            return dict(self._groups)

    # 为调用方和专项测试保留兼容别名。
    get_series = get_window
    clear_group = delete_group


__all__ = [
    "DEFAULT_FILENAME",
    "LEGACY_SCHEMA_VERSION",
    "PlayerStatsStore",
    "SCHEMA_VERSION",
]
