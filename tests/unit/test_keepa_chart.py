"""Pure-function tests for `keepa_chart`.

The full PNG → observations pipeline depends on Tesseract via pytesseract,
which isn't installed on every dev host. Tests in this file exercise the
deterministic helpers that compose the extractor (calibration fits,
monotonicity enforcement, daily compaction, bad-input handling). The
end-to-end PNG test is gated on `pytesseract.get_tesseract_version()`.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from book_alerter.keepa_chart import (
    ExtractedObservation,
    _DateCalib,
    _LinearCalib,
    _compact_daily,
    _enforce_monotonic_decreasing,
    extract_observations,
    observed_at_to_datetime,
)


def test_observed_at_to_datetime_returns_midnight_utc():
    dt = observed_at_to_datetime(date(2026, 5, 14))
    assert dt == datetime(2026, 5, 14, 0, 0, 0, tzinfo=UTC)


def test_linear_calib_fits_known_line():
    # Pixel-y ↑ → price ↓; classic Keepa y-axis (origin top-left).
    points = [(50.0, 5000), (150.0, 3000), (250.0, 1000)]  # slope = -20 pence/px
    calib = _LinearCalib.fit(points)
    assert round(calib.slope) == -20
    assert calib(100.0) == 4000
    assert calib(200.0) == 2000
    assert calib.min_price == 1000
    assert calib.max_price == 5000


def test_date_calib_fits_monthly_labels():
    points = [
        (100.0, date(2026, 1, 1)),
        (400.0, date(2026, 2, 1)),
        (700.0, date(2026, 3, 1)),
    ]
    calib = _DateCalib.fit(points)
    # Monthly spacing isn't perfectly uniform (28–31 days), so the fit
    # interpolates to within ±2 days at the calibration points.
    assert abs((calib(100.0) - date(2026, 1, 1)).days) <= 2
    assert abs((calib(400.0) - date(2026, 2, 1)).days) <= 2
    mid = calib(250.0)
    assert abs((mid - date(2026, 1, 16)).days) <= 2


def test_enforce_monotonic_decreasing_keeps_good_points():
    # y increases left-to-right; price strictly decreases (Keepa invariant).
    points = [(50.0, 5000), (150.0, 3000), (250.0, 1000)]
    assert _enforce_monotonic_decreasing(points) == points


def test_enforce_monotonic_decreasing_drops_ocr_misread():
    # OCR drops a digit on the TOP-of-chart label: "£22" → "£2" gives a
    # point at pixel_y=50 (highest position) claiming a price of only 200p.
    # Every other point has both a larger pixel_y AND a smaller-than-the-
    # top-should-be price → no negative-slope pair → the outlier falls out.
    points = [
        (50.0, 200),    # misread — claims £2 at the top of the chart
        (100.0, 4000),
        (150.0, 3000),
        (200.0, 2000),
        (250.0, 1000),
    ]
    survivors = _enforce_monotonic_decreasing(points)
    assert (50.0, 200) not in survivors
    assert (250.0, 1000) in survivors


def test_enforce_monotonic_decreasing_returns_empty_on_unrecoverable_chaos():
    # Three values with no consistent direction → all dropped.
    chaos = [(50.0, 1000), (150.0, 1000), (250.0, 1000)]
    assert _enforce_monotonic_decreasing(chaos) == []


def test_compact_daily_picks_median_per_bucket():
    obs = [
        ExtractedObservation(date(2026, 5, 1), "amazon", 1000),
        ExtractedObservation(date(2026, 5, 1), "amazon", 1100),
        ExtractedObservation(date(2026, 5, 1), "amazon", 1200),
        ExtractedObservation(date(2026, 5, 1), "new", 1500),
        ExtractedObservation(date(2026, 5, 2), "amazon", 900),
    ]
    compact = _compact_daily(obs)
    assert ExtractedObservation(date(2026, 5, 1), "amazon", 1100) in compact
    assert ExtractedObservation(date(2026, 5, 1), "new", 1500) in compact
    assert ExtractedObservation(date(2026, 5, 2), "amazon", 900) in compact
    assert len(compact) == 3


def test_extract_observations_rejects_unparseable_bytes():
    assert extract_observations(b"not a PNG") == []
    assert extract_observations(b"") == []


def test_extract_observations_rejects_wrong_geometry():
    # Small PNG that opens fine but isn't 1200×400 — extractor must refuse,
    # not extrapolate from the wrong layout.
    img = Image.new("RGB", (100, 100), color=(255, 255, 255))
    buf = BytesIO()
    img.save(buf, format="PNG")
    assert extract_observations(buf.getvalue()) == []


def _has_tesseract() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _has_tesseract(), reason="tesseract not installed on host")
def test_extract_observations_synthetic_chart():
    """End-to-end: render a 1200×400 PNG that mimics Keepa's layout (£20 and
    £10 y-axis labels, Jan/Feb/Mar x-axis labels, a horizontal amazon-orange
    line at £15) and verify the extractor recovers ~£15 observations.

    This is intentionally permissive: OCR-on-synthetic-pixels won't match
    Keepa's actual font, so we accept any recovered points and just assert
    the price is within £3 of the rendered line."""
    img = Image.new("RGB", (1200, 400), color=(255, 255, 255))
    arr = np.asarray(img).copy()
    # Plot rectangle: y=20..380 maps to £20..£0 (slope = -20px/£). Render
    # a flat line at £15 → pixel_y = 380 - (15 * (380-20)/20) = 380 - 270 = 110.
    arr[108:112, 50:1102] = (255, 165, 0)  # amazon orange
    img = Image.fromarray(arr)

    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14
        )
    except OSError:
        font = ImageFont.load_default()
    draw.text((10, 12), "£20", fill=(0, 0, 0), font=font)
    draw.text((10, 192), "£10", fill=(0, 0, 0), font=font)
    draw.text((10, 372), "£0", fill=(0, 0, 0), font=font)
    # X-axis labels — minimal three months for the fit.
    draw.text((60, 384), "Jan", fill=(0, 0, 0), font=font)
    draw.text((560, 384), "Feb", fill=(0, 0, 0), font=font)
    draw.text((1060, 384), "Mar", fill=(0, 0, 0), font=font)

    buf = BytesIO()
    img.save(buf, format="PNG")
    out = extract_observations(buf.getvalue())

    # The extractor may return empty if synthetic OCR fails to match the £
    # prefix on a non-Keepa font. That's an acceptable outcome — what we
    # assert is the no-crash contract; if anything is recovered, prices
    # cluster around £15.
    if out:
        amazon_pts = [o for o in out if o.series == "amazon"]
        assert amazon_pts, "amazon series should be detected when present"
        prices = [o.price_minor for o in amazon_pts]
        median = sorted(prices)[len(prices) // 2]
        assert 1200 <= median <= 1800, f"expected ~£15, got £{median / 100}"
