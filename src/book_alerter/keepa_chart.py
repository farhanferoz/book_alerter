"""Reconstruct numeric price observations from a Keepa price-history PNG.

The Keepa chart layout (margins, fonts, colors) is stable across all ASINs.
A future Keepa restyle would break this; the extractor returns an empty list
on any parse failure rather than emitting bogus data.
"""
from __future__ import annotations

import io
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

import numpy as np
from PIL import Image

from book_alerter.db.models import Condition
from book_alerter.logging_setup import get_logger

log = get_logger(__name__)


SeriesName = Literal["amazon", "new", "used"]


@dataclass(frozen=True)
class ExtractedObservation:
    observed_at: date
    series: SeriesName
    # Pence. Reconstruction error is ~±£1 from anti-aliasing + axis-tick
    # rounding; callers should not treat this as exact.
    price_minor: int


_SERIES_COLORS: dict[SeriesName, tuple[int, int, int]] = {
    "amazon": (255, 165, 0),
    "new": (136, 136, 221),
    "used": (68, 68, 68),
}
_COLOR_TOLERANCE = 30  # per-channel delta tolerance for anti-aliased neighbours

# Plot rectangle in pixel coords, hand-sampled from a 1200×400 reference PNG.
# The extractor only supports this geometry; callers must request it.
_EXPECTED_W = 1200
_EXPECTED_H = 400
_PLOT_LEFT = 50
# Stay clear of the legend (~col 1108) whose coloured dots would pollute the trace.
_PLOT_RIGHT = 1102
_PLOT_TOP = 20
_PLOT_BOTTOM = 380

_Y_LABEL_RE = re.compile(r"£\s*(\d{1,4})")
_MONTH_RE = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b")
_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def extract_observations(png_bytes: bytes) -> list[ExtractedObservation]:
    """Return one observation per (day, series) from a Keepa-rendered PNG.

    Returns an empty list if the chart can't be parsed (unexpected geometry,
    OCR failure, no recognised series lines). Never raises on bad input.
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

    y_words = _ocr_region(img, (0, _PLOT_TOP, _PLOT_LEFT + 5, _PLOT_BOTTOM), upscale=3)
    x_words = _ocr_region(img, (_PLOT_LEFT, _PLOT_BOTTOM, _PLOT_RIGHT, _EXPECTED_H), upscale=3)
    if y_words is None or x_words is None:
        return []
    y_calib = _calibrate_y_axis(y_words)
    x_calib = _calibrate_x_axis(x_words)
    if y_calib is None or x_calib is None:
        log.warning("keepa_chart.calibration.failed",
                    y_ok=y_calib is not None, x_ok=x_calib is not None)
        return []
    # Allow ~20% headroom on either side of the calibrated label range —
    # legitimate prices can briefly poke outside the plotted band when Keepa
    # caches a chart while values are stable, but anything further out is
    # extrapolation noise (anti-aliased title-bar pixels, axis-tick text,
    # legend dots, etc.). Without this clamp the linear fit happily emits
    # £6.38 for a pixel 50px below the lowest gridline.
    price_lo = max(50, int(y_calib.min_price * 0.8))
    price_hi = int(y_calib.max_price * 1.2)

    plot = arr[_PLOT_TOP:_PLOT_BOTTOM, _PLOT_LEFT:_PLOT_RIGHT].astype(np.int16)
    out: list[ExtractedObservation] = []
    for series, target in _SERIES_COLORS.items():
        out.extend(_trace_series(plot, series, target, y_calib, x_calib, price_lo, price_hi))
    log.info("keepa_chart.extracted", count=len(out),
             price_range=(price_lo, price_hi))
    return _compact_daily(out)


# --- OCR --------------------------------------------------------------------


@dataclass(frozen=True)
class _OcrWord:
    text: str
    left: int
    top: int
    width: int
    height: int


def _ocr_region(
    img: Image.Image,
    box: tuple[int, int, int, int],
    *,
    upscale: int = 3,
) -> list[_OcrWord] | None:
    """OCR a sub-region of the PNG with optional upscaling.

    Keepa renders axis labels at ~8px tall, well below Tesseract's
    recommended 30+px character height. Cropping to the label region and
    upscaling 3x bumps confidence from sub-50 (often gibberish like "eu" or
    "ex" for "£14") to 90+. Bounding boxes are rescaled back to original
    image coordinates so downstream callers see consistent geometry.
    """
    try:
        import pytesseract
    except ImportError:
        return None
    x0, y0, _x1, _y1 = box
    crop = img.crop(box)
    if upscale != 1:
        crop = crop.resize((crop.width * upscale, crop.height * upscale), Image.LANCZOS)
    try:
        data = pytesseract.image_to_data(
            crop, config="--psm 6", output_type=pytesseract.Output.DICT
        )
    except Exception as exc:
        log.info("keepa_chart.ocr.error", error=str(exc))
        return None
    words: list[_OcrWord] = []
    for text, left, top, width, height, conf in zip(
        data["text"], data["left"], data["top"],
        data["width"], data["height"], data["conf"],
        strict=True,
    ):
        if not text or int(conf) < 20:
            continue
        words.append(_OcrWord(
            text=text.strip(),
            left=int(left) // upscale + x0,
            top=int(top) // upscale + y0,
            width=int(width) // upscale,
            height=int(height) // upscale,
        ))
    return words


# --- Calibration ------------------------------------------------------------


def _calibrate_y_axis(words: list[_OcrWord]) -> _LinearCalib | None:
    """Build a (pixel_y → price-pence) map from the £-prefixed y-axis labels.

    Tesseract occasionally drops a digit ("£22" → "£2") which would shift the
    whole regression by ~£7. We defend with two layers:

    1. Robust fit: iteratively drop the worst-residual point until the max
       residual is below `_Y_CALIB_MAX_RESIDUAL_PENCE`. A misread like
       (y=105, £2) sits ~2000 pence off the true line; the loop ejects it
       on the first iteration.
    2. Range gating: callers use the surviving min/max label prices to
       clamp emitted observations, so anti-aliased pixels far outside the
       plotted band don't leak through as fake low/high prices.
    """
    points: list[tuple[float, int]] = []
    for w in words:
        # The £ prefix is load-bearing: a stray "2" from "2026" near the top
        # of the image would otherwise rotate the slope ~30%.
        if w.left >= _PLOT_LEFT + 5:
            continue
        m = _Y_LABEL_RE.fullmatch(w.text)
        if not m:
            continue
        price_minor = int(m.group(1)) * 100
        center_y = w.top + w.height / 2
        points.append((center_y, price_minor))
    if len(points) < 2:
        return None
    points.sort()
    pruned = _enforce_monotonic_decreasing(points)
    if len(pruned) < 2:
        log.warning("keepa_chart.y_calib.no_inliers", n_input=len(points))
        return None
    if len(pruned) < len(points):
        log.info(
            "keepa_chart.y_calib.outliers_dropped",
            n_in=len(points), n_kept=len(pruned),
            dropped=[p for p in points if p not in pruned],
        )
    return _LinearCalib.fit(pruned)


def _enforce_monotonic_decreasing(
    points: list[tuple[float, int]],
) -> list[tuple[float, int]]:
    """Drop OCR misreads using the y-axis invariant: price strictly
    decreases as pixel-y increases (higher y is lower on the chart).

    Iteratively removes any point that has no pairwise neighbour with the
    expected negative slope. A typical Tesseract digit-drop like
    "£22" → "£2" leaves the outlier with NO negative-slope pair against
    the surviving labels, so it falls out in one pass. Residual-based
    rejection can't do this safely — a single outlier on the edge of the
    data pulls the OLS line enough that good points end up looking like
    the misreads.
    """
    survivors = list(points)
    while len(survivors) >= 2:
        keep: list[tuple[float, int]] = []
        for i, (yi, pi) in enumerate(survivors):
            has_neg_neighbour = any(
                (pj - pi) * (yj - yi) < 0
                for j, (yj, pj) in enumerate(survivors) if j != i
            )
            if has_neg_neighbour:
                keep.append((yi, pi))
        if len(keep) == len(survivors):
            return survivors
        if not keep:
            return []
        survivors = keep
    return survivors


def _calibrate_x_axis(words: list[_OcrWord]) -> _DateCalib | None:
    """Build a (pixel_x → date) map from the bottom-axis month labels."""
    raw: list[tuple[float, str]] = []
    for w in words:
        if w.top < _PLOT_BOTTOM:
            continue
        m = _MONTH_RE.search(w.text)
        if not m:
            continue
        center_x = w.left + w.width / 2
        raw.append((center_x, m.group(1)))
    if len(raw) < 2:
        return None
    raw.sort()
    # Assign years walking BACKWARDS from the rightmost label. Today's year
    # for the rightmost; subtract one whenever the previous label's month is
    # numerically greater than the current month (wraparound across Dec→Jan).
    today = datetime.now(UTC).date()
    cur_year = today.year
    cur_month = today.month
    labels: list[tuple[float, date]] = []
    for x, name in reversed(raw):
        month_num = _MONTHS[name]
        if month_num > cur_month:
            cur_year -= 1
        labels.append((x, date(cur_year, month_num, 1)))
        cur_month = month_num
    labels.sort()
    return _DateCalib.fit(labels)


@dataclass
class _LinearCalib:
    """pixel_y → price-pence. Higher pixel_y = lower price → slope negative.

    Also carries the min/max OCR'd label prices so the extractor can clamp
    out-of-range pixels (axis labels, title text, legend dots) instead of
    extrapolating them to plausible-looking but fictional prices.
    """

    slope: float
    intercept: float
    min_price: int
    max_price: int

    @classmethod
    def fit(cls, points: list[tuple[float, int]]) -> _LinearCalib:
        xs = np.array([p[0] for p in points], dtype=float)
        ys = np.array([p[1] for p in points], dtype=float)
        slope, intercept = np.polyfit(xs, ys, 1)
        prices = [p[1] for p in points]
        return cls(
            slope=float(slope),
            intercept=float(intercept),
            min_price=min(prices),
            max_price=max(prices),
        )

    def __call__(self, pixel_y: float) -> int:
        return round(self.slope * pixel_y + self.intercept)


@dataclass
class _DateCalib:
    """pixel_x → date, via a linear fit on (pixel_x, days-since-epoch)."""

    slope_days: float
    epoch_offset: float
    epoch: date

    @classmethod
    def fit(cls, points: list[tuple[float, date]]) -> _DateCalib:
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
    plot_int16: np.ndarray,
    series: SeriesName,
    target: tuple[int, int, int],
    y_calib: _LinearCalib,
    x_calib: _DateCalib,
    price_lo: int,
    price_hi: int,
) -> list[ExtractedObservation]:
    _h, w, _ = plot_int16.shape
    tgt = np.array(target, dtype=np.int16)
    diff = np.abs(plot_int16 - tgt).max(axis=2)  # (h, w)
    mask = diff <= _COLOR_TOLERANCE
    out: list[ExtractedObservation] = []
    for col in range(w):
        ys = np.flatnonzero(mask[:, col])
        if ys.size == 0:
            continue
        # The line is ~2px thick (anti-aliased); use the centre. Multiple
        # columns mapping to the same calendar date are deduped in
        # _compact_daily.
        pixel_y_full = round(ys.mean()) + _PLOT_TOP
        pixel_x_full = col + _PLOT_LEFT
        price = y_calib(pixel_y_full)
        if price < price_lo or price > price_hi:
            continue
        out.append(ExtractedObservation(x_calib(pixel_x_full), series, price))
    return out


def _compact_daily(observations: Iterable[ExtractedObservation]) -> list[ExtractedObservation]:
    """Reduce to one observation per (date, series), taking the MEDIAN price.

    Median is robust to the occasional anti-aliasing pixel that drags one
    column to a wrong y on a 3-px/day chart.
    """
    buckets: dict[tuple[date, SeriesName], list[int]] = {}
    for o in observations:
        buckets.setdefault((o.observed_at, o.series), []).append(o.price_minor)
    out: list[ExtractedObservation] = []
    for (d, s), prices in sorted(buckets.items()):
        med = int(np.median(prices))
        out.append(ExtractedObservation(d, s, med))
    return out


# Keepa collapses all four used grades into one line; we attribute to "used_g"
# (Good, mid-tier) since that's the modal grade in marketplace listings.
SERIES_TO_SELLER_CONDITION: dict[SeriesName, tuple[str, Condition]] = {
    "amazon": ("Amazon", "new"),
    "new": ("Amazon Marketplace", "new"),
    "used": ("Amazon Marketplace", "used_g"),
}

def observed_at_to_datetime(d: date) -> datetime:
    """Midnight-UTC datetime for a recovered chart date."""
    return datetime.combine(d, time.min, tzinfo=UTC)
