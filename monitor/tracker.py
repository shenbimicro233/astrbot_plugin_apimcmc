"""Aggregate Minecraft server change tracking without player attribution."""

from __future__ import annotations


class ChangeTracker:
    """Track committed aggregate state and report a pending notification."""

    def __init__(self) -> None:
        self.last_player_count: int | None = None
        self.last_status: str | None = None

    def check_changes(self, server_data: dict) -> tuple[bool, str]:
        """Compare an observation without mutating the committed state."""

        if server_data is None:
            return False, "获取服务器数据失败"

        current_online = server_data["online"]
        current_status = server_data["status"]

        if self.last_player_count is None:
            if current_online > 0:
                return True, "服务器监控已启动，当前有玩家在线"
            return True, "服务器监控已启动"

        if self.last_status != current_status:
            if current_status == "online":
                return True, "🟢 服务器已上线"
            return True, "🔴 服务器已离线"

        player_diff = current_online - self.last_player_count
        if player_diff > 0:
            return True, "👥 有玩家上线"
        if player_diff < 0:
            return True, "👥 有玩家下线"
        return False, "无变化"

    def commit(self, server_data: dict) -> None:
        """Commit a successfully handled aggregate server observation."""

        self.last_player_count = server_data["online"]
        self.last_status = server_data["status"]

    def reset(self) -> None:
        """Discard the committed aggregate state."""

        self.last_player_count = None
        self.last_status = None
