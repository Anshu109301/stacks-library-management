"""
charts.py
=========
Server-side chart rendering for the admin analytics page, using
matplotlib + pandas instead of a client-side JS charting library.

Why: the previous version loaded Chart.js from cdnjs.cloudflare.com in
the browser. On any network that blocks that CDN (school/office
firewalls, some ISPs, offline demos), every chart on the analytics page
silently failed to render. Rendering charts here, on the server, and
embedding them as base64 PNGs removes that dependency completely — the
page needs no external script and works identically online or offline.

Every chart is built straight from the same real, DB-backed data the
numeric tables next to it already use (see models.py) — nothing here
is hardcoded or faked, and an empty dataset renders a clear "No data
yet" placeholder instead of a misleading blank or broken chart.
"""

import base64
import io

import matplotlib
matplotlib.use("Agg")  # non-interactive backend: safe to use inside a web server
import matplotlib.pyplot as plt
import pandas as pd

PALETTE = ["#0071e3", "#b9862e", "#1d8a4c", "#d0342c", "#6e6e73", "#86868b", "#5ac8fa", "#0058b0"]

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "axes.edgecolor": "#d0d0d5",
    "axes.labelcolor": "#3a3a3c",
    "text.color": "#3a3a3c",
    "xtick.color": "#6e6e73",
    "ytick.color": "#6e6e73",
})


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", transparent=True)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _empty_chart(message: str = "No data yet") -> str:
    fig, ax = plt.subplots(figsize=(5, 3.2))
    ax.axis("off")
    ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=11, color="#8a8a8e")
    return _fig_to_base64(fig)


def pie_chart(distribution: dict) -> str:
    """distribution: {label: count}. Long tails are grouped into 'Other'
    so labels/percentages stay readable instead of overlapping."""
    labels = list(distribution.keys())
    values = list(distribution.values())
    if not labels or sum(values) == 0:
        return _empty_chart()

    df = pd.DataFrame({"label": labels, "value": values}).sort_values("value", ascending=False)

    max_slices = 7
    if len(df) > max_slices:
        head = df.iloc[: max_slices - 1]
        other_total = df.iloc[max_slices - 1:]["value"].sum()
        df = pd.concat(
            [head, pd.DataFrame([{"label": "Other", "value": other_total}])],
            ignore_index=True,
        )

    fig, ax = plt.subplots(figsize=(5, 4))
    wedges, _texts, autotexts = ax.pie(
        df["value"],
        colors=PALETTE[: len(df)],
        startangle=90,
        autopct=lambda pct: f"{pct:.0f}%" if pct >= 4 else "",
        pctdistance=0.75,
        wedgeprops={"linewidth": 1, "edgecolor": "white"},
    )
    for t in autotexts:
        t.set_color("white")
        t.set_fontsize(9)
    ax.legend(
        wedges,
        [f"{l} ({v})" for l, v in zip(df["label"], df["value"])],
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        fontsize=9,
        frameon=False,
    )
    ax.set_aspect("equal")
    return _fig_to_base64(fig)


def trend_chart(labels: list, values: list, series_label: str, color: str, kind: str = "line") -> str:
    """kind: 'line' (filled area) or 'bar'."""
    if not labels or sum(values) == 0:
        return _empty_chart()

    df = pd.DataFrame({"label": labels, "value": values})
    fig, ax = plt.subplots(figsize=(9, 3.2))
    if kind == "bar":
        ax.bar(df["label"], df["value"], color=color, width=0.6, zorder=3)
    else:
        ax.plot(df["label"], df["value"], color=color, marker="o", linewidth=2, zorder=3)
        ax.fill_between(df["label"], df["value"], color=color, alpha=0.12)
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", color="#e5e5ea", linewidth=0.8, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    ax.set_ylabel(series_label, fontsize=9)
    fig.tight_layout()
    return _fig_to_base64(fig)
