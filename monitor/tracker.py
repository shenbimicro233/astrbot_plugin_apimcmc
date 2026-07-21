"""Minecraft 服务器状态变化检测器。

维护上次状态缓存，检测玩家数量变化和服务器在线状态变化。

不依赖 AstrBot API，可独立测试。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..mcserver.formatter import extract_player_names


class ChangeTracker:
    """状态变化检测器。

    维护 last_* 缓存，每次调用 check_changes() 对比当前数据与缓存，
    返回 (has_changed, description)。
    """

    def __init__(self):
        self.last_player_count: Optional[int] = None
        self.last_player_list: List[str] = []
        self.last_status: Optional[str] = None

    def check_changes(self, server_data: dict) -> Tuple[bool, str]:
        """检查服务器状态是否有变化。

        Returns:
            (True, 描述信息) — 有变化或首次检测
            (False, "无变化") — 无变化
        """
        if server_data is None:
            return False, "获取服务器数据失败"

        current_online = server_data["online"]
        current_players = server_data["players"]
        current_status = server_data["status"]

        current_player_names = extract_player_names(current_players)

        # 首次检测
        if self.last_player_count is None:
            self.last_player_count = current_online
            self.last_player_list = current_player_names.copy()
            self.last_status = current_status

            if current_online > 0:
                return True, "服务器监控已启动，当前有玩家在线"
            return True, "服务器监控已启动"

        changes = []

        # 服务器在线状态变化
        if self.last_status != current_status:
            if current_status == "online":
                changes.append("🟢 服务器已上线")
            else:
                changes.append("🔴 服务器已离线")

        # 玩家数量变化
        player_diff = current_online - self.last_player_count
        if player_diff > 0:
            new_players = set(current_player_names) - set(self.last_player_list)
            if new_players:
                changes.append(f"📈 {', '.join(new_players)} 加入了服务器 (+{player_diff})")
            else:
                changes.append(f"📈 有 {player_diff} 名玩家加入了服务器")
        elif player_diff < 0:
            left_players = set(self.last_player_list) - set(current_player_names)
            if left_players:
                changes.append(f"📉 {', '.join(left_players)} 离开了服务器 ({player_diff})")
            else:
                changes.append(f"📉 有 {abs(player_diff)} 名玩家离开了服务器")

        # 更新缓存
        self.last_player_count = current_online
        self.last_player_list = current_player_names.copy()
        self.last_status = current_status

        if changes:
            return True, "\n".join(changes)
        return False, "无变化"

    def reset(self):
        """重置状态缓存到初始值。"""
        self.last_player_count = None
        self.last_player_list = []
        self.last_status = None
