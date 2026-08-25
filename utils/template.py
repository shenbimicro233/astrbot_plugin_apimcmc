"""监控通知模板引擎。

模板层只处理规范化的服务器数据，不依赖 AstrBot，方便配置迁移、API
预览和监控通知共享完全相同的渲染规则。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


Renderer = Callable[[dict, str, str], str | None]


@dataclass(frozen=True)
class FieldSpec:
    """一个可插入模板的字段定义。"""

    key: str
    label: str
    description: str
    renderer: Renderer
    preview: str


def _is_empty(value: object, *unknown: str) -> bool:
    """判断字段是否为空或使用了约定的未知占位值。"""

    if value is None:
        return True
    if isinstance(value, str):
        normalized = value.strip()
        return not normalized or normalized in {"未知", *unknown}
    return False


def _status_emoji(data: dict, _server_type: str, _hitokoto: str) -> str:
    return "🟢" if data.get("status") == "online" else "🔴"


def _change_msg(data: dict, _server_type: str, _hitokoto: str) -> str:
    value = data.get("change_msg")
    return "" if _is_empty(value) else str(value)


def _name(data: dict, _server_type: str, _hitokoto: str) -> str:
    value = data.get("name")
    if value is None or value == "":
        return "未知服务器"
    return "" if _is_empty(value, "未知") else str(value)


def _motd(data: dict, _server_type: str, _hitokoto: str) -> str:
    value = data.get("motd")
    if _is_empty(value, "未知"):
        return ""
    text = str(value)
    if len(text) > 120:
        text = text[:120] + "..."
    return f"📝 MOTD: {text}"


def _version(data: dict, _server_type: str, _hitokoto: str) -> str:
    value = data.get("version")
    if _is_empty(value, "未知", "未知版本"):
        return ""
    return f"🎮 版本: {value}"


def _protocol(data: dict, _server_type: str, _hitokoto: str) -> str:
    value = data.get("protocol")
    return "" if _is_empty(value, "未知") or value == 0 else f"🔌 协议: {value}"


def _online(data: dict, _server_type: str, _hitokoto: str) -> str:
    value = data.get("online")
    return "" if _is_empty(value, "未知") else str(value)


def _max_players(data: dict, _server_type: str, _hitokoto: str) -> str:
    value = data.get("max")
    return "" if _is_empty(value, "未知") else str(value)


def _player_count(data: dict, _server_type: str, _hitokoto: str) -> str:
    online = data.get("online")
    maximum = data.get("max")
    if _is_empty(online, "未知") or _is_empty(maximum, "未知"):
        return ""
    return f"👥 {online}/{maximum}"


def _software(data: dict, _server_type: str, _hitokoto: str) -> str:
    value = data.get("software")
    if _is_empty(value, "未知"):
        return ""
    return f"🛠️ 软件: {value}"


def _type_label(data: dict, server_type: str, _hitokoto: str) -> str:
    return f"🔧 类型: {'基岩版' if server_type == 'bedrock' else 'Java版'}"


def _map(data: dict, _server_type: str, _hitokoto: str) -> str:
    value = data.get("map")
    if _is_empty(value, "未知"):
        return ""
    return f"🗺️ 地图: {value}"


def _update_time(data: dict, _server_type: str, _hitokoto: str) -> str:
    value = data.get("update_time")
    return "" if _is_empty(value, "未知") else f"🕒 更新时间: {value}"


def _hitokoto(_data: dict, _server_type: str, hitokoto: str) -> str:
    return "" if _is_empty(hitokoto, "未知") else f"💬 {hitokoto}"


FIELD_REGISTRY: list[FieldSpec] = [
    FieldSpec(
        "change_msg",
        "\u72b6\u6001\u53d8\u5316",
        "\u670d\u52a1\u5668\u6216\u73a9\u5bb6\u72b6\u6001\u53d8\u5316\u6d88\u606f",
        _change_msg,
        "\U0001f7e2 \u670d\u52a1\u5668\u5df2\u4e0a\u7ebf",
    ),
    FieldSpec("status_emoji", "状态图标", "在线或离线状态图标", _status_emoji, "🟢"),
    FieldSpec("name", "服务器名称", "服务器名称", _name, "play.example.com"),
    FieldSpec(
        "motd", "MOTD 描述", "服务器 MOTD（最长 120 字符）", _motd, "📝 MOTD: A Minecraft Server"
    ),
    FieldSpec("version", "游戏版本", "游戏版本", _version, "🎮 版本: Paper 1.20.1"),
    FieldSpec("protocol", "协议版本", "协议版本号", _protocol, "🔌 协议: 760"),
    FieldSpec("online", "在线人数", "当前在线人数", _online, "5"),
    FieldSpec("max", "最大人数", "服务器最大人数", _max_players, "20"),
    FieldSpec("player_count", "在线/最大人数", "在线人数与最大人数", _player_count, "👥 5/20"),
    FieldSpec("software", "服务端软件", "服务端软件名称", _software, "🛠️ 软件: Paper"),
    FieldSpec("type_label", "服务器类型", "Java 或基岩版", _type_label, "🔧 类型: 基岩版"),
    FieldSpec("map", "地图名称", "当前地图名称", _map, "🗺️ 地图: world"),
    FieldSpec(
        "update_time", "更新时间", "状态更新时间", _update_time, "🕒 更新时间: 2026-07-22 14:30"
    ),
    FieldSpec("hitokoto", "一言", "随机一言（按需请求）", _hitokoto, "💬 这是一条示例一言"),
]
FIELD_BY_KEY = {field.key: field for field in FIELD_REGISTRY}

LEGACY_CUSTOM_FIELD_ORDER = [
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
LEGACY_CUSTOM_FIELD_KEYS = frozenset((*LEGACY_CUSTOM_FIELD_ORDER, "hitokoto"))

DEFAULT_ONLINE_TEMPLATE = "\n".join(
    (
        "{status_emoji}",
        "{name}",
        "{motd}",
        "{version}",
        "{protocol}",
        "{player_count}",
        "{software}",
        "{type_label}",
        "{map}",
        "{update_time}",
    )
)
DEFAULT_OFFLINE_TEMPLATE = "🔴 {name} | 服务器已离线"

SAMPLE_SERVER_DATA = {
    "status": "online",
    "change_msg": "\U0001f7e2 \u670d\u52a1\u5668\u5df2\u4e0a\u7ebf",
    "name": "play.example.com",
    "motd": "A Minecraft Server",
    "version": "Paper 1.20.1",
    "protocol": 760,
    "online": 5,
    "max": 20,
    "software": "Paper",
    "map": "world",
    "update_time": "2026-07-22 14:30",
}
SAMPLE_OFFLINE_SERVER_DATA = {
    **SAMPLE_SERVER_DATA,
    "status": "offline",
    "change_msg": "\U0001f534 \u670d\u52a1\u5668\u5df2\u79bb\u7ebf",
    "online": 0,
    "max": 0,
}
SAMPLE_SERVER_TYPE = "bedrock"
SAMPLE_HITOKOTO = "这是一条示例一言"

_PLACEHOLDER_RE = re.compile(r"\{([^{}\r\n]+)\}")


def validate_template(template: str) -> list[str]:
    """返回模板中出现的未知占位符，按首次出现顺序去重。"""

    if not isinstance(template, str):
        raise TypeError("模板必须是字符串")
    unknown: list[str] = []
    for key in _PLACEHOLDER_RE.findall(template):
        if key not in FIELD_BY_KEY and key not in unknown:
            unknown.append(key)
    return unknown


def render_template(
    template: str,
    server_data: dict,
    server_type: str,
    hitokoto: str = "",
) -> str:
    """将模板渲染为通知文本并归一化空行。

    未知占位符理论上应在 API/配置入口被拦截；纯函数渲染时保留原文，便于
    调试和兼容旧数据，而不会静默吞掉用户文字。
    """

    if not isinstance(template, str):
        raise TypeError("模板必须是字符串")
    data = server_data or {}

    def replace(match: re.Match[str]) -> str:
        field = FIELD_BY_KEY.get(match.group(1))
        if field is None:
            return match.group(0)
        value = field.renderer(data, server_type, hitokoto)
        return "" if value is None or value == "未知" else str(value)

    rendered = _PLACEHOLDER_RE.sub(replace, template).replace("\r\n", "\n")
    rendered = "\n".join(line for line in rendered.split("\n") if line.strip())
    lines = rendered.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def template_needs_hitokoto(template: str) -> bool:
    """判断模板是否实际引用一言字段。"""

    return "hitokoto" in _PLACEHOLDER_RE.findall(template or "")


def build_template_from_fields(fields: object) -> str:
    """按旧版固定顺序将字段列表迁移为模板文本。"""

    if not isinstance(fields, (list, tuple)):
        return ""
    selected = {field for field in fields if isinstance(field, str)}
    lines = [f"{{{key}}}" for key in LEGACY_CUSTOM_FIELD_ORDER if key in selected]
    if "hitokoto" in selected:
        lines.append("{hitokoto}")
    return "\n".join(lines)
