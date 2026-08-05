"""Player-count statistics domain, persistence, and image rendering."""

from typing import TYPE_CHECKING

from .model import (
    BUCKET_COUNT,
    BUCKET_MINUTES,
    DaySeries,
    PlayerCountDaySeries,
    PlayerCountObservation,
    PlayerCountWindowSeries,
    SLOT_SECONDS,
    TargetSignature,
    WINDOW_DURATION,
    WINDOW_SLOT_COUNT,
    bucket_index_for,
    bucket_label,
    slot_for_datetime,
    target_id_for_signature,
)
from .store import PlayerStatsStore

if TYPE_CHECKING:
    from .renderer import PlayerStatsRenderer


def __getattr__(name: str):
    if name == "PlayerStatsRenderer":
        from .renderer import PlayerStatsRenderer

        return PlayerStatsRenderer
    raise AttributeError(name)


__all__ = [
    "BUCKET_COUNT",
    "BUCKET_MINUTES",
    "DaySeries",
    "PlayerCountDaySeries",
    "PlayerCountObservation",
    "PlayerCountWindowSeries",
    "PlayerStatsRenderer",
    "PlayerStatsStore",
    "SLOT_SECONDS",
    "TargetSignature",
    "WINDOW_DURATION",
    "WINDOW_SLOT_COUNT",
    "bucket_index_for",
    "bucket_label",
    "slot_for_datetime",
    "target_id_for_signature",
]
