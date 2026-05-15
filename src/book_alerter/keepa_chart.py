"""Extract numeric price observations from a Keepa price-history PNG.

Keepa publishes a free, no-auth PNG endpoint at `graph.keepa.com/pricehistory.png`
that renders a price-history chart for any Amazon ASIN. The same data is sold
behind a €19/mo API; this module reads the visual chart and reconstructs
~85-95% of the data as `(date, series, price)` tuples — good enough to seed
the signal engine's percentile distribution without paying.

Pipeline:

  1. Load PNG with PIL.
  2. OCR the y-axis labels (with pytesseract) → linear (pixel_y → price)
     calibration.
  3. OCR the x-axis month labels → (pixel_x → date) calibration.
  4. For each known series color (Amazon orange, marketplace new, marketplace
     used), walk every column inside the plot rectangle and find the y of
     the topmost matching pixel. Map (x, y) to (date, price).
  5. Compact runs to one observation per day per series.

The PNG layout is stable across Keepa's userbase — they generate it on a
single render pipeline — so margins / fonts / colors don't vary by ASIN.
A future change to Keepa's chart styling would break this; the extractor
prints a clear warning and returns an empty list rather than emitting
bogus data.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Literal

import numpy as np
from PIL import Image

from book_alerter.logging_setup import get_logger

log = get_logger(__name__)


SeriesName = Literal["amazon", "new", "used"]


@dataclass(frozen=True)
class ExtractedObservation:
    """One (date, series, price) tuple recovered from the chart."""

    observed_at: date
    series: SeriesName
    # Pence. Reconstruction error is roughly +/- £1 due to anti-aliasing and
    # axis-tick rounding; callers should not treat this as exact.
    price_minor: int


# Target RGB for each series line. Sampled from a real Keepa chart;
# tolerance below catches anti-aliased neighbours.
_SERIES_COLORS: dict[SeriesName, tuple[int, int, int]] = {
    "amazon": (255, 165, 0),
    "new": (136, 136, 221),
    "used": (68, 68, 68),
}
_COLOR_TOLERANCE = 30  # max per-channel delta from the exact target


# Chart layout constants. The Keepa endpoint always returns a 1200x400 PNG
# when those are the requested width/height (or whatever was requested if
# different). These margins are sampled by hand from a 1200x400 sample.
# Other widths/heights will scale roughly proportionally; the extractor is
# written for 1200x400 — callers must request that geometry.
_EXPECTED_W = 1200
_EXPECTED_H = 400
_PLOT_LEFT = 50
# Right edge: the data area ends ~col 1101 on a 1200-wide chart; the legend
# (with its own coloured dots that would pollute the trace) starts ~col 1108.
# Stay well clear of it.
_PLOT_RIGHT = 1102
_PLOT_TOP = 20
_PLOT_BOTTOM = 380


def extract_observations(png_bytes: bytes) -> list[ExtractedObservation]:
    """Return one observation per (day, series) from a Keepa-rendered PNG.

    Returns an empty list if the chart can't be parsed (unexpected geometry,
    OCR failure, no recognised series lines). Never raises on bad input —
    the caller treats an empty result as "no historical data available".
    """
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    except Exception as exc:
        log.warning("keepa_chart.load.error", error=str(exc))
        return []
    if img.size != (_EXPECTED_W, _EXPECTED_H):
        log.warning(
            "keepa_chart.unexpected_geometry",
            got=img.size,
            expected=(_EXPECTED_W, _EXPECTED_H),
        )
        return []
    arr = np.asarray(img)  # shape: (H, W, 3), dtype uint8

    y_calib = _calibrate_y_axis(arr)
    x_calib = _calibrate_x_axis(arr)
    if y_calib is None or x_calib is None:
        log.warning("keepa_chart.calibration.failed",
                    y_ok=y_calib is not None, x_ok=x_calib is not None)
        return []

    out: list[ExtractedObservation] = []
    for series, target in _SERIES_COLORS.items():
        out.extend(_trace_series(arr, series, target, y_calib, x_calib))
    log.info("keepa_chart.extracted", count=len(out))
    return _compact_daily(out)


# --- Calibration ------------------------------------------------------------


def _calibrate_y_axis(arr: np.ndarray) -> "_LinearCalib | None":
    """Read y-axis price labels with OCR; build a (pixel_y → price-pence) map.

    Keepa renders the y-axis labels in the left margin (x < _PLOT_LEFT). They
    are integer pound values prefixed by '£'. Tesseract reads them; we pair
    each value with its detected pixel y-position (centre of the bounding
    box) and fit a line.

    Gracefully degrades to None on any failure (no tesseract installed, OCR
    error, regression fit error). Callers treat None as "no Keepa data".
    """
    try:
        import pytesseract
    except ImportError:
        return None

    # Crop the left margin where prices sit. Slight upward / downward padding
    # so labels close to the edges aren't clipped by the bounding box.
    left = Image.fromarray(arr[:, : _PLOT_LEFT + 5])
    try:
        data = pytesseract.image_to_data(
            left, config="--psm 6", output_type=pytesseract.Output.DICT
        )
    except Exception as exc:
        log.info("keepa_chart.y_ocr.error", error=str(exc))
        return None
    points: list[tuple[float, int]] = []  # (pixel_y_center, price_minor)
    for text, top, height, conf in zip(
        data["text"], data["top"], data["height"], data["conf"]
    ):
        if not text or int(conf) < 20:
            continue
        # Require £ prefix: stray digits from the title or watermark would
        # otherwise pollute the fit (a "2" from "2026" near the top of the
        # image got picked up and rotated the slope ~30%).
        m = re.fullmatch(r"£\s*(\d{1,4})", text.strip())
        if not m:
            continue
        price_minor = int(m.group(1)) * 100
        center_y = float(top) + float(height) / 2
        points.append((center_y, price_minor))
    if len(points) < 2:
        return None
    # Sort by pixel-y so the regression sees ascending input. (Higher pixel-y
    # = lower price on a chart — y increases downward in image coordinates.)
    points.sort()
    return _LinearCalib.fit(points)


_MONTH_RE = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b")
_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _calibrate_x_axis(arr: np.ndarray) -> "_DateCalib | None":
    """Read x-axis month labels; build a (pixel_x → date) map.

    See `_calibrate_y_axis` for graceful-degradation contract.
    """
    try:
        import pytesseract
    except ImportError:
        return None

    bottom = Image.fromarray(arr[_PLOT_BOTTOM:, :])
    try:
        data = pytesseract.image_to_data(
            bottom, config="--psm 6", output_type=pytesseract.Output.DICT
        )
    except Exception as exc:
        log.info("keepa_chart.x_ocr.error", error=str(exc))
        return None
    raw: list[tuple[float, str]] = []  # (pixel_x_center, month-abbrev)
    for text, left, width, conf in zip(
        data["text"], data["left"], data["width"], data["conf"]
    ):
        if not text or int(conf) < 20:
            continue
        m = _MONTH_RE.search(text)
        if not m:
            continue
        center_x = float(left) + float(width) / 2
        raw.append((center_x, m.group(1)))
    if len(raw) < 2:
        return None
    raw.sort()
    # Assign a year to each month label by walking BACKWARDS from the
    # rightmost (most recent) label. The rightmost label's year is "this
    # year" (or last year if the month is in the future).
    today = datetime.utcnow().date()
    labels: list[tuple[float, date]] = []
    cur_year = today.year
    cur_month = today.month
    for x, name in reversed(raw):
        month_num = _MONTHS[name]
        # Walk backwards until month_num <= cur_month, decrementing year on
        # wrap-around.
        while month_num > cur_month:
            cur_month += 12
            cur_year -= 1
        labels.append((x, date(cur_year, month_num, 1)))
        cur_month = month_num - 1
        if cur_month <= 0:
            cur_month += 12
            cur_year -= 1
    labels.sort()
    return _DateCalib.fit(labels)


@dataclass
class _LinearCalib:
    """y = slope * x + intercept.

    For pixel_y → price-pence. Higher pixel_y → lower price, so slope is
    negative.
    """

    slope: float
    intercept: float

    @classmethod
    def fit(cls, points: list[tuple[float, int]]) -> "_LinearCalib":
        xs = np.array([p[0] for p in points], dtype=float)
        ys = np.array([p[1] for p in points], dtype=float)
        slope, intercept = np.polyfit(xs, ys, 1)
        return cls(slope=float(slope), intercept=float(intercept))

    def __call__(self, pixel_y: float) -> int:
        return round(self.slope * pixel_y + self.intercept)


@dataclass
class _DateCalib:
    """pixel_x → date.

    Internally fits a linear mapping over (pixel_x, days-since-epoch).
    """

    slope_days: float
    epoch_offset: float
    epoch: date

    @classmethod
    def fit(cls, points: list[tuple[float, date]]) -> "_DateCalib":
        epoch = points[0][1]
        xs = np.array([p[0] for p in points], dtype=float)
        ys = np.array([(p[1] - epoch).days for p in points], dtype=float)
        slope, intercept = np.polyfit(xs, ys, 1)
        return cls(slope_days=float(slope), epoch_offset=float(intercept), epoch=epoch)

    def __call__(self, pixel_x: float) -> date:
        offset = round(self.slope_days * pixel_x + self.epoch_offset)
        return self.epoch + timedelta(days=offset)


# --- Series tracing ---------------------------------------------------------


def _trace_series(
    arr: np.ndarray,
    series: SeriesName,
    target: tuple[int, int, int],
    y_calib: _LinearCalib,
    x_calib: _DateCalib,
) -> list[ExtractedObservation]:
    """Walk each column of the plot area, find the y of the target color line."""
    plot = arr[_PLOT_TOP:_PLOT_BOTTOM, _PLOT_LEFT:_PLOT_RIGHT]
    h, w, _ = plot.shape
    tgt = np.array(target, dtype=np.int16)
    diff = np.abs(plot.astype(np.int16) - tgt).max(axis=2)  # shape (h, w)
    mask = diff <= _COLOR_TOLERANCE  # bool (h, w)
    out: list[ExtractedObservation] = []
    for col in range(w):
        ys = np.flatnonzero(mask[:, col])
        if ys.size == 0:
            continue
        # The line itself is ~2 px thick (anti-aliased). Use the centre.
        # Step-function shape means consecutive cols of the same y form a
        # horizontal segment; we keep all and let _compact_daily dedupe.
        pixel_y_in_plot = int(round(ys.mean()))
        pixel_y_full = pixel_y_in_plot + _PLOT_TOP
        pixel_x_full = col + _PLOT_LEFT
        price = y_calib(pixel_y_full)
        if price <= 0:
            continue
        d = x_calib(pixel_x_full)
        out.append(ExtractedObservation(d, series, price))
    return out


def _compact_daily(observations: Iterable[ExtractedObservation]) -> list[ExtractedObservation]:
    """Reduce to one observation per (date, series), taking the MEDIAN price.

    Multiple x-columns can map to the same calendar date (3 pixels/day on a
    1200×365 chart). Median is robust to the occasional anti-aliasing pixel
    that drags one column to a wrong y.
    """
    buckets: dict[tuple[date, SeriesName], list[int]] = {}
    for o in observations:
        buckets.setdefault((o.observed_at, o.series), []).append(o.price_minor)
    out: list[ExtractedObservation] = []
    for (d, s), prices in sorted(buckets.items()):
        med = int(np.median(prices))
        out.append(ExtractedObservation(d, s, med))
    return out


# Map Keepa's three series to (seller_label, condition) tuples used by our
# PriceObservation rows. "Amazon" = the buy-box winner when Amazon themselves
# are the seller; "Amazon Marketplace" = the lowest 3rd-party offer (new or
# used). Used grade defaults to "used_g" (Good, mid-tier) since Keepa
# collapses all four used grades into one line.
SERIES_TO_SELLER_CONDITION: dict[SeriesName, tuple[str, str]] = {
    "amazon": ("Amazon", "new"),
    "new": ("Amazon Marketplace", "new"),
    "used": ("Amazon Marketplace", "used_g"),
}
