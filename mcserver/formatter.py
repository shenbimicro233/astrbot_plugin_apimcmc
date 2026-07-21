"""Minecraft 服务器数据格式化，将结构化数据转为可读文本。

不依赖 AstrBot API，可独立测试。
"""

from __future__ import annotations

from typing import List


def extract_player_names(player_list: list) -> List[str]:
    """从玩家列表中提取玩家名称列表。"""
    if not player_list or not isinstance(player_list, list):
        return []

    names = []
    for player in player_list:
        if isinstance(player, dict):
            name = (
                player.get("name_clean")
                or player.get("name")
                or player.get("username")
                or "未知玩家"
            )
            names.append(str(name))
        else:
            names.append(str(player))
    return names


def _format_motd(motd: str) -> str:
    """格式化 MOTD 字段，超过长度则截断。"""
    if not motd:
        return ""
    if len(motd) > 120:
        motd = motd[:120] + "..."
    return f"📝 MOTD: {motd}\n"


def _format_optional_field(label: str, value: str) -> str:
    """格式化可选字段，值为空或'未知'时不返回。"""
    if not value or value == "未知":
        return ""
    return f"{label}: {value}\n"


def _format_player_section(server_data: dict) -> str:
    """格式化玩家在线数量与列表。"""
    online = server_data.get("online", 0)
    max_players = server_data.get("max", 0)
    message = f"👥 在线玩家: {online}/{max_players}"

    if online <= 0:
        return message + "\n📋 当前无玩家在线"

    player_names = extract_player_names(server_data.get("players", []))
    if not player_names:
        return f"{message}\n📋 当前有 {online} 名玩家在线"

    display_count = min(8, len(player_names))
    display_names = player_names[:display_count]
    message += f"\n📋 玩家列表: {', '.join(display_names)}"
    if len(player_names) > display_count:
        message += f" (+{len(player_names) - display_count}人)"
    return message


def _format_extra_fields(server_data: dict) -> str:
    """格式化地图、ID、服务器类型、更新时间等附加字段。"""
    parts = []

    server_map = server_data.get("map")
    if server_map and server_map != "未知":
        parts.append(f"🗺️ 地图: {server_map}")

    server_id = server_data.get("id")
    if server_id and server_id != "未知":
        short_id = server_id[:12] + "..." if len(server_id) > 12 else server_id
        parts.append(f"🆔 ID: {short_id}")

    return parts


def format_server_info(server_data: dict, server_type: str = "bedrock") -> str:
    """将服务器结构化数据格式化为可读文本消息。"""
    if server_data is None:
        return "❌ 获取服务器数据失败"

    status = server_data.get("status", "offline")
    status_emoji = "🟢" if status == "online" else "🔴"
    name = server_data.get("name", "未知服务器")
    message = f"{status_emoji} 服务器: {name}\n"

    message += _format_motd(server_data.get("motd", ""))
    message += f"🎮 版本: {server_data.get('version', '未知')}\n"

    protocol = server_data.get("protocol")
    message += _format_optional_field("🔌 协议", protocol if protocol else "")

    software = server_data.get("software")
    message += _format_optional_field("🛠️ 软件", software if software else "")

    message += _format_player_section(server_data)

    for part in _format_extra_fields(server_data):
        message += f"\n{part}"

    type_label = "基岩版" if server_type == "bedrock" else "Java版"
    message += f"\n🔧 类型: {type_label}"

    update_time = server_data.get("update_time", "未知")
    message += f"\n🕒 更新时间: {update_time}"

    return message
