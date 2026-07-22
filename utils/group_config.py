"""Per-group 监控配置持久化。

不依赖 AstrBot Star 实例；仅用 logger 输出加载/保存结果。
调用方式：from utils.group_config import ConfigManager, DEFAULT_GROUP_CONFIG, CONFIG_FILE
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Dict, Optional

from astrbot.api import logger

PLUGIN_NAME = "astrbot_plugin_minecraft_monitor_plus"
CONFIG_DATA_DIR = os.path.join("data", "plugin_data", PLUGIN_NAME)
CONFIG_FILE = os.path.join(CONFIG_DATA_DIR, "configs.json")

DEFAULT_GROUP_CONFIG = {
    "group_id": "",
    "name": "Minecraft服务器",
    "server_ip": "",
    "server_port": 19132,
    "server_type": "bedrock",
    "enabled": True,
    "use_hitokoto": True,
    "api_source": "",
    "mcmotdapi_host": "",
    "mcmotdapi_ssl": None,
    "simple_mode": None,
}


class GroupConfig:
    """单个群的监控配置。"""

    def __init__(self, data: dict):
        """从字典初始化群配置，字段缺失时使用默认值。"""
        self.group_id: str = str(data.get("group_id", ""))
        self.name: str = str(data.get("name", DEFAULT_GROUP_CONFIG["name"]))
        self.server_ip: str = str(data.get("server_ip", ""))
        self.server_port: int = int(
            data.get("server_port", DEFAULT_GROUP_CONFIG["server_port"])
        )
        self.server_type: str = str(
            data.get("server_type", DEFAULT_GROUP_CONFIG["server_type"])
        )
        self.enabled: bool = bool(data.get("enabled", True))
        self.use_hitokoto: bool = bool(
            data.get("use_hitokoto", DEFAULT_GROUP_CONFIG["use_hitokoto"])
        )
        self.api_source: str = str(data.get("api_source", ""))
        self.mcmotdapi_host: str = str(data.get("mcmotdapi_host", "") or "")
        raw_ssl = data.get("mcmotdapi_ssl")
        self.mcmotdapi_ssl: bool | None = None if raw_ssl is None else bool(raw_ssl)
        raw_simple = data.get("simple_mode")
        self.simple_mode: bool | None = None if raw_simple is None else bool(raw_simple)
        self.created_at: str = str(data.get("created_at", ""))

    def to_dict(self) -> dict:
        """将配置转换为可序列化的字典。"""
        return {
            "group_id": self.group_id,
            "name": self.name,
            "server_ip": self.server_ip,
            "server_port": self.server_port,
            "server_type": self.server_type,
            "enabled": self.enabled,
            "use_hitokoto": self.use_hitokoto,
            "api_source": self.api_source,
            "mcmotdapi_host": self.mcmotdapi_host,
            "mcmotdapi_ssl": self.mcmotdapi_ssl,
            "simple_mode": self.simple_mode,
            "created_at": self.created_at,
        }

    def is_valid(self) -> bool:
        """检查配置是否包含必要的群号、IP 和端口。"""
        return bool(self.group_id and self.server_ip and self.server_port)


class ConfigManager:
    """Per-group 配置管理器，以 JSON 文件持久化存储。"""

    def __init__(self):
        """加载已存在的配置文件，不存在则初始化为空。"""
        self._groups: Dict[str, GroupConfig] = {}
        self._load()

    def _load(self) -> None:
        """从 CONFIG_FILE 读取配置并解析。"""
        try:
            os.makedirs(CONFIG_DATA_DIR, exist_ok=True)
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for gid, gdata in raw.get("groups", {}).items():
                    self._groups[gid] = GroupConfig(gdata)
                logger.info(f"已加载 {len(self._groups)} 个群的配置")
            else:
                logger.info("per-group 配置文件不存在，初始化为空")
        except Exception as e:
            logger.error(f"加载 per-group 配置失败: {e}")

    def _save(self) -> None:
        """将当前配置写入 CONFIG_FILE。"""
        try:
            os.makedirs(CONFIG_DATA_DIR, exist_ok=True)
            groups_raw = {gid: cfg.to_dict() for gid, cfg in self._groups.items()}
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"groups": groups_raw}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 per-group 配置失败: {e}")

    def get_all(self) -> Dict[str, dict]:
        """返回所有群的配置字典（以 dict 形式）。"""
        return {gid: cfg.to_dict() for gid, cfg in self._groups.items()}

    def get(self, group_id: str) -> Optional[GroupConfig]:
        """返回指定群的配置对象，不存在返回 None。"""
        return self._groups.get(group_id)

    def get_enabled_groups(self) -> Dict[str, GroupConfig]:
        """返回所有已启用且有效的群配置。"""
        return {
            gid: cfg
            for gid, cfg in self._groups.items()
            if cfg.enabled and cfg.is_valid()
        }

    def save_group(self, data: dict) -> GroupConfig:
        """新增或更新一个群的配置并持久化。"""
        gid = str(data.get("group_id", ""))
        if not gid:
            raise ValueError("group_id 不能为空")

        existing = self._groups.get(gid)
        created_at = (
            existing.created_at
            if existing
            else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

        merged = {
            **DEFAULT_GROUP_CONFIG,
            **(existing.to_dict() if existing else {}),
            **data,
            "created_at": created_at,
        }
        cfg = GroupConfig(merged)
        self._groups[gid] = cfg
        self._save()
        return cfg

    def delete_group(self, group_id: str) -> bool:
        """删除指定群的配置，存在则删除并持久化。"""
        if group_id in self._groups:
            del self._groups[group_id]
            self._save()
            return True
        return False

