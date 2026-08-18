"""提供随插件分发的 GNU Unifont 资源元数据、维护校验和运行时加载。"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from PIL import ImageFont


_FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

# 资源文件名与官方发行包保持一致，避免把地区变体误当作标准字体。
BUNDLED_UNIFONT_PATH = _FONT_DIR / "unifont-17.0.05.otf"
UNIFONT_FONT_PATH = BUNDLED_UNIFONT_PATH
UNIFONT_LICENSE_PATH = _FONT_DIR / "OFL-1.1.txt"
UNIFONT_OFL_LICENSE_PATH = UNIFONT_LICENSE_PATH
UNIFONT_GPL_LICENSE_PATH = _FONT_DIR / "LICENSE.txt"
UNIFONT_SOURCE_URL = (
    "https://unifoundry.com/pub/unifont/unifont-17.0.05/font-builds/unifont-17.0.05.otf"
)
UNIFONT_DISTRIBUTION_URL = "https://unifoundry.com/pub/"
UNIFONT_VERSION = "17.0.05"

# 发布文件的 SHA-256 已固定；许可证哈希使用换行归一化文本。
UNIFONT_SHA256 = "85701ab9b1e251ee16f4df00b13f22eac311d72b7dab427a7d975fe7f5064702"
UNIFONT_LICENSE_SHA256 = "b7e8b9d8656d5216a61f48c7e62704b8aec50e8af8ca410b28fb04ea32e0f220"
UNIFONT_OFL_LICENSE_SHA256 = UNIFONT_LICENSE_SHA256
UNIFONT_GPL_LICENSE_SHA256 = "1e74cb82bf476843e97c2596297b04219b1a7e51f7238944a8c031cb9401fa87"


def asset_matches(path: Path, expected_hash: str) -> bool:
    """检查二进制资源是否与记录的 SHA-256 完全一致。"""

    if not expected_hash or expected_hash.startswith("__"):
        return False
    try:
        digest = sha256(path.read_bytes()).hexdigest()
    except OSError:
        return False
    return digest == expected_hash.lower()


def text_asset_matches(path: Path, expected_hash: str) -> bool:
    """按平台无关换行符计算许可证文本哈希。"""

    if not expected_hash or expected_hash.startswith("__"):
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    return sha256(normalized.encode("utf-8")).hexdigest() == expected_hash.lower()


def unifont_assets_are_valid(
    *,
    font_path: Path | None = None,
    font_hash: str | None = None,
    license_path: Path | None = None,
    license_hash: str | None = None,
    gpl_license_path: Path | None = None,
    gpl_license_hash: str | None = None,
) -> bool:
    """供构建或维护工具验证字体、OFL 和 GPL 字体嵌入例外文本。"""

    font_path = BUNDLED_UNIFONT_PATH if font_path is None else font_path
    font_hash = UNIFONT_SHA256 if font_hash is None else font_hash
    license_path = UNIFONT_LICENSE_PATH if license_path is None else license_path
    license_hash = UNIFONT_LICENSE_SHA256 if license_hash is None else license_hash
    gpl_license_path = UNIFONT_GPL_LICENSE_PATH if gpl_license_path is None else gpl_license_path
    gpl_license_hash = UNIFONT_GPL_LICENSE_SHA256 if gpl_license_hash is None else gpl_license_hash
    return (
        asset_matches(font_path, font_hash)
        and text_asset_matches(license_path, license_hash)
        and text_asset_matches(gpl_license_path, gpl_license_hash)
    )


def load_unifont_font(
    size: int,
    *,
    font_path: Path | None = None,
    font_hash: str | None = None,
    license_path: Path | None = None,
    license_hash: str | None = None,
    gpl_license_path: Path | None = None,
    gpl_license_hash: str | None = None,
) -> Any | None:
    """加载随包 Unifont；字体缺失或 Pillow 无法解析时返回 ``None``。

    固定版本和许可证哈希由构建/发布校验负责，不能作为运行时安全边界。
    保留哈希参数是为了兼容旧调用方和测试替身。
    """

    del font_hash, license_path, license_hash, gpl_license_path, gpl_license_hash
    font_path = BUNDLED_UNIFONT_PATH if font_path is None else font_path
    try:
        return ImageFont.truetype(str(font_path), size=size)
    except (OSError, ValueError):
        return None


# 旧版本插件可能从这些名字导入资源检查器。保留入口语义，实际资源已切换为 Unifont。
BUNDLED_FUSION_PIXEL_PATH = BUNDLED_UNIFONT_PATH
FUSION_PIXEL_LICENSE_PATH = UNIFONT_LICENSE_PATH
FUSION_PIXEL_SHA256 = UNIFONT_SHA256
FUSION_PIXEL_LICENSE_SHA256 = UNIFONT_LICENSE_SHA256


def fusion_pixel_assets_are_valid(**kwargs: Any) -> bool:
    """兼容旧调用方；校验目标已改为 Unifont。"""

    return unifont_assets_are_valid(**kwargs)


def load_fusion_pixel_font(size: int, **kwargs: Any) -> Any | None:
    """兼容旧调用方；加载目标已改为 Unifont。"""

    return load_unifont_font(size, **kwargs)


__all__ = [
    "BUNDLED_UNIFONT_PATH",
    "UNIFONT_GPL_LICENSE_PATH",
    "UNIFONT_GPL_LICENSE_SHA256",
    "UNIFONT_FONT_PATH",
    "UNIFONT_LICENSE_PATH",
    "UNIFONT_LICENSE_SHA256",
    "UNIFONT_OFL_LICENSE_PATH",
    "UNIFONT_OFL_LICENSE_SHA256",
    "UNIFONT_SHA256",
    "UNIFONT_SOURCE_URL",
    "UNIFONT_DISTRIBUTION_URL",
    "UNIFONT_VERSION",
    "asset_matches",
    "text_asset_matches",
    "unifont_assets_are_valid",
    "load_unifont_font",
    # 兼容旧配置/外部导入。
    "BUNDLED_FUSION_PIXEL_PATH",
    "FUSION_PIXEL_LICENSE_PATH",
    "FUSION_PIXEL_LICENSE_SHA256",
    "FUSION_PIXEL_SHA256",
    "fusion_pixel_assets_are_valid",
    "load_fusion_pixel_font",
]
