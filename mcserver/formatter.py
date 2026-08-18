"""Minecraft 服务器数据格式化，将结构化数据转为可读文本。

不依赖 AstrBot API，可独立测试。
"""

from __future__ import annotations


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
    """格式化玩家在线数量（仅聚合计数，不展示玩家名单）。"""
    online = server_data.get("online", 0)
    max_players = server_data.get("max", 0)
    message = f"👥 在线玩家: {online}/{max_players}"

    if online <= 0:
        return message + "\n📭 当前无玩家在线"
    return f"{message}\n📌 当前有 {online} 名玩家在线"


def _format_extra_fields(server_data: dict) -> list[str]:
    """格式化地图、ID、服务器类型、更新时间等附加字段。"""
    parts: list[str] = []

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


# 自定义模式可选的字段名与对应渲染逻辑
FIELD_RENDERERS = {
    "status_emoji": lambda d, t: "🟢" if d.get("status") == "online" else "🔴",
    "name": lambda d, t: d.get("name", "未知服务器"),
    "version": lambda d, t: (
        f"🎮 版本: {d['version']}" if d.get("version") and d["version"] != "未知版本" else ""
    ),
    "protocol": lambda d, t: f"🔌 协议: {d['protocol']}" if d.get("protocol") else "",
    "player_count": lambda d, t: f"👥 {d.get('online', 0)}/{d.get('max', 0)}",
    "motd": lambda d, t: _format_motd(d.get("motd", "")).rstrip("\n") if d.get("motd") else "",
    "software": lambda d, t: (
        f"🛠️ 软件: {d['software']}" if d.get("software") and d["software"] != "未知" else ""
    ),
    "type_label": lambda d, t: f"🔧 类型: {'基岩版' if t == 'bedrock' else 'Java版'}",
    "map": lambda d, t: f"🗺️ 地图: {d['map']}" if d.get("map") and d["map"] != "未知" else "",
    "update_time": lambda d, t: f"🕒 更新时间: {d['update_time']}" if d.get("update_time") else "",
}

# 自定义模式的固定字段顺序（hitokoto 由调用方在函数外追加，不在此处理）
CUSTOM_FIELD_ORDER = [
    "status_emoji",
    "name",
    "motd",
    "version",
    "protocol",
    "player_count",
    "software",
    "type_label",
    "map",
    "update_time",
]


def format_server_info_custom(server_data: dict, server_type: str, fields: list) -> str:
    """根据所选字段列表自定义格式，字段顺序固定。"""
    if server_data is None:
        return "❌ 获取服务器数据失败"

    status = server_data.get("status", "offline")
    if status != "online":
        name = server_data.get("name", "未知服务器")
        return f"🔴 {name} | 服务器已离线"

    lines = []
    for field in CUSTOM_FIELD_ORDER:
        if field not in fields:
            continue
        renderer = FIELD_RENDERERS.get(field)
        if renderer:
            text = renderer(server_data, server_type)
            if text:
                lines.append(text)

    return "\n".join(lines) if lines else "❌ 未选择任何字段"


def format_server_info_simple(server_data: dict) -> str:
    """将服务器数据格式化为精简单行摘要，避免刷屏。"""
    if server_data is None:
        return "❌ 获取服务器数据失败"

    status = server_data.get("status", "offline")
    if status != "online":
        name = server_data.get("name", "未知服务器")
        return f"🔴 {name} | 服务器已离线"

    status_emoji = "🟢"
    name = server_data.get("name", "未知服务器")

    # 在线/最大玩家数
    online = server_data.get("online", 0)
    max_players = server_data.get("max", 0)

    return f"{status_emoji} {name}  👥 {online}/{max_players}"
