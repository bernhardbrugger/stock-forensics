"""The price chart as an inline SVG: weekly closes, 50 and 200-day averages, drawdown shading.

Pure Python, no scripts and no external files, so it stays inside the single self-contained HTML.
Colors come from CSS classes of the report (``.chart ...``), which follow the light or dark mode.
The picture has a title and a text description for screen readers, axis labels with the first,
middle and last date and the price range, and a legend. Every coordinate is finite and rounded.

The shaded areas mark stretches in which the price was at least 10 % below its highest weekly
close of the shown period (the "drawdown" the figures above describe).
"""

from __future__ import annotations

from decimal import Decimal
from html import escape

from markupsafe import Markup

from stock_forensics.market.models import PriceChart

WIDTH = 720
HEIGHT = 300
LEFT = 58
RIGHT = 14
TOP = 34  # room above the plot for the legend
BOTTOM = 34
SHADE_FROM = Decimal("-0.10")  # drawdown at which the shading starts


def _fmt(x: float) -> str:
    return f"{x:.1f}"


def price_chart_svg(chart: PriceChart) -> Markup:
    """The chart as markup that is safe to insert into the HTML (all text is escaped here)."""
    points = chart.points
    days = [p.day.toordinal() for p in points]
    values: list[Decimal] = []
    for p in points:
        values.append(p.close)
        values += [v for v in (p.sma_50, p.sma_200) if v is not None]
    low, high = float(min(values)), float(max(values))
    span = (high - low) or 1.0
    pad = span * 0.06
    low, high = low - pad, high + pad
    first, last = days[0], days[-1]
    width_days = (last - first) or 1
    plot_w, plot_h = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM

    def x(day: int) -> float:
        return LEFT + plot_w * (day - first) / width_days

    def y(value: Decimal | float) -> float:
        return TOP + plot_h * (high - float(value)) / (high - low)

    def line(selected: list[tuple[int, Decimal]]) -> str:
        return " ".join(
            ("M" if i == 0 else "L") + f"{_fmt(x(d))},{_fmt(y(v))}"
            for i, (d, v) in enumerate(selected)
        )

    close_path = line([(d, p.close) for d, p in zip(days, points, strict=True)])
    fast_path = line([(d, p.sma_50) for d, p in zip(days, points, strict=True) if p.sma_50])
    slow_path = line([(d, p.sma_200) for d, p in zip(days, points, strict=True) if p.sma_200])

    # drawdown shading: between the running peak and the price while the drawdown is deep
    peak = points[0].close
    peaks: list[Decimal] = []
    deep: list[bool] = []
    for p in points:
        peak = max(peak, p.close)
        peaks.append(peak)
        deep.append(p.close / peak - 1 <= SHADE_FROM)
    shades: list[str] = []
    i = 0
    while i < len(points):
        if not deep[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(points) and deep[j + 1]:
            j += 1
        a = max(i - 1, 0)  # start where the price was still near its peak
        upper = [f"{_fmt(x(days[k]))},{_fmt(y(peaks[k]))}" for k in range(a, j + 1)]
        lower = [f"{_fmt(x(days[k]))},{_fmt(y(points[k].close))}" for k in range(j, a - 1, -1)]
        shades.append(f'<polygon class="dd" points="{" ".join(upper + lower)}"/>')
        i = j + 1

    ticks = [low + pad, (low + high) / 2, high - pad]
    y_labels = "".join(
        f'<line class="grid" x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{_fmt(y(t))}" y2="{_fmt(y(t))}"/>'
        f'<text class="axis" x="{LEFT - 6}" y="{_fmt(y(t) + 4)}" text-anchor="end">${t:,.0f}</text>'
        for t in ticks
    )
    mid = points[len(points) // 2]
    x_labels = "".join(
        f'<text class="axis" x="{_fmt(x(p.day.toordinal()))}" y="{HEIGHT - 12}" '
        f'text-anchor="{anchor}">{p.day.isoformat()}</text>'
        for p, anchor in ((points[0], "start"), (mid, "middle"), (points[-1], "end"))
    )
    legend = (
        f'<g class="legend" transform="translate({LEFT + 8},16)">'
        '<line class="px" x1="0" x2="18" y1="0" y2="0"/><text x="24" y="4">Weekly close</text>'
        '<line class="s50" x1="112" x2="130" y1="0" y2="0"/>'
        '<text x="136" y="4">50-day average</text>'
        '<line class="s200" x1="248" x2="266" y1="0" y2="0"/>'
        '<text x="272" y="4">200-day average</text>'
        '<rect class="dd" x="392" y="-6" width="18" height="12"/>'
        '<text x="416" y="4">10% or more below the high</text></g>'
    )
    ticker = escape(chart.ticker)
    description = (
        f"Weekly closing price of {ticker} from {points[0].day} to {points[-1].day}: "
        f"from ${float(points[0].close):,.2f} to ${float(points[-1].close):,.2f}, "
        f"low ${float(min(p.close for p in points)):,.2f}, high "
        f"${float(max(p.close for p in points)):,.2f}, with its 50-day and 200-day averages."
    )
    return Markup(
        f'<svg class="chart" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
        f'aria-labelledby="chart-title chart-desc" xmlns="http://www.w3.org/2000/svg">'
        f'<title id="chart-title">Price chart of {ticker}</title>'
        f'<desc id="chart-desc">{escape(description)}</desc>'
        f"{y_labels}{''.join(shades)}"
        f'<path class="s200" d="{slow_path}"/><path class="s50" d="{fast_path}"/>'
        f'<path class="px" d="{close_path}"/>'
        f"{x_labels}{legend}</svg>"
    )
