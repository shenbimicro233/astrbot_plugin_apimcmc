"""Pillow renderer for rolling 24-hour player-count charts."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageDraw, ImageFont

from .model import (
    PlayerCountObservation,
    PlayerCountWindowSeries,
    WINDOW_DURATION,
    WINDOW_SLOT_COUNT,
    to_local_datetime,
)

IMAGE_WIDTH = 1200
IMAGE_HEIGHT = 675
TEMP_FILE_PREFIX = "player-stats-"
STALE_AFTER = timedelta(hours=24)
LONG_OFFLINE_AFTER = timedelta(minutes=30)
WINDOW_SECONDS = int(WINDOW_DURATION.total_seconds())
CHART_PANEL_LEFT = 55
CHART_PLOT_LEFT = 108
Y_AXIS_LABEL_RIGHT_GAP = 6
Y_AXIS_LABEL_SIZE = 14
Y_AXIS_LABEL_MAX_WIDTH = CHART_PLOT_LEFT - Y_AXIS_LABEL_RIGHT_GAP - CHART_PANEL_LEFT

BUNDLED_MONOCRAFT_PATH = Path(__file__).resolve().parent / "assets" / "fonts" / "Monocraft.ttf"
MONOCRAFT_LICENSE_PATH = Path(__file__).resolve().parent / "assets" / "fonts" / "OFL-Monocraft.txt"
MONOCRAFT_SHA256 = "262e31822e0a75be567b02320bfa092143d871e8a273e529d1c5ab0c04cd215f"
MONOCRAFT_LICENSE_SHA256 = "f69c147003e052dbc9d96c40a9f73647e72766cfda95a597b94ed827fe25acb1"

BUNDLED_FUSION_PIXEL_PATH = (
    Path(__file__).resolve().parent / "assets" / "fonts" / "fusion-pixel-10px-monospaced-ja.ttf"
)
FUSION_PIXEL_LICENSE_PATH = (
    Path(__file__).resolve().parent / "assets" / "fonts" / "OFL-FusionPixel.txt"
)
FUSION_PIXEL_SHA256 = "a476f9fc45dbd2b8b292f6264dfd0aa1a5e9e4ee464c819f306c5e0ee141b227"
FUSION_PIXEL_LICENSE_SHA256 = "bc518cf64b8032c07690f33cc270c35c179255a6ac8efa7c165ebae7e8f76a63"

TEXT_STYLE_MONOCRAFT = "monocraft"
TEXT_STYLE_GALACTIC = "galactic"
TEXT_STYLE_SYSTEM = "system"
TextStyle = Literal["monocraft", "galactic", "system"]

_ENGLISH_CHART_COPY = {
    "title": "PLAYER STATISTICS",
    "edition_java": "Java Edition",
    "edition_bedrock": "Bedrock Edition",
    "edition_unknown": "Minecraft",
    "current": "CURRENT",
    "peak": "PEAK",
    "average": "AVERAGE",
    "coverage": "COVERAGE",
    "empty": "NO VALID OBSERVATIONS",
    "offline_title": "SERVER OFFLINE",
    "offline_detail": "NO PLAYER TREND WHILE OFFLINE",
    "last_observed": "LAST OBSERVED",
    "offline_legend": "OFFLINE 30+ MIN",
    "contiguous": "CONTIGUOUS",
    "missing_gap": "MISSING GAP",
}

_CHINESE_CHART_COPY = {
    "title": "玩家在线统计",
    "edition_java": "Java 版",
    "edition_bedrock": "基岩版",
    "edition_unknown": "我的世界",
    "current": "当前在线",
    "peak": "峰值",
    "average": "平均在线",
    "coverage": "观测覆盖",
    "empty": "暂无有效观测",
    "offline_title": "服务器离线",
    "offline_detail": "离线期间不显示玩家趋势",
    "last_observed": "最后观测",
    "offline_legend": "离线超过 30 分钟",
    "contiguous": "连续观测",
    "missing_gap": "缺失间隔",
}

DEFAULT_FONT_CANDIDATES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "DejaVuSans.ttf",
)

KNOWN_FONT_PATHS = (
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\msyhbd.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
    Path("/System/Library/Fonts/PingFang.ttc"),
)

LogFn = Callable[[str, str], None]


def normalize_text_style(value: object) -> TextStyle:
    """Normalize persisted chart text styles at the renderer boundary."""

    normalized = str(value).strip().lower()
    if normalized == TEXT_STYLE_GALACTIC:
        return TEXT_STYLE_GALACTIC
    if normalized == TEXT_STYLE_SYSTEM:
        return TEXT_STYLE_SYSTEM
    return TEXT_STYLE_MONOCRAFT


def translate_galactic_text(text: str) -> str:
    """Map ASCII letters to Monocraft's Standard Galactic Alphabet glyphs."""

    translated: list[str] = []
    for character in str(text):
        if "A" <= character <= "Z":
            translated.append(chr(0xEB40 + ord(character) - ord("A")))
        elif "a" <= character <= "z":
            translated.append(chr(0xEB40 + ord(character) - ord("a")))
        else:
            translated.append(character)
    return "".join(translated)


@dataclass(frozen=True)
class ChartMetrics:
    latest: int
    peak: int
    average: float | Decimal
    observed_buckets: int
    elapsed_buckets: int
    last_observed_at: datetime | None
    axis_max: int
    tick_step: int
    long_offline: bool = False
    offline_since: datetime | None = None

    @property
    def coverage(self) -> float:
        if self.elapsed_buckets <= 0:
            return 0.0
        return self.observed_buckets / self.elapsed_buckets

    @property
    def latest_online(self) -> int:
        return self.latest


def _nice_axis(maximum: int, tick_count: int = 4) -> tuple[int, int]:
    """Keep the axis ceiling exact while selecting readable integer ticks."""

    maximum = max(1, maximum)
    denominator = max(1, tick_count)
    rough_step = max(1, (maximum + denominator - 1) // denominator)
    magnitude = 10 ** (len(str(rough_step)) - 1)
    scaled_step = (rough_step + magnitude - 1) // magnitude
    if scaled_step <= 1:
        factor = 1
    elif scaled_step <= 2:
        factor = 2
    elif scaled_step <= 5:
        factor = 5
    else:
        factor = 10
    step = max(1, factor * magnitude)
    return maximum, step


def _format_axis_value(value: int) -> str:
    """Keep large integer tick labels readable inside the fixed y-axis gutter."""

    if value < 1000:
        return str(value)
    for suffix, scale in (("K", 10**3), ("M", 10**6), ("B", 10**9), ("T", 10**12)):
        if value < scale * 1000:
            tenths = (value * 10 + scale // 2) // scale
            if tenths % 10:
                return f"{tenths // 10}.{tenths % 10}{suffix}"
            return f"{tenths // 10}{suffix}"
    digits = str(value)
    mantissa = digits[0] if len(digits) == 1 or digits[1] == "0" else f"{digits[0]}.{digits[1]}"
    return f"{mantissa}e{len(digits) - 1}"


def _long_offline_since(
    observations: Sequence[PlayerCountObservation],
) -> datetime | None:
    """Return the start of a confirmed contiguous 30-minute offline run."""

    if not observations or observations[-1].server_online:
        return None

    first = observations[-1]
    current = observations[-1]
    for observation in reversed(observations[:-1]):
        if observation.server_online or current.slot != observation.slot + 1:
            break
        first = observation
        current = observation

    if observations[-1].observed_at - first.observed_at < LONG_OFFLINE_AFTER:
        return None
    return first.observed_at


def _window_observations(
    series: PlayerCountWindowSeries,
) -> tuple[PlayerCountObservation, ...]:
    """Project the exact final 24 hours even for an unpruned caller series."""

    if not series.observations:
        return ()
    cutoff = series.observations[-1].observed_at - WINDOW_DURATION
    return tuple(
        observation for observation in series.observations if observation.observed_at >= cutoff
    )[-WINDOW_SLOT_COUNT:]


def calculate_metrics(
    series: PlayerCountWindowSeries,
    now: datetime | str | None = None,
) -> ChartMetrics:
    """Project renderer metrics from the rolling-window observations."""

    if not isinstance(series, PlayerCountWindowSeries):
        raise TypeError("series must be a PlayerCountWindowSeries")
    visible = _window_observations(series)
    latest_observation = visible[-1] if visible else None
    online_values = [observation.online for observation in visible]
    maximum = max(online_values, default=0)
    axis_max, tick_step = _nice_axis(maximum)
    offline_since = _long_offline_since(visible)
    average: float | Decimal
    if online_values:
        total = sum(online_values)
        try:
            average = total / len(online_values)
        except OverflowError:
            average = Decimal(total) / Decimal(len(online_values))
    else:
        average = 0.0

    return ChartMetrics(
        latest=latest_observation.online if latest_observation else 0,
        peak=max(online_values, default=0),
        average=average,
        observed_buckets=len(visible),
        elapsed_buckets=WINDOW_SLOT_COUNT,
        last_observed_at=(latest_observation.observed_at if latest_observation else None),
        axis_max=axis_max,
        tick_step=tick_step,
        long_offline=offline_since is not None,
        offline_since=offline_since,
    )


class PlayerStatsRenderer:
    """Render one rolling 24-hour series into a Minecraft-inspired PNG."""

    width = IMAGE_WIDTH
    height = IMAGE_HEIGHT

    def __init__(
        self,
        output_dir: str | Path,
        font_candidates: Sequence[str] | str | None = None,
        *,
        fonts: Sequence[str] | str | None = None,
        text_style: object = TEXT_STYLE_MONOCRAFT,
        logger: logging.Logger | LogFn | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.text_style = normalize_text_style(text_style)
        configured = font_candidates if font_candidates is not None else fonts
        if configured is None:
            self.font_candidates = list(DEFAULT_FONT_CANDIDATES)
        elif isinstance(configured, str):
            self.font_candidates = [configured]
        elif isinstance(configured, Sequence) and not isinstance(configured, (bytes, bytearray)):
            self.font_candidates = [
                str(candidate) for candidate in configured if str(candidate).strip()
            ]
        else:
            # Persisted AstrBot configuration is user-editable. Treat a
            # malformed fallback list like an omitted value so it cannot stop
            # plugin construction before the renderer can apply its defaults.
            self.font_candidates = list(DEFAULT_FONT_CANDIDATES)
        self._logger = logger
        # Pillow's bundled stubs model FreeTypeFont separately from ImageFont;
        # the renderer intentionally accepts either implementation.
        self._font_cache: dict[tuple[int, bool], Any] = {}
        self._monocraft_cache: dict[int, Any] = {}
        self._fusion_cache: dict[int, Any] = {}
        self._bundled_assets_valid: bool | None = None
        self._fusion_assets_valid: bool | None = None

    @staticmethod
    def _font_supports_cjk(font: Any) -> bool:
        try:
            first = bytes(font.getmask("字"))
            second = bytes(font.getmask("测"))
        except (AttributeError, OSError, UnicodeError, ValueError):
            return False
        return bool(first and second and first != second)

    def has_cjk_font(self) -> bool:
        """Return whether the selected chart font has distinct CJK glyphs."""

        return self._font_supports_cjk(self._font(18))

    def has_bundled_monocraft(self) -> bool:
        """Return whether the bundled font and its required license are usable."""

        return self._monocraft_font(18) is not None

    def has_bundled_fusion_pixel(self) -> bool:
        """Return whether the bundled Fusion Pixel font and license are usable."""

        return self._fusion_pixel_font(18) is not None

    @property
    def requires_bundled_monocraft(self) -> bool:
        """Return whether the selected style normally uses bundled Monocraft."""

        return self.text_style == TEXT_STYLE_GALACTIC

    @property
    def requires_bundled_fusion_pixel(self) -> bool:
        """Return whether the selected style normally uses bundled Fusion Pixel."""

        return self.text_style == TEXT_STYLE_MONOCRAFT

    @staticmethod
    def _asset_matches(path: Path, expected_hash: str) -> bool:
        try:
            digest = sha256(path.read_bytes()).hexdigest()
        except OSError:
            return False
        return digest == expected_hash

    @staticmethod
    def _text_asset_matches(path: Path, expected_hash: str) -> bool:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return False
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        return sha256(normalized.encode("utf-8")).hexdigest() == expected_hash

    def _bundled_assets_are_valid(self) -> bool:
        cached = self._bundled_assets_valid
        if cached is not None:
            return cached
        valid = self._asset_matches(
            BUNDLED_MONOCRAFT_PATH,
            MONOCRAFT_SHA256,
        ) and self._text_asset_matches(
            MONOCRAFT_LICENSE_PATH,
            MONOCRAFT_LICENSE_SHA256,
        )
        self._bundled_assets_valid = valid
        return valid

    def _fusion_assets_are_valid(self) -> bool:
        cached = self._fusion_assets_valid
        if cached is not None:
            return cached
        valid = self._asset_matches(
            BUNDLED_FUSION_PIXEL_PATH,
            FUSION_PIXEL_SHA256,
        ) and self._text_asset_matches(
            FUSION_PIXEL_LICENSE_PATH,
            FUSION_PIXEL_LICENSE_SHA256,
        )
        self._fusion_assets_valid = valid
        return valid

    def _load_monocraft_font(self, size: int) -> Any | None:
        if not self._bundled_assets_are_valid():
            return None
        try:
            return ImageFont.truetype(str(BUNDLED_MONOCRAFT_PATH), size=size)
        except (OSError, ValueError):
            return None

    def _load_fusion_pixel_font(self, size: int) -> Any | None:
        if not self._fusion_assets_are_valid():
            return None
        try:
            return ImageFont.truetype(str(BUNDLED_FUSION_PIXEL_PATH), size=size)
        except (OSError, ValueError):
            return None

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

    def _font(self, size: int, *, bold: bool = False) -> Any:
        key = size, bold
        cached = self._font_cache.get(key)
        if cached is not None:
            return cached

        candidates: list[str] = list(self.font_candidates)
        candidates.extend(str(path) for path in KNOWN_FONT_PATHS)
        if bold:
            candidates.insert(0, r"C:\Windows\Fonts\msyhbd.ttc")
            candidates.append("DejaVuSans-Bold.ttf")
        else:
            candidates.append("DejaVuSans.ttf")

        fallback: Any = None
        for candidate in candidates:
            try:
                font = ImageFont.truetype(candidate, size=size)
                if fallback is None:
                    fallback = font
                if self._font_supports_cjk(font):
                    self._font_cache[key] = font
                    return font
            except (OSError, ValueError):
                continue

        if fallback is None:
            try:
                fallback = ImageFont.load_default(size=size)
            except TypeError:
                fallback = ImageFont.load_default()
        font = fallback
        self._font_cache[key] = font
        return font

    def _monocraft_font(self, size: int) -> Any | None:
        cached = self._monocraft_cache.get(size)
        if cached is not None:
            return cached
        font = self._load_monocraft_font(size)
        if font is not None:
            self._monocraft_cache[size] = font
        return font

    def _fusion_pixel_font(self, size: int) -> Any | None:
        cached = self._fusion_cache.get(size)
        if cached is not None:
            return cached
        font = self._load_fusion_pixel_font(size)
        if font is not None:
            self._fusion_cache[size] = font
        return font

    def _prepare_text(
        self,
        text: str,
        size: int,
        *,
        bold: bool,
        role: str,
    ) -> tuple[str, Any]:
        value = str(text)
        if self.text_style == TEXT_STYLE_MONOCRAFT:
            fusion_font = self._fusion_pixel_font(size)
            if fusion_font is not None:
                return value, fusion_font
            return value, self._font(size, bold=bold)

        if self.text_style == TEXT_STYLE_SYSTEM:
            return value, self._font(size, bold=bold)

        font = self._monocraft_font(size)
        if font is None:
            return value, self._font(size, bold=bold)
        return translate_galactic_text(value), font

    def _chart_copy(self, key: str) -> str:
        """Return mode-owned fixed chart copy without mixing font families."""

        if self.text_style == TEXT_STYLE_MONOCRAFT:
            return _CHINESE_CHART_COPY[key]
        return _ENGLISH_CHART_COPY[key]

    def _font_for_text(
        self,
        text: str,
        size: int,
        *,
        bold: bool,
        role: str,
    ) -> Any:
        return self._prepare_text(
            text,
            size,
            bold=bold,
            role=role,
        )[1]

    @staticmethod
    def _ellipsize(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: Any,
        max_width: int,
    ) -> str:
        value = str(text)
        if draw.textlength(value, font=font) <= max_width:
            return value
        suffix = "..."
        if draw.textlength(suffix, font=font) > max_width:
            return ""
        low, high = 0, len(value)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = value[:middle] + suffix
            if draw.textlength(candidate, font=font) <= max_width:
                low = middle
            else:
                high = middle - 1
        return value[:low] + suffix

    @staticmethod
    def _draw_pixel_texture(draw: ImageDraw.ImageDraw) -> None:
        draw.rectangle((0, 0, IMAGE_WIDTH, IMAGE_HEIGHT), fill="#6d482d")
        tile = 24
        dirt_palette = ("#765038", "#68432e", "#855b3d", "#593924")
        for y in range(0, IMAGE_HEIGHT, tile):
            for x in range(0, IMAGE_WIDTH, tile):
                color = dirt_palette[((x // tile) * 7 + (y // tile) * 11) % len(dirt_palette)]
                draw.rectangle((x, y, x + tile - 1, y + tile - 1), fill=color)
                if ((x // tile) + (y // tile)) % 3 == 0:
                    draw.rectangle((x + 5, y + 8, x + 11, y + 13), fill="#4d311f")

        draw.rectangle((0, 0, IMAGE_WIDTH, 34), fill="#4b8f38")
        grass_palette = ("#67a842", "#4e9135", "#76b64a", "#3f7e31")
        for x in range(0, IMAGE_WIDTH, 20):
            color = grass_palette[(x // 20) % len(grass_palette)]
            draw.rectangle((x, 4, x + 19, 25), fill=color)
            draw.polygon(
                ((x, 25), (x + 8, 25), (x + 13, 34), (x + 20, 34), (x + 20, 25)),
                fill="#3b742d",
            )

    def _draw_text(
        self,
        draw: ImageDraw.ImageDraw,
        position: tuple[int, int],
        text: str,
        *,
        size: int,
        fill: str,
        bold: bool = False,
        max_width: int | None = None,
        anchor: str | None = None,
        role: str = "label",
    ) -> None:
        value, font = self._prepare_text(
            text,
            size,
            bold=bold,
            role=role,
        )
        value = self._ellipsize(draw, value, font, max_width) if max_width else value
        draw.text(position, value, font=font, fill=fill, anchor=anchor)

    def _draw_card_value(
        self,
        draw: ImageDraw.ImageDraw,
        right_x: int,
        center_y: int,
        text: str,
        *,
        size: int,
        fill: str,
        max_width: int,
    ) -> None:
        """Right-align a value whose glyph bounding box is centered at center_y."""

        value, font = self._prepare_text(
            text,
            size,
            bold=True,
            role="data",
        )
        value = self._ellipsize(draw, value, font, max_width)
        box = draw.textbbox((0, 0), value, font=font, anchor="ls")
        anchor_y = int(round(center_y - (box[1] + box[3]) / 2))
        draw.text((right_x, anchor_y), value, font=font, fill=fill, anchor="rs")

    @staticmethod
    def _point_for_observation(
        observation: PlayerCountObservation,
        *,
        left: int,
        top: int,
        right: int,
        bottom: int,
        axis_max: int,
        window_start: datetime,
    ) -> tuple[int, int]:
        elapsed = (observation.observed_at - window_start).total_seconds()
        fraction = min(1.0, max(0.0, elapsed / WINDOW_SECONDS))
        x = left + int(fraction * (right - left))
        y = bottom - (observation.online * (bottom - top) // axis_max)
        return x, y

    def _draw_chart(
        self,
        draw: ImageDraw.ImageDraw,
        series: PlayerCountWindowSeries,
        metrics: ChartMetrics,
        now: datetime,
    ) -> None:
        left, top, right, bottom = CHART_PLOT_LEFT, 260, 1138, 540
        draw.rounded_rectangle(
            (CHART_PANEL_LEFT, 232, 1168, 600),
            radius=6,
            fill="#151b19",
            outline="#99ad9e",
            width=2,
        )

        if metrics.last_observed_at is not None:
            window_end = metrics.last_observed_at
            window_start = window_end - WINDOW_DURATION
        else:
            window_end = to_local_datetime(now)
            window_start = window_end - WINDOW_DURATION

        for index in range(9):
            tick_time = window_start + timedelta(seconds=int(WINDOW_SECONDS * index / 8))
            x = left + int((index / 8) * (right - left))
            draw.line((x, top, x, bottom), fill="#36423d", width=1)
            self._draw_text(
                draw,
                (x, bottom + 16),
                tick_time.strftime("%H:%M"),
                size=17,
                fill="#c4cec9",
                anchor="ma",
                role="data",
            )

        tick_values = list(range(0, metrics.axis_max + 1, metrics.tick_step))
        if tick_values[-1] != metrics.axis_max:
            tick_values.append(metrics.axis_max)
        for value in tick_values:
            y = bottom - (value * (bottom - top) // metrics.axis_max)
            draw.line((left, y, right, y), fill="#3b4843", width=1)
            self._draw_text(
                draw,
                (left - Y_AXIS_LABEL_RIGHT_GAP, y),
                _format_axis_value(value),
                size=Y_AXIS_LABEL_SIZE,
                fill="#d5ddd9",
                anchor="rm",
                max_width=Y_AXIS_LABEL_MAX_WIDTH,
                role="data",
            )

        draw.line((left, top, left, bottom), fill="#d5ddd9", width=2)
        draw.line((left, bottom, right, bottom), fill="#d5ddd9", width=2)

        visible = _window_observations(series)
        if metrics.long_offline:
            draw.rounded_rectangle(
                (CHART_PANEL_LEFT, 232, 1168, 600),
                radius=6,
                fill=(184, 36, 36, 118),
                outline=(244, 108, 108, 210),
                width=2,
            )
            self._draw_text(
                draw,
                ((left + right) // 2, (top + bottom) // 2 - 14),
                self._chart_copy("offline_title"),
                size=34,
                fill="#fff0f0",
                anchor="mm",
            )
            self._draw_text(
                draw,
                ((left + right) // 2, (top + bottom) // 2 + 28),
                self._chart_copy("offline_detail"),
                size=18,
                fill="#ffd5d5",
                anchor="mm",
            )
            return

        previous: PlayerCountObservation | None = None
        previous_point: tuple[int, int] | None = None
        for observation in visible:
            point = self._point_for_observation(
                observation,
                left=left,
                top=top,
                right=right,
                bottom=bottom,
                axis_max=metrics.axis_max,
                window_start=window_start,
            )
            if (
                previous is not None
                and previous_point is not None
                and observation.slot == previous.slot + 1
            ):
                draw.line((*previous_point, *point), fill="#72d572", width=4)
            draw.ellipse(
                (point[0] - 5, point[1] - 5, point[0] + 5, point[1] + 5),
                fill="#9bed80",
                outline="#153e20",
                width=2,
            )
            previous = observation
            previous_point = point

        if not visible:
            self._draw_text(
                draw,
                ((left + right) // 2, (top + bottom) // 2),
                self._chart_copy("empty"),
                size=26,
                fill="#9aa69f",
                anchor="mm",
            )

    def render(
        self,
        series: PlayerCountWindowSeries,
        server_name: str | Mapping[str, object] = "Minecraft服务器",
        edition: str = "",
        now: datetime | str | None = None,
        *,
        server_type: str | None = None,
    ) -> Path:
        """Render a chart and return its generated path.

        ``server_name`` remains accepted for callers from v1.1.0, but is
        intentionally ignored so the generated image never identifies a
        configured server.
        """

        if not isinstance(series, PlayerCountWindowSeries):
            raise TypeError("series must be a PlayerCountWindowSeries")
        if isinstance(server_name, Mapping):
            metadata = server_name
            display_edition = str(metadata.get("edition", metadata.get("server_type", edition)))
        else:
            display_edition = str(server_type if server_type is not None else edition)

        local_now = to_local_datetime(now)
        metrics = calculate_metrics(series, local_now)
        self.cleanup_stale(now=local_now)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / f"{TEMP_FILE_PREFIX}{uuid.uuid4().hex}.png"

        image = Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), "#6d482d")
        draw = ImageDraw.Draw(image, "RGBA")
        self._draw_pixel_texture(draw)
        draw.rounded_rectangle(
            (32, 45, 1168, 645),
            radius=6,
            fill="#202724",
            outline="#d6ead5",
            width=3,
        )

        edition_label = {
            "java": self._chart_copy("edition_java"),
            "bedrock": self._chart_copy("edition_bedrock"),
        }.get(display_edition.lower(), self._chart_copy("edition_unknown"))
        self._draw_text(
            draw,
            (58, 70),
            self._chart_copy("title"),
            size=34,
            fill="#f1f5f2",
            bold=True,
            max_width=620,
        )
        self._draw_text(
            draw,
            (1142, 70),
            (
                f"{metrics.last_observed_at - WINDOW_DURATION:%m-%d %H:%M}"
                f" - {metrics.last_observed_at:%m-%d %H:%M}"
                if metrics.last_observed_at is not None
                else local_now.strftime("%m-%d %H:%M")
            ),
            size=16,
            fill="#c7d8cf",
            anchor="ra",
            max_width=430,
            role="data",
        )
        self._draw_text(
            draw,
            (1142, 101),
            edition_label,
            size=16,
            fill="#aebbb4",
            anchor="ra",
            max_width=220,
        )
        cards = (
            (self._chart_copy("current"), str(metrics.latest)),
            (self._chart_copy("peak"), str(metrics.peak)),
            (self._chart_copy("average"), f"{metrics.average:.1f}"),
            (self._chart_copy("coverage"), f"{metrics.observed_buckets}/{metrics.elapsed_buckets}"),
        )
        card_left, card_top, card_width, gap = 58, 128, 256, 22
        for index, (label, value) in enumerate(cards):
            x = card_left + index * (card_width + gap)
            draw.rounded_rectangle(
                (x, card_top, x + card_width, card_top + 76),
                radius=5,
                fill="#2d3732",
                outline="#53665b",
                width=2,
            )
            # Two-row layout: label sits near the top and the value is
            # vertically centered in the remaining card area using the font's
            # bounding box so digits no longer look bottom-heavy.
            label_top = card_top + 8
            label_value, label_font = self._prepare_text(
                label,
                18,
                bold=False,
                role="label",
            )
            label_box = draw.textbbox((0, 0), label_value, font=label_font, anchor="la")
            label_height = label_box[3] - label_box[1]
            self._draw_text(draw, (x + 18, label_top), label, size=18, fill="#aebbb4")
            value_center = int((label_top + label_height + card_top + 76) // 2)
            self._draw_card_value(
                draw,
                x + card_width - 18,
                value_center,
                value,
                size=34,
                fill="#8ce67f",
                max_width=card_width - 36,
            )

        self._draw_chart(draw, series, metrics, local_now)
        last_time = (
            metrics.last_observed_at.strftime("%H:%M:%S")
            if metrics.last_observed_at is not None
            else "--:--:--"
        )
        self._draw_text(
            draw,
            (62, 608),
            self._chart_copy("last_observed"),
            size=15,
            fill="#cbd4cf",
        )
        self._draw_text(
            draw,
            (210, 608),
            last_time,
            size=15,
            fill="#cbd4cf",
            role="data",
        )
        if metrics.long_offline:
            draw.rounded_rectangle(
                (937, 608, 961, 628),
                radius=2,
                fill=(184, 36, 36, 150),
                outline="#f46c6c",
                width=1,
            )
            self._draw_text(
                draw,
                (972, 608),
                self._chart_copy("offline_legend"),
                size=15,
                fill="#ffd5d5",
            )
        else:
            draw.line((806, 618, 838, 618), fill="#72d572", width=4)
            self._draw_text(
                draw,
                (847, 608),
                self._chart_copy("contiguous"),
                size=15,
                fill="#cbd4cf",
            )
            draw.line((974, 618, 1006, 618), fill="#78847e", width=2)
            self._draw_text(
                draw,
                (1015, 608),
                self._chart_copy("missing_gap"),
                size=15,
                fill="#cbd4cf",
            )

        try:
            image.save(output_path, format="PNG", optimize=True)
        except Exception:
            output_path.unlink(missing_ok=True)
            raise
        return output_path

    def cleanup_stale(
        self,
        *,
        now: datetime | str | None = None,
        max_age: timedelta = STALE_AFTER,
    ) -> int:
        """Remove only stale generated chart files from the scoped directory."""

        if not self.output_dir.exists():
            return 0
        cutoff = to_local_datetime(now).timestamp() - max_age.total_seconds()
        removed = 0
        for path in self.output_dir.glob(f"{TEMP_FILE_PREFIX}*.png"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError as exc:
                self._log("warn", f"清理过期玩家统计图片失败: {exc}")
        return removed

    def cleanup_all(self) -> int:
        """Remove generated charts during plugin termination."""

        if not self.output_dir.exists():
            return 0
        removed = 0
        for path in self.output_dir.glob(f"{TEMP_FILE_PREFIX}*.png"):
            try:
                if path.is_file():
                    path.unlink()
                    removed += 1
            except OSError as exc:
                self._log("warn", f"清理玩家统计图片失败: {exc}")
        return removed

    cleanup = cleanup_stale


PlayerCountRenderer = PlayerStatsRenderer

__all__ = [
    "BUNDLED_FUSION_PIXEL_PATH",
    "BUNDLED_MONOCRAFT_PATH",
    "CHART_PANEL_LEFT",
    "CHART_PLOT_LEFT",
    "ChartMetrics",
    "DEFAULT_FONT_CANDIDATES",
    "FUSION_PIXEL_LICENSE_PATH",
    "FUSION_PIXEL_LICENSE_SHA256",
    "FUSION_PIXEL_SHA256",
    "IMAGE_HEIGHT",
    "IMAGE_WIDTH",
    "LONG_OFFLINE_AFTER",
    "MONOCRAFT_LICENSE_PATH",
    "MONOCRAFT_LICENSE_SHA256",
    "MONOCRAFT_SHA256",
    "PlayerCountRenderer",
    "PlayerStatsRenderer",
    "STALE_AFTER",
    "TEMP_FILE_PREFIX",
    "TEXT_STYLE_GALACTIC",
    "TEXT_STYLE_MONOCRAFT",
    "TEXT_STYLE_SYSTEM",
    "TextStyle",
    "WINDOW_SECONDS",
    "Y_AXIS_LABEL_MAX_WIDTH",
    "Y_AXIS_LABEL_SIZE",
    "calculate_metrics",
    "normalize_text_style",
    "translate_galactic_text",
]
