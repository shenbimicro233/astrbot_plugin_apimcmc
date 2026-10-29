"""服务器状态卡的固定尺寸 Pillow 渲染器。"""

from __future__ import annotations

import base64
import logging
import threading
import uuid
import warnings
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

try:
    from ..player_stats.font_resources import load_unifont_font
except ImportError:
    # 支持从插件根目录直接运行的测试环境。
    from player_stats.font_resources import load_unifont_font
from .model import CardStatus, ServerEdition, StatusCard

# 兼容仍从旧模块级名字打桩的调用方；实际加载目标为 Unifont。
load_fusion_pixel_font = load_unifont_font


IMAGE_WIDTH = 960
IMAGE_HEIGHT = 800
TEMP_FILE_PREFIX = "server-status-"
STALE_AFTER = timedelta(hours=24)
MAX_ICON_ENCODED_LENGTH = 512 * 1024
MAX_ICON_BYTES = 384 * 1024
MAX_ICON_DIMENSION = 512
MAX_ICON_PIXELS = MAX_ICON_DIMENSION * MAX_ICON_DIMENSION
ICON_SIZE = 128

STATUS_PRESENTATION: dict[CardStatus, tuple[str, str]] = {
    CardStatus.ONLINE: ("在线", "#8be878"),
    CardStatus.OFFLINE: ("离线", "#f0b762"),
    CardStatus.UNREACHABLE: ("不可达", "#f37a7a"),
}
EDITION_DISPLAY_NAMES = {
    ServerEdition.JAVA: "Java",
    ServerEdition.BEDROCK: "Bedrock",
}
EDITION_CARD_TITLES = {
    ServerEdition.JAVA: "[MCJE服务器信息]",
    ServerEdition.BEDROCK: "[MCBE服务器信息]",
}
CARD_FIELD_LABELS = {
    "address": "测试地址: ",
    "edition": "类型: ",
    "software": "服务端名: ",
    "protocol": "协议版本: ",
    "players": "当前人数: ",
    "latency": "测试延迟: ",
}

_DEFAULT_FONT_CANDIDATES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "DejaVuSans.ttf",
)
_KNOWN_FONT_PATHS = (
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
    Path("/System/Library/Fonts/PingFang.ttc"),
)

LogFn = Callable[[str, str], None]


class RendererClosedError(RuntimeError):
    """插件终止且渲染器停止后抛出。"""


class ServerStatusRenderer:
    """将不可变 ``StatusCard`` 渲染为隔离的临时 PNG。"""

    width = IMAGE_WIDTH
    height = IMAGE_HEIGHT

    def __init__(
        self,
        output_dir: str | Path,
        font_candidates: Sequence[str] | str | None = None,
        *,
        logger: logging.Logger | LogFn | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        if font_candidates is None:
            self.font_candidates = list(_DEFAULT_FONT_CANDIDATES)
        elif isinstance(font_candidates, str):
            self.font_candidates = [font_candidates]
        elif isinstance(font_candidates, Sequence) and not isinstance(
            font_candidates, (bytes, bytearray)
        ):
            self.font_candidates = [
                str(candidate) for candidate in font_candidates if str(candidate).strip()
            ]
        else:
            self.font_candidates = list(_DEFAULT_FONT_CANDIDATES)
        self._logger = logger
        self._font_cache: dict[tuple[int, bool], Any] = {}
        self._unifont_cache: dict[int, Any] = {}
        self._lifecycle_lock = threading.RLock()
        self._owned_paths: set[Path] = set()
        self._closed = False

    @staticmethod
    def _font_supports_cjk(font: Any) -> bool:
        try:
            first = bytes(font.getmask("字"))
            second = bytes(font.getmask("测"))
        except (AttributeError, OSError, UnicodeError, ValueError):
            return False
        return bool(first and second and first != second)

    def _log(self, level: str, message: str) -> None:
        if callable(self._logger) and not isinstance(self._logger, logging.Logger):
            self._logger(level.upper(), message)
            return
        target = (
            self._logger
            if isinstance(self._logger, logging.Logger)
            else logging.getLogger(__name__)
        )
        method = "warning" if level.lower() == "warn" else level.lower()
        getattr(target, method, target.info)(message)

    def _fallback_font(self, size: int, *, bold: bool = False) -> Any:
        key = size, bold
        cached = self._font_cache.get(key)
        if cached is not None:
            return cached

        candidates = list(self.font_candidates)
        candidates.extend(str(path) for path in _KNOWN_FONT_PATHS)
        if bold:
            candidates.insert(0, r"C:\Windows\Fonts\msyhbd.ttc")
            candidates.append("DejaVuSans-Bold.ttf")
        else:
            candidates.append("DejaVuSans.ttf")

        fallback: Any = None
        for candidate in candidates:
            try:
                font = ImageFont.truetype(candidate, size=size)
            except (OSError, ValueError):
                continue
            if fallback is None:
                fallback = font
            if self._font_supports_cjk(font):
                self._font_cache[key] = font
                return font

        if fallback is None:
            try:
                fallback = ImageFont.load_default(size=size)
            except TypeError:
                fallback = ImageFont.load_default()
        self._font_cache[key] = fallback
        return fallback

    def _unifont_font(self, size: int) -> Any | None:
        cached = self._unifont_cache.get(size)
        if cached is not None:
            return cached
        font = load_unifont_font(size)
        if font is not None:
            self._unifont_cache[size] = font
        return font

    def _font(self, size: int, *, bold: bool = False) -> Any:
        font = self._unifont_font(size)
        if font is not None:
            return font
        return self._fallback_font(size, bold=bold)

    def has_bundled_unifont(self) -> bool:
        """返回内置 Unifont 是否可用。"""

        return self._unifont_font(18) is not None

    def has_bundled_fusion_pixel(self) -> bool:
        """兼容旧接口；返回 Unifont 是否可用。"""

        return self.has_bundled_unifont()

    @staticmethod
    def ellipsize(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: Any,
        max_width: int,
        *,
        stroke_width: int = 0,
    ) -> str:
        """按渲染像素截断动态文本，且不改变布局。"""

        # Pillow 的 textlength 不计算描边，两侧各预留 stroke_width 像素。
        extra = 2 * stroke_width
        value = str(text)
        if draw.textlength(value, font=font) + extra <= max_width:
            return value
        suffix = "..."
        if draw.textlength(suffix, font=font) + extra > max_width:
            return ""
        low, high = 0, len(value)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = value[:middle] + suffix
            if draw.textlength(candidate, font=font) + extra <= max_width:
                low = middle
            else:
                high = middle - 1
        return value[:low] + suffix

    def _draw_text(
        self,
        draw: ImageDraw.ImageDraw,
        position: tuple[int, int],
        text: str,
        *,
        size: int,
        fill: str,
        max_width: int,
        anchor: str = "la",
        bold: bool = False,
        stroke_width: int = 0,
        stroke_fill: str | None = None,
    ) -> None:
        font = self._font(size, bold=bold)
        value = self.ellipsize(draw, text, font, max_width, stroke_width=stroke_width)
        draw.text(
            position,
            value,
            font=font,
            fill=fill,
            anchor=anchor,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill if stroke_width else None,
        )

    @staticmethod
    def _decode_icon(value: object) -> Image.Image | None:
        if not isinstance(value, str) or not value or len(value) > MAX_ICON_ENCODED_LENGTH:
            return None

        encoded = value
        if value.lower().startswith("data:"):
            header, separator, encoded = value.partition(",")
            if separator != "," or header.lower() != "data:image/png;base64":
                return None
        if len(encoded) > MAX_ICON_ENCODED_LENGTH:
            return None

        try:
            decoded = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeEncodeError, ValueError):
            return None
        if not decoded or len(decoded) > MAX_ICON_BYTES:
            return None

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(decoded)) as inspected:
                    if inspected.format != "PNG":
                        return None
                    width, height = inspected.size
                    if (
                        width <= 0
                        or height <= 0
                        or width > MAX_ICON_DIMENSION
                        or height > MAX_ICON_DIMENSION
                        or width * height > MAX_ICON_PIXELS
                    ):
                        return None
                    inspected.verify()
                with Image.open(BytesIO(decoded)) as source:
                    source.load()
                    icon = source.convert("RGBA")
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            OSError,
            SyntaxError,
            ValueError,
        ):
            return None

        icon.thumbnail((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)
        return icon

    @staticmethod
    def _draw_grass(draw: ImageDraw.ImageDraw) -> None:
        grass = ("#426f39", "#50883f", "#345d31", "#609947")
        soil = ("#4d4037", "#5c4b3e", "#3f352f")
        for x in range(0, IMAGE_WIDTH, 24):
            color = grass[(x // 24) % len(grass)]
            draw.rectangle((x, 0, x + 23, 17), fill=color)
            draw.rectangle((x, IMAGE_HEIGHT - 18, x + 23, IMAGE_HEIGHT - 1), fill=color)
            draw.rectangle((x + 5, 14, x + 16, 23), fill=soil[(x // 24) % len(soil)])
            draw.rectangle(
                (x + 5, IMAGE_HEIGHT - 24, x + 16, IMAGE_HEIGHT - 15),
                fill=soil[(x // 24) % len(soil)],
            )

    def _draw_placeholder(self, image: Image.Image, edition: ServerEdition) -> None:
        draw = ImageDraw.Draw(image, "RGBA")
        inset = max(10, ICON_SIZE // 8)
        tile = (0, 0, ICON_SIZE - 1, ICON_SIZE - 1)
        draw.rectangle(tile, fill="#252c28", outline="#a4b7a7", width=3)
        draw.rectangle(
            (inset, inset + 1, ICON_SIZE - inset - 1, ICON_SIZE - inset - 1),
            fill="#56615a",
            outline="#27342c",
            width=3,
        )
        draw.rectangle(
            (inset + 7, inset + 8, ICON_SIZE - inset - 8, ICON_SIZE - inset - 8),
            fill="#6f7d72",
        )
        step = max(10, ICON_SIZE // 8)
        for y in range(inset + 11, ICON_SIZE - inset - 8, step):
            for x in range(inset + 11, ICON_SIZE - inset - 8, step):
                if (x + y) // step % 2 == 0:
                    draw.rectangle((x, y, x + 6, y + 6), fill="#88968a")
        label = "JE" if edition is ServerEdition.JAVA else "BE"
        font = self._font(ICON_SIZE // 5, bold=True)
        draw.text(
            (ICON_SIZE // 2, ICON_SIZE // 2 + 2),
            label,
            font=font,
            fill="#f0f4ef",
            anchor="mm",
            stroke_width=1,
            stroke_fill="#f0f4ef",
        )

    def _draw_icon(self, image: Image.Image, card: StatusCard) -> None:
        icon = self._decode_icon(card.icon) if card.edition is ServerEdition.JAVA else None
        if icon is None:
            self._draw_placeholder(image, card.edition)
            return
        offset = ((ICON_SIZE - icon.width) // 2, (ICON_SIZE - icon.height) // 2)
        image.alpha_composite(icon, dest=offset)

    @staticmethod
    def _visible_status(status: CardStatus) -> tuple[str, str]:
        if status is CardStatus.ONLINE:
            return STATUS_PRESENTATION[CardStatus.ONLINE]
        if status is CardStatus.OFFLINE:
            return STATUS_PRESENTATION[CardStatus.OFFLINE]
        return STATUS_PRESENTATION[CardStatus.UNREACHABLE]

    @staticmethod
    def _visible_edition(edition: ServerEdition) -> str:
        display_edition = (
            ServerEdition.JAVA if edition is ServerEdition.JAVA else ServerEdition.BEDROCK
        )
        return EDITION_DISPLAY_NAMES[display_edition]

    @staticmethod
    def _visible_players(card: StatusCard) -> str:
        if card.online is None or card.maximum is None:
            return "--"
        return f"{card.online} / {card.maximum}"

    @staticmethod
    def _visible_latency(card: StatusCard) -> str:
        return f"{card.latency_ms} ms" if card.latency_ms is not None else "--"

    @staticmethod
    def _latency_color(card: StatusCard) -> str:
        if card.latency_ms is None:
            return "#FFFFFF"
        if card.latency_ms <= 50:
            return "#55FF55"
        if card.latency_ms <= 100:
            return "#FFFF55"
        if card.latency_ms <= 200:
            return "#FFAA00"
        return "#FF5555"

    def render(self, card: StatusCard) -> Path:
        """将允许的八个字段渲染为唯一的本地 PNG。"""

        if not isinstance(card, StatusCard):
            raise TypeError("card must be a StatusCard")

        with self._lifecycle_lock:
            if self._closed:
                raise RendererClosedError("renderer is closed")
            return self._render_unlocked(card)

    def _render_unlocked(self, card: StatusCard) -> Path:
        """持有 ``cleanup_all()`` 使用的生命周期锁时执行渲染。"""

        self._cleanup_stale_unlocked()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        identifier = uuid.uuid4().hex
        output_path = self.output_dir / f"{TEMP_FILE_PREFIX}{identifier}.png"
        temporary_path = self.output_dir / f".{TEMP_FILE_PREFIX}{identifier}.tmp"

        image = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), "#303532")
        draw = ImageDraw.Draw(image, "RGBA")
        self._draw_grass(draw)
        draw.rectangle(
            (24, 28, IMAGE_WIDTH - 25, IMAGE_HEIGHT - 29),
            fill="#1d2320",
            outline="#b9c4bc",
            width=3,
        )

        # 文本与图标布局参照 8kateam/astrbot_plugin_picmcmotd：
        # 左上角大图标 + 右上两行大标题，下方为「灰色标签: 白色值」信息行，
        # Minecraft 风格深色描边；背景仍为本插件程序绘制的草地与石墨边框。
        margin = 40
        header_height = ICON_SIZE
        title_size = 56
        extra_size = 48
        title_stroke = 4
        extra_stroke = 3
        white = "#FFFFFF"
        label_gray = "#AAAAAA"
        stroke_dark = "#3F3F3F"

        icon_canvas = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), "#252c28")
        self._draw_icon(icon_canvas, card)
        image.paste(icon_canvas, (margin, margin), icon_canvas)
        draw.rectangle(
            (margin - 1, margin - 1, margin + ICON_SIZE, margin + ICON_SIZE),
            outline="#a9b8ac",
            width=2,
        )

        title_x = margin + ICON_SIZE + 16
        title_max_width = IMAGE_WIDTH - margin - title_x
        title_edition = (
            ServerEdition.JAVA if card.edition is ServerEdition.JAVA else ServerEdition.BEDROCK
        )
        header1 = EDITION_CARD_TITLES[title_edition]
        status_text, status_color = self._visible_status(card.status)
        self._draw_text(
            draw,
            (title_x, 72),
            header1,
            size=title_size,
            fill=white,
            max_width=title_max_width,
            anchor="lm",
            bold=True,
            stroke_width=title_stroke,
            stroke_fill=stroke_dark,
        )
        self._draw_text(
            draw,
            (title_x, 136),
            status_text,
            size=title_size,
            fill=status_color,
            max_width=title_max_width,
            anchor="lm",
            bold=True,
            stroke_width=title_stroke,
            stroke_fill=stroke_dark,
        )

        rows = (
            (CARD_FIELD_LABELS["address"], card.address, white),
            (CARD_FIELD_LABELS["edition"], self._visible_edition(card.edition), white),
            (CARD_FIELD_LABELS["software"], card.software or "--", white),
            (CARD_FIELD_LABELS["protocol"], card.protocol or "--", white),
            (CARD_FIELD_LABELS["players"], self._visible_players(card), white),
            (
                CARD_FIELD_LABELS["latency"],
                self._visible_latency(card),
                self._latency_color(card),
            ),
        )
        label_font = self._font(extra_size)
        label_max_width = int(max(draw.textlength(label, font=label_font) for label, _, _ in rows))
        label_x = margin
        value_x = margin + label_max_width + 16
        value_max_width = IMAGE_WIDTH - margin - value_x
        row_top = margin + header_height + 16
        row_height = 96
        for index, (label, value, fill) in enumerate(rows):
            center_y = row_top + index * row_height + row_height // 2
            self._draw_text(
                draw,
                (label_x, center_y),
                label,
                size=extra_size,
                fill=label_gray,
                max_width=value_max_width,
                anchor="lm",
                bold=True,
                stroke_width=extra_stroke,
                stroke_fill=stroke_dark,
            )
            self._draw_text(
                draw,
                (value_x, center_y),
                value,
                size=extra_size,
                fill=fill,
                max_width=value_max_width,
                anchor="lm",
                bold=True,
                stroke_width=extra_stroke,
                stroke_fill=stroke_dark,
            )

        try:
            image.save(temporary_path, format="PNG", optimize=True)
            temporary_path.replace(output_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            raise
        self._owned_paths.add(output_path)
        return output_path

    def _matching_paths(self) -> tuple[Path, ...]:
        if not self.output_dir.exists():
            return ()
        return tuple(
            path
            for pattern in (f"{TEMP_FILE_PREFIX}*.png", f".{TEMP_FILE_PREFIX}*.tmp")
            for path in self.output_dir.glob(pattern)
        )

    def _cleanup_stale_unlocked(
        self,
        *,
        now: datetime | None = None,
        max_age: timedelta = STALE_AFTER,
    ) -> int:
        """持有生命周期锁时删除过期的匹配文件。"""

        cutoff = (now or datetime.now()).timestamp() - max_age.total_seconds()
        removed = 0
        for path in self._matching_paths():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    self._owned_paths.discard(path)
                    removed += 1
            except OSError as exc:
                self._log("warn", f"清理过期查询图片失败: {type(exc).__name__}")
        return removed

    def cleanup_stale(
        self,
        *,
        now: datetime | None = None,
        max_age: timedelta = STALE_AFTER,
    ) -> int:
        """仅删除此渲染器目录中过期的服务器状态图片。"""

        with self._lifecycle_lock:
            return self._cleanup_stale_unlocked(now=now, max_age=max_age)

    def cleanup_all(self) -> int:
        """插件终止时仅删除此渲染器生成的文件。"""

        with self._lifecycle_lock:
            self._closed = True
            removed = 0
            for path in tuple(self._owned_paths):
                try:
                    if path.is_file():
                        path.unlink()
                        removed += 1
                    self._owned_paths.discard(path)
                except OSError as exc:
                    self._log("warn", f"清理查询图片失败: {type(exc).__name__}")
            return removed


__all__ = [
    "CARD_FIELD_LABELS",
    "EDITION_CARD_TITLES",
    "EDITION_DISPLAY_NAMES",
    "ICON_SIZE",
    "IMAGE_HEIGHT",
    "IMAGE_WIDTH",
    "MAX_ICON_BYTES",
    "MAX_ICON_DIMENSION",
    "MAX_ICON_ENCODED_LENGTH",
    "MAX_ICON_PIXELS",
    "RendererClosedError",
    "STALE_AFTER",
    "STATUS_PRESENTATION",
    "TEMP_FILE_PREFIX",
    "ServerStatusRenderer",
]
