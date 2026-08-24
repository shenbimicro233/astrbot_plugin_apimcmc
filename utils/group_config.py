"""每群监控配置的读取、迁移与原子持久化。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

try:
    from ..mcserver.template import (
        LEGACY_CUSTOM_FIELD_KEYS,
        build_template_from_fields,
    )
except (ImportError, ValueError):
    # 允许独立导入 ``utils``；直接加载纯模板模块，避免触发 mcserver 工厂的
    # 宿主依赖和跨包相对导入。
    import importlib.util
    import sys

    _template_name = "_apimcmc_template_standalone"
    _template_path = Path(__file__).resolve().parents[1] / "mcserver" / "template.py"
    _template_spec = importlib.util.spec_from_file_location(_template_name, _template_path)
    if _template_spec is None or _template_spec.loader is None:
        raise ImportError("无法加载模板模块")
    _template_module = importlib.util.module_from_spec(_template_spec)
    sys.modules[_template_name] = _template_module
    _template_spec.loader.exec_module(_template_module)
    LEGACY_CUSTOM_FIELD_KEYS = _template_module.LEGACY_CUSTOM_FIELD_KEYS
    build_template_from_fields = _template_module.build_template_from_fields

PLUGIN_NAME = "astrbot_plugin_minecraft_monitor_plus"
CONFIG_DATA_DIR = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
CONFIG_FILE = CONFIG_DATA_DIR / "configs.json"
CONFIG_SCHEMA_VERSION = 2
MESSAGE_FORMATS = {"full", "simple", "custom"}

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
    "enable_srv": True,
    "message_format": None,
    "message_template": "",
    "offline_template": "",
}


def is_srv_port_optional(server_type: str, enable_srv: bool) -> bool:
    """Java 开启 SRV 时允许省略端口。"""

    return server_type == "java" and enable_srv is True


def normalize_server_port(value: object) -> int | None:
    """规范化可选端口，坏值返回 None。"""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        port = value
    elif isinstance(value, str) and value.strip().isdecimal():
        port = int(value.strip())
    else:
        return None
    return port if 1 <= port <= 65535 else None


def _warn(message: str) -> None:
    warning = getattr(logger, "warning", None)
    if warning is not None:
        warning(message)
    else:
        logger.info(message)


def _legacy_message_config(data: dict) -> tuple[str | None, str, str]:
    """把旧 custom_mode/custom_fields/simple_mode 映射为模板配置。"""

    raw_custom = data.get("custom_mode")
    raw_fields = data.get("custom_fields")
    raw_simple = data.get("simple_mode")
    valid_fields: list[str] = []
    if isinstance(raw_fields, list):
        for field in raw_fields:
            if (
                isinstance(field, str)
                and field in LEGACY_CUSTOM_FIELD_KEYS
                and field not in valid_fields
            ):
                valid_fields.append(field)
            elif field not in valid_fields:
                _warn(f"忽略旧配置中不支持的 custom_fields 项: {field!r}")
    elif raw_fields is not None:
        _warn("忽略类型错误的旧 custom_fields")
    if raw_custom is not None and type(raw_custom) is not bool:
        _warn("忽略类型错误的旧 custom_mode")
    if raw_simple is not None and type(raw_simple) is not bool:
        _warn("忽略类型错误的旧 simple_mode")
    if raw_custom is True:
        if valid_fields:
            return "custom", build_template_from_fields(valid_fields), ""
        # 明确的旧自定义模式但没有有效字段时，保留全局继承语义。
        return None, "", ""

    if type(raw_simple) is bool:
        return ("simple" if raw_simple else "full"), "", ""
    return None, "", ""


class GroupConfig:
    """单个群的监控配置。"""

    def __init__(self, data: dict):
        if not isinstance(data, dict):
            raise TypeError("群配置必须是对象")
        self.group_id = str(data.get("group_id", ""))
        self.name = str(data.get("name", DEFAULT_GROUP_CONFIG["name"]))
        self.server_ip = str(data.get("server_ip", ""))
        self.server_port = normalize_server_port(
            data.get("server_port", DEFAULT_GROUP_CONFIG["server_port"])
        )
        self.server_type = str(data.get("server_type", DEFAULT_GROUP_CONFIG["server_type"]))
        self.enabled = data.get("enabled", True) is not False
        self.api_source = str(data.get("api_source", ""))
        self.mcmotdapi_host = str(data.get("mcmotdapi_host", "") or "")
        raw_ssl = data.get("mcmotdapi_ssl")
        self.mcmotdapi_ssl = None if raw_ssl is None else (raw_ssl is True)

        raw_enable_srv = data.get("enable_srv")
        if type(raw_enable_srv) is not bool:
            raw_enable_srv = data.get("mcmotdapi_enable_srv")
        self.enable_srv = raw_enable_srv if type(raw_enable_srv) is bool else True

        message_keys = {"message_format", "message_template", "offline_template"}
        has_new_message_config = any(key in data for key in message_keys)
        has_legacy_message_config = any(
            key in data for key in ("custom_mode", "custom_fields", "simple_mode")
        )
        if not has_new_message_config and has_legacy_message_config:
            self.message_format, self.message_template, self.offline_template = (
                _legacy_message_config(data)
            )
        else:
            raw_format = data.get("message_format")
            self.message_format = (
                raw_format
                if isinstance(raw_format, str) and raw_format in MESSAGE_FORMATS
                else None
            )
            raw_template = data.get("message_template", "")
            raw_offline = data.get("offline_template", "")
            self.message_template = raw_template if isinstance(raw_template, str) else ""
            self.offline_template = raw_offline if isinstance(raw_offline, str) else ""

        self.created_at = str(data.get("created_at", ""))

    def to_dict(self) -> dict:
        """返回 v2 可序列化配置；旧字段不会再次写入。"""

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
            "enable_srv": self.enable_srv,
            "message_format": self.message_format,
            "message_template": self.message_template,
            "offline_template": self.offline_template,
            "created_at": self.created_at,
        }

    def is_valid(self) -> bool:
        """检查群号、地址和端口/SRV 配置是否有效。"""

        port_valid = self.server_port is not None or is_srv_port_optional(
            self.server_type, self.enable_srv
        )
        return bool(self.group_id and self.server_ip and port_valid)

    def has_query_identity(self) -> bool:
        """检查解析全局策略前必须存在的群号和地址。"""

        return bool(self.group_id and self.server_ip)


class ConfigManager:
    """每群配置管理器，负责 v1 -> v2 迁移和原子写入。"""

    def __init__(self, config_file: str | Path | None = None):
        self.config_file = Path(config_file) if config_file else CONFIG_FILE
        self._groups: Dict[str, GroupConfig] = {}
        self._load()

    def _load(self) -> None:
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            if not self.config_file.exists():
                logger.info("per-group 配置文件不存在，初始化为空")
                return
            with self.config_file.open("r", encoding="utf-8") as file:
                raw = json.load(file)
            if not isinstance(raw, dict) or not isinstance(raw.get("groups", {}), dict):
                raise ValueError("per-group 配置文件格式错误")

            raw_groups = raw.get("groups", {})
            schema_version = raw.get("schema_version")
            needs_write = schema_version != CONFIG_SCHEMA_VERSION or "groups" not in raw
            loaded_groups: Dict[str, GroupConfig] = {}
            for gid, group_data in raw_groups.items():
                if not isinstance(group_data, dict):
                    _warn(f"跳过群 {gid}：配置不是对象")
                    needs_write = True
                    continue
                try:
                    source = dict(group_data)
                    source["group_id"] = str(gid)
                    cfg = GroupConfig(source)
                except Exception as exc:
                    _warn(f"跳过群 {gid}：配置无法归一化: {exc}")
                    needs_write = True
                    continue
                loaded_groups[str(gid)] = cfg
                if cfg.to_dict() != group_data:
                    needs_write = True

            self._groups = loaded_groups
            if needs_write:
                try:
                    self._save(loaded_groups)
                except Exception as exc:
                    _warn(f"配置迁移回写失败，将继续使用内存配置: {exc}")
            logger.info(f"已加载 {len(self._groups)} 个群的配置")
        except Exception as exc:
            self._groups = {}
            logger.error(f"加载 per-group 配置失败: {exc}")

    def _save(self, groups: Dict[str, GroupConfig] | None = None) -> None:
        source = self._groups if groups is None else groups
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        groups_raw = {gid: cfg.to_dict() for gid, cfg in source.items()}
        temp_file = self.config_file.with_suffix(self.config_file.suffix + ".tmp")
        try:
            with temp_file.open("w", encoding="utf-8") as file:
                json.dump(
                    {"schema_version": CONFIG_SCHEMA_VERSION, "groups": groups_raw},
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
                file.flush()
            temp_file.replace(self.config_file)
        except Exception:
            temp_file.unlink(missing_ok=True)
            raise

    def get_all(self) -> Dict[str, dict]:
        return {gid: cfg.to_dict() for gid, cfg in self._groups.items()}

    def get(self, group_id: str) -> Optional[GroupConfig]:
        return self._groups.get(str(group_id))

    def get_enabled_groups(self) -> Dict[str, GroupConfig]:
        return {
            gid: cfg
            for gid, cfg in self._groups.items()
            if cfg.enabled and cfg.has_query_identity()
        }

    def save_group(self, data: dict) -> GroupConfig:
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
        gid = str(group_id)
        if gid not in self._groups:
            return False
        updated_groups = dict(self._groups)
        del updated_groups[gid]
        self._save(updated_groups)
        self._groups = updated_groups
        return True
