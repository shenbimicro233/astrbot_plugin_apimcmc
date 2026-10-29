"""Domain contracts for fixed 15-minute player-count observations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal, TypeAlias

BUCKET_MINUTES: Final = 15
BUCKETS_PER_HOUR: Final = 60 // BUCKET_MINUTES
BUCKET_COUNT: Final = 24 * BUCKETS_PER_HOUR
SLOT_SECONDS: Final = BUCKET_MINUTES * 60
WINDOW_DURATION: Final = timedelta(hours=24)
WINDOW_SLOT_COUNT: Final = int(WINDOW_DURATION.total_seconds() // SLOT_SECONDS)

LegacyTargetSignature: TypeAlias = tuple[str, int | None, str, str, str, bool | None]
SrvEnabledTargetSignature: TypeAlias = tuple[
    str,
    int | None,
    str,
    str,
    str,
    bool | None,
    Literal[True],
]
TargetSignature: TypeAlias = LegacyTargetSignature | SrvEnabledTargetSignature


def to_local_datetime(value: datetime | str | None = None) -> datetime:
    """Normalize a timestamp to the host's local timezone."""

    if value is None:
        value = datetime.now().astimezone()
    elif isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        if not normalized:
            raise ValueError("observed_at cannot be empty")
        try:
            value = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("observed_at must be an ISO-8601 timestamp") from exc
    elif not isinstance(value, datetime):
        raise TypeError("observed_at must be a datetime or ISO-8601 string")

    # 未指定目标时区时，astimezone() 使用主机规则，包括历史夏令时偏移。
    return value.astimezone()


def bucket_index_for(value: datetime | str | None = None) -> int:
    """Return the local wall-clock bucket index in the range 0..95."""

    local_value = to_local_datetime(value)
    return local_value.hour * BUCKETS_PER_HOUR + local_value.minute // BUCKET_MINUTES


def slot_for_datetime(value: datetime | str | None = None) -> int:
    """Return the absolute 15-minute slot containing a timestamp.

    Slots are derived from UTC epoch seconds, so they are independent of local
    midnight and of daylight-saving wall-clock transitions.
    """

    local_value = to_local_datetime(value)
    return int(local_value.timestamp() // SLOT_SECONDS)


def bucket_label(bucket: int) -> str:
    """Return the HH:MM label at the start of a bucket."""

    normalized = _validate_bucket(bucket)
    minutes = normalized * BUCKET_MINUTES
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _validate_bucket(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("bucket must be an integer")
    if not 0 <= value < BUCKET_COUNT:
        raise ValueError(f"bucket must be between 0 and {BUCKET_COUNT - 1}")
    return value


def _validate_slot(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("slot must be an integer")
    if value < 0:
        raise ValueError("slot must be a non-negative integer")
    return value


def normalize_count(value: object, field_name: str) -> int:
    """Normalize a non-negative count while rejecting booleans."""

    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be a non-negative integer")
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, float) and value.is_integer():
        normalized = int(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped or not stripped.isdecimal():
            raise ValueError(f"{field_name} must be a non-negative integer")
        normalized = int(stripped)
    else:
        raise TypeError(f"{field_name} must be a non-negative integer")

    if normalized < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return normalized


def normalize_target_signature(signature: Sequence[object]) -> TargetSignature:
    """Validate and normalize the monitor's effective target signature."""

    if isinstance(signature, (str, bytes)) or not isinstance(signature, Sequence):
        raise TypeError("target signature must be a sequence")
    if len(signature) not in (6, 7):
        raise ValueError("target signature must contain six or seven values")

    server_ip_value = signature[0]
    server_type_value = signature[2]
    api_source_value = signature[3]
    api_host_value = signature[4]
    if not (
        isinstance(server_ip_value, str)
        and isinstance(server_type_value, str)
        and isinstance(api_source_value, str)
        and isinstance(api_host_value, str)
    ):
        raise TypeError("target signature text fields must be strings")

    server_ip = server_ip_value.strip()
    server_type = server_type_value.strip()
    api_source = api_source_value.strip()
    api_host = api_host_value.strip()
    if not server_ip or not server_type or not api_source:
        raise ValueError("target signature contains an empty required value")

    ssl = signature[5]
    if ssl is not None and not isinstance(ssl, bool):
        raise TypeError("mcmotdapi_ssl must be a boolean or null")

    if len(signature) == 6:
        raw_port = signature[1]
        if raw_port is None:
            if server_type != "java" or api_source != "mcstatus":
                raise ValueError("six-value null-port signatures require mcstatus Java")
            port = None
        else:
            port = normalize_count(raw_port, "server_port")
            if not 1 <= port <= 65535:
                raise ValueError("server_port must be between 1 and 65535")
        return server_ip, port, server_type, api_source, api_host, ssl

    srv_enabled = signature[6]
    if type(srv_enabled) is not bool:
        raise TypeError("enable_srv must be a boolean")
    if not srv_enabled:
        raise ValueError("seven-value target signatures require enable_srv=true")
    if api_source not in {"mcmotdapi", "local"} or server_type != "java":
        raise ValueError("SRV-enabled target signatures require mcmotdapi/local Java")
    raw_port = signature[1]
    if raw_port is None:
        port = None
    else:
        port = normalize_count(raw_port, "server_port")
        if not 1 <= port <= 65535:
            raise ValueError("server_port must be between 1 and 65535")
    return server_ip, port, server_type, api_source, api_host, ssl, True


def target_id_for_signature(signature: Sequence[object]) -> str:
    """Hash a canonical monitor target signature for bounded persistence."""

    normalized = normalize_target_signature(signature)
    encoded = json.dumps(
        list(normalized),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, init=False)
class PlayerCountObservation:
    """One successful player-count observation for an absolute 15-minute slot."""

    slot: int
    observed_at: datetime
    online: int
    capacity: int
    server_online: bool

    def __init__(
        self,
        slot: object,
        observed_at: object,
        online: object,
        capacity: object,
        server_online: object = True,
    ) -> None:
        if not isinstance(observed_at, (datetime, str)):
            raise TypeError("observed_at must be a datetime or ISO-8601 string")
        if not isinstance(server_online, bool):
            raise TypeError("server_online must be a boolean")
        normalized_slot = _validate_slot(slot)
        normalized_time = to_local_datetime(observed_at)
        if slot_for_datetime(normalized_time) != normalized_slot:
            raise ValueError("observation timestamp does not match slot")
        object.__setattr__(self, "slot", normalized_slot)
        object.__setattr__(self, "observed_at", normalized_time)
        object.__setattr__(self, "online", normalize_count(online, "online"))
        object.__setattr__(self, "capacity", normalize_count(capacity, "capacity"))
        object.__setattr__(self, "server_online", server_online)

    @property
    def bucket(self) -> int:
        """Legacy local wall-clock bucket containing this observation."""

        return bucket_index_for(self.observed_at)

    @property
    def observed_at_iso(self) -> str:
        return self.observed_at.isoformat()

    def to_dict(self, *, include_bucket: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "observed_at": self.observed_at_iso,
            "online": self.online,
            "capacity": self.capacity,
            "server_online": self.server_online,
        }
        if include_bucket:
            payload["bucket"] = self.bucket
        return payload

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[object, object],
        *,
        slot: int | None = None,
        bucket: int | None = None,
    ) -> PlayerCountObservation:
        if not isinstance(payload, Mapping):
            raise TypeError("observation must be an object")
        if slot is None and bucket is None:
            raise ValueError("observation slot is required")
        if "observed_at" not in payload:
            raise ValueError("observation observed_at is required")
        if "online" not in payload:
            raise ValueError("observation online count is required")
        if "capacity" not in payload and "max" not in payload:
            raise ValueError("observation capacity count is required")
        capacity = payload["capacity"] if "capacity" in payload else payload["max"]

        raw_observed_at = payload["observed_at"]
        if not isinstance(raw_observed_at, (datetime, str)):
            raise TypeError("observed_at must be a datetime or ISO-8601 string")
        observed_at = to_local_datetime(raw_observed_at)
        if slot is not None:
            normalized_slot = _validate_slot(slot)
        else:
            legacy_bucket = _validate_bucket(bucket)
            if bucket_index_for(observed_at) != legacy_bucket:
                raise ValueError("observation timestamp does not match bucket")
            normalized_slot = slot_for_datetime(observed_at)
        return cls(
            slot=normalized_slot,
            observed_at=observed_at,
            online=payload["online"],
            capacity=capacity,
            server_online=payload.get("server_online", True),
        )


@dataclass(frozen=True, init=False)
class PlayerCountWindowSeries:
    """Typed, ordered observations for one group and target in a rolling window."""

    group_id: str
    target_id: str
    observations: tuple[PlayerCountObservation, ...]

    def __init__(
        self,
        group_id: object,
        target_id: object,
        observations: Iterable[PlayerCountObservation] | Mapping[object, PlayerCountObservation],
    ) -> None:
        normalized_group_id = str(group_id).strip()
        normalized_target_id = str(target_id).strip()
        if not normalized_group_id:
            raise ValueError("group_id cannot be empty")
        if not normalized_target_id:
            raise ValueError("target_id cannot be empty")

        source = observations.values() if isinstance(observations, Mapping) else observations
        by_slot: dict[int, PlayerCountObservation] = {}
        for observation in source:
            if not isinstance(observation, PlayerCountObservation):
                raise TypeError("observations must contain PlayerCountObservation values")
            if observation.slot in by_slot:
                raise ValueError(f"duplicate observation slot: {observation.slot}")
            by_slot[observation.slot] = observation

        ordered = tuple(by_slot[index] for index in sorted(by_slot))
        object.__setattr__(self, "group_id", normalized_group_id)
        object.__setattr__(self, "target_id", normalized_target_id)
        object.__setattr__(self, "observations", ordered)

    @property
    def slots(self) -> dict[int, PlayerCountObservation]:
        return {observation.slot: observation for observation in self.observations}

    @property
    def window_start(self) -> datetime | None:
        """Start of the rolling window, ending at the latest observation."""

        if not self.observations:
            return None
        return self.observations[-1].observed_at - WINDOW_DURATION

    @property
    def last_observed_at(self) -> datetime | None:
        if not self.observations:
            return None
        return self.observations[-1].observed_at

    def observation_at(self, slot: int) -> PlayerCountObservation | None:
        return self.slots.get(_validate_slot(slot))

    def __len__(self) -> int:
        return len(self.observations)

    def __bool__(self) -> bool:
        return bool(self.observations)


# 为偏好较短名称或兼容旧领域名称的调用方保留稳定别名。
PlayerCountDaySeries = PlayerCountWindowSeries
DaySeries = PlayerCountWindowSeries
PlayerStatsDay = PlayerCountWindowSeries
bucket_for_datetime = bucket_index_for
get_bucket_index = bucket_index_for
canonical_target_id = target_id_for_signature
hash_target_signature = target_id_for_signature


__all__ = [
    "BUCKET_COUNT",
    "BUCKET_MINUTES",
    "BUCKETS_PER_HOUR",
    "DaySeries",
    "PlayerCountDaySeries",
    "PlayerCountObservation",
    "PlayerCountWindowSeries",
    "PlayerStatsDay",
    "SLOT_SECONDS",
    "TargetSignature",
    "WINDOW_DURATION",
    "WINDOW_SLOT_COUNT",
    "bucket_for_datetime",
    "bucket_index_for",
    "bucket_label",
    "canonical_target_id",
    "get_bucket_index",
    "hash_target_signature",
    "normalize_count",
    "normalize_target_signature",
    "slot_for_datetime",
    "target_id_for_signature",
    "to_local_datetime",
]
