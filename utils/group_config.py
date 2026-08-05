"""Per-group 监控配置持久化。

不依赖 AstrBot Star 实例；仅用 logger 输出加载/保存结果。
调用方式：from utils.group_config import ConfigManager, DEFAULT_GROUP_CONFIG, CONFIG_FILE
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

PLUGIN_NAME = "astrbot_plugin_minecraft_monitor_plus"
CONFIG_DATA_DIR = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
CONFIG_FILE = CONFIG_DATA_DIR / "configs.json"

DEFAULT_GROUP_CONFIG = {
    "group_id": "",
    "name": "Minecraft服务器",
    "server_ip": "",
    "server_port": 19132,
    "server_type": "bedrock",
    "enabled": True,
    "api_source": "",
    "mcmotdapi_host": "",
    "mcmotdapi_ssl": None,
    "simple_mode": None,
    "custom_mode": None,
    "custom_fields": None,
}


class GroupConfig:
    """单个群的监控配置。"""

    def __init__(self, data: dict):
        """从字典初始化群配置，字段缺失时使用默认值。"""
        self.group_id: str = str(data.get("group_id", ""))
        self.name: str = str(data.get("name", DEFAULT_GROUP_CONFIG["name"]))
        self.server_ip: str = str(data.get("server_ip", ""))
        self.server_port: int = int(data.get("server_port", DEFAULT_GROUP_CONFIG["server_port"]))
        self.server_type: str = str(data.get("server_type", DEFAULT_GROUP_CONFIG["server_type"]))
        self.enabled: bool = bool(data.get("enabled", True))
        self.api_source: str = str(data.get("api_source", ""))
        self.mcmotdapi_host: str = str(data.get("mcmotdapi_host", "") or "")
        raw_ssl = data.get("mcmotdapi_ssl")
        self.mcmotdapi_ssl: bool | None = None if raw_ssl is None else bool(raw_ssl)
        raw_simple = data.get("simple_mode")
        self.simple_mode: bool | None = None if raw_simple is None else bool(raw_simple)
        raw_custom = data.get("custom_mode")
        self.custom_mode: bool | None = None if raw_custom is None else bool(raw_custom)
        raw_fields = data.get("custom_fields")
        self.custom_fields: list | None = raw_fields if isinstance(raw_fields, list) else None
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
            "api_source": self.api_source,
            "mcmotdapi_host": self.mcmotdapi_host,
            "mcmotdapi_ssl": self.mcmotdapi_ssl,
            "simple_mode": self.simple_mode,
            "custom_mode": self.custom_mode,
            "custom_fields": self.custom_fields,
            "created_at": self.created_at,
        }

    def is_valid(self) -> bool:
        """检查配置是否包含必要的群号、IP 和端口。"""
        return bool(self.group_id and self.server_ip and self.server_port)


class ConfigManager:
    """Per-group 配置管理器，以 JSON 文件持久化存储。"""

    def __init__(self, config_file: str | Path | None = None):
        """加载已存在的配置文件，不存在则初始化为空。"""
        self.config_file = Path(config_file) if config_file else CONFIG_FILE
        self._groups: Dict[str, GroupConfig] = {}
        self._load()

    def _load(self) -> None:
        """从 CONFIG_FILE 读取配置并解析。"""
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            if self.config_file.exists():
                with self.config_file.open("r", encoding="utf-8") as f:
                    raw = json.load(f)
                if not isinstance(raw, dict) or not isinstance(raw.get("groups", {}), dict):
                    raise ValueError("per-group 配置文件格式错误")
                loaded_groups: Dict[str, GroupConfig] = {}
                for gid, gdata in raw.get("groups", {}).items():
                    if not isinstance(gdata, dict):
                        raise ValueError(f"群 {gid} 的配置格式错误")
                    loaded_groups[str(gid)] = GroupConfig(gdata)
                self._groups = loaded_groups
                logger.info(f"已加载 {len(self._groups)} 个群的配置")
            else:
                logger.info("per-group 配置文件不存在，初始化为空")
        except Exception as e:
            logger.error(f"加载 per-group 配置失败: {e}")

    def _save(self, groups: Dict[str, GroupConfig] | None = None) -> None:
        """将当前配置写入 CONFIG_FILE。"""
        source = self._groups if groups is None else groups
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        groups_raw = {gid: cfg.to_dict() for gid, cfg in source.items()}
        temp_file = self.config_file.with_suffix(self.config_file.suffix + ".tmp")
        try:
            with temp_file.open("w", encoding="utf-8") as f:
                json.dump({"groups": groups_raw}, f, ensure_ascii=False, indent=2)
                f.flush()
            temp_file.replace(self.config_file)
        except Exception:
            temp_file.unlink(missing_ok=True)
            raise

    def get_all(self) -> Dict[str, dict]:
        """返回所有群的配置字典（以 dict 形式）。"""
        return {gid: cfg.to_dict() for gid, cfg in self._groups.items()}

    def get(self, group_id: str) -> Optional[GroupConfig]:
        """返回指定群的配置对象，不存在返回 None。"""
        return self._groups.get(group_id)

    def get_enabled_groups(self) -> Dict[str, GroupConfig]:
        """返回所有已启用且有效的群配置。"""
        return {gid: cfg for gid, cfg in self._groups.items() if cfg.enabled and cfg.is_valid()}

    def save_group(self, data: dict) -> GroupConfig:
        """新增或更新一个群的配置并持久化。"""
        gid = str(data.get("group_id", ""))
        if not gid:
            raise ValueError("group_id 不能为空")

        existing = self._groups.get(gid)
        created_at = (
            existing.created_at if existing else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

        merged = {
            **DEFAULT_GROUP_CONFIG,
            **(existing.to_dict() if existing else {}),
            **data,
            "created_at": created_at,
        }
        cfg = GroupConfig(merged)
        updated_groups = dict(self._groups)
        updated_groups[gid] = cfg
        self._save(updated_groups)
        self._groups = updated_groups
        return cfg

    def delete_group(self, group_id: str) -> bool:
        """删除指定群的配置，存在则删除并持久化。"""
        if group_id in self._groups:
            updated_groups = dict(self._groups)
            del updated_groups[group_id]
            self._save(updated_groups)
            self._groups = updated_groups
            return True
        return False
