"""results/bench-*.json  ->  bench-table.md + SVG charts.

No plotting dependency: the SVGs are hand-assembled (a test rig on a Pi
shouldn't need matplotlib). Run after a --bench suite:

    python3 -m hil.charts results/            # writes into results/
"""

import glob
import json
import pathlib
import sys

# --- tiny SVG primitives --------------------------------------------------
W, H = 720, 420
PAD_L, PAD_R, PAD_T, PAD_B = 70, 160, 30, 55
PLOT_W = W - PAD_L - PAD_R
PLOT_H = H - PAD_T - PAD_B
PALETTE = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#0891b2"]

_CSS = """
  text{font:13px -apple-system,Segoe UI,Roboto,sans-serif;fill:#1f2937}
  .ax{stroke:#9ca3af;stroke-width:1}
  .grid{stroke:#e5e7eb;stroke-width:1}
  .ttl{font-size:15px;font-weight:600}
  @media (prefers-color-scheme:dark){
    text{fill:#e5e7eb}.ax{stroke:#6b7280}.grid{stroke:#374151}
    svg{background:#111827}
  }
"""


def _svg(body, title):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'font-family="sans-serif"><style>{_CSS}</style>'
        f'<rect width="{W}" height="{H}" fill="white" '
        f'fill-opacity="0" /><text class="ttl" x="{PAD_L}" y="18">{title}</text>'
        f"{body}</svg>\n"
    )


def _nice_max(v):
    if v <= 0:
        return 1
    import math

    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag


def _axes(xmax, ymax, xlabel, ylabel, xticks=None):
    x0, y0 = PAD_L, PAD_T + PLOT_H
    out = [
        f'<line class="ax" x1="{x0}" y1="{y0}" x2="{x0 + PLOT_W}" y2="{y0}"/>',
        f'<line class="ax" x1="{x0}" y1="{y0}" x2="{x0}" y2="{PAD_T}"/>',
    ]
    for i in range(6):
        yv = ymax * i / 5
        y = y0 - PLOT_H * i / 5
        out.append(f'<line class="grid" x1="{x0}" y1="{y:.1f}" x2="{x0 + PLOT_W}" y2="{y:.1f}"/>')
        out.append(f'<text x="{x0 - 8}" y="{y + 4:.1f}" text-anchor="end">{yv:g}</text>')
    ticks = xticks if xticks is not None else [xmax * i / 5 for i in range(6)]
    for xv in ticks:
        x = x0 + PLOT_W * (xv / xmax if xmax else 0)
        out.append(f'<text x="{x:.1f}" y="{y0 + 18}" text-anchor="middle">{xv:g}</text>')
    out.append(f'<text x="{x0 + PLOT_W / 2}" y="{H - 12}" text-anchor="middle">{xlabel}</text>')
    out.append(
        f'<text transform="translate(16,{PAD_T + PLOT_H / 2}) rotate(-90)" '
        f'text-anchor="middle">{ylabel}</text>'
    )
    return "".join(out), x0, y0


def _legend(labels):
    out = []
    for i, lab in enumerate(labels):
        c = PALETTE[i % len(PALETTE)]
        y = PAD_T + 6 + i * 20
        out.append(f'<rect x="{W - PAD_R + 12}" y="{y}" width="12" height="12" fill="{c}"/>')
        out.append(f'<text x="{W - PAD_R + 30}" y="{y + 11}">{lab}</text>')
    return "".join(out)


# --- chart builders -----------------------------------------------------
def chart_latency_vs_size(records):
    pts_by_board = {}
    for r in records:
        if r.get("report_bytes") is None:
            continue
        b = r["latency_ms"].get("button", {})
        for series, stat in (
            ("e2e p50", b.get("e2e", {}).get("p50")),
            ("ble p50", b.get("ble", {}).get("p50")),
        ):
            key = f"{r['board']} {series}"
            pts_by_board.setdefault(key, []).append((r["report_bytes"], stat))
    pts_by_board = {k: sorted(p for p in v if p[1] is not None) for k, v in pts_by_board.items()}
    pts_by_board = {k: v for k, v in pts_by_board.items() if v}
    if not pts_by_board:
        return None
    xmax = _nice_max(max(x for v in pts_by_board.values() for x, _ in v))
    ymax = _nice_max(max(y for v in pts_by_board.values() for _, y in v))
    axes, x0, y0 = _axes(xmax, ymax, "HID input report size (bytes)", "button latency p50 (ms)")
    body = [axes]
    for i, (label, pts) in enumerate(sorted(pts_by_board.items())):
        c = PALETTE[i % len(PALETTE)]
        d = " ".join(f"{x0 + PLOT_W * x / xmax:.1f},{y0 - PLOT_H * y / ymax:.1f}" for x, y in pts)
        body.append(f'<polyline points="{d}" fill="none" stroke="{c}" stroke-width="2"/>')
        for x, y in pts:
            body.append(
                f'<circle cx="{x0 + PLOT_W * x / xmax:.1f}" '
                f'cy="{y0 - PLOT_H * y / ymax:.1f}" r="3.5" fill="{c}"/>'
            )
    body.append(_legend(sorted(pts_by_board)))
    return _svg("".join(body), "Latency vs HID report size")


def _bar_chart(title, groups, series_names, ylabel, unit=""):
    """groups: list[(label, [values per series])]."""
    if not groups:
        return None
    ymax = _nice_max(max((v for _, vs in groups for v in vs if v is not None), default=1))
    axes, x0, y0 = _axes(1, ymax, "", ylabel, xticks=[])
    body = [axes]
    gw = PLOT_W / max(1, len(groups))
    bw = gw / (len(series_names) + 1)
    for gi, (label, vals) in enumerate(groups):
        gx = x0 + gi * gw
        for si, v in enumerate(vals):
            if v is None:
                continue
            c = PALETTE[si % len(PALETTE)]
            bx = gx + bw * (si + 0.5)
            bh = PLOT_H * v / ymax
            body.append(
                f'<rect x="{bx:.1f}" y="{y0 - bh:.1f}" width="{bw * 0.9:.1f}" '
                f'height="{bh:.1f}" fill="{c}"/>'
            )
        body.append(
            f'<text x="{gx + gw / 2:.1f}" y="{y0 + 18}" text-anchor="middle">{label}</text>'
        )
    body.append(_legend(series_names))
    return _svg("".join(body), title)


def chart_polling_rate(records):
    groups = []
    for r in sorted(records, key=lambda r: r.get("report_bytes") or 0):
        clean = r.get("clean_rate_hz")
        ceiling = 1000 / r["conn_interval_ms"] if r.get("conn_interval_ms") else None
        groups.append((f"{r['profile']} {r['board']}", [clean, ceiling]))
    return _bar_chart(
        "Fastest rate with 100% delivery",
        groups,
        ["measured clean Hz", "conn-interval ceiling"],
        "Hz",
    )


def chart_latency_distribution(records):
    groups = []
    for r in sorted(records, key=lambda r: r.get("report_bytes") or 0):
        b = r["latency_ms"].get("button", {}).get("e2e", {})
        groups.append((f"{r['profile']} {r['board']}", [b.get("p50"), b.get("p90"), b.get("p99")]))
    return _bar_chart(
        "Button latency distribution (end-to-end)", groups, ["p50", "p90", "p99"], "ms"
    )


# --- table --------------------------------------------------------------
def _env_line(records):
    e = next((r.get("env") for r in records if r.get("env")), None)
    if not e:
        return ""
    load = next(
        (
            r["load"]["start"]["loadavg"]
            for r in records
            if r.get("load", {}).get("start", {}).get("loadavg")
        ),
        None,
    )
    l1 = f", load {load[0]:.1f}" if load else ""
    return (
        f"> {e.get('distro')} · kernel {e.get('kernel')} · {e.get('arch')} · "
        f"BlueZ {e.get('bluez')} · Python {e.get('python')}{l1}\n\n"
    )


def table_md(records):
    rows = [
        "| Board | Profile | Report B | Descr B | Conn ms | MTU | "
        "btn e2e p50/p99 ms | axis p50 ms | clean Hz | dropped |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(records, key=lambda r: (r["board"], r.get("report_bytes") or 0)):
        b = r["latency_ms"].get("button", {}).get("e2e", {})
        ax = r["latency_ms"].get("axis", {}).get("e2e", {})
        dropped = sum(d.get("dropped", 0) for d in r["latency_ms"].values())
        rows.append(
            f"| {r['board']} | {r['profile']} | {r.get('report_bytes')} | "
            f"{r.get('descriptor_bytes')} | {r.get('conn_interval_ms')} | "
            f"{r.get('mtu')} | {b.get('p50')}/{b.get('p99')} | {ax.get('p50', '-')} | "
            f"{r.get('clean_rate_hz') or '-'} | {dropped} |"
        )
    return _env_line(records) + "\n".join(rows) + "\n"


# --- driver -----------------------------------------------------------
def latest_per_profile(paths):
    """paths must be sorted ascending (timestamp in filename) so the newest
    run for each (board, profile) is the one kept."""
    best = {}
    for p in paths:
        r = json.loads(pathlib.Path(p).read_text())
        best[(r["board"], r["profile"])] = r
    return list(best.values())


def main(results_dir):
    results_dir = pathlib.Path(results_dir)
    paths = sorted(glob.glob(str(results_dir / "bench-*.json")))
    if not paths:
        print("no results/bench-*.json found", file=sys.stderr)
        return 1
    records = latest_per_profile(paths)

    (results_dir / "bench-table.md").write_text("# HIL benchmark\n\n" + table_md(records))
    for name, fn in (
        ("latency-vs-reportsize", chart_latency_vs_size),
        ("polling-rate", chart_polling_rate),
        ("latency-distribution", chart_latency_distribution),
    ):
        svg = fn(records)
        if svg:
            (results_dir / f"{name}.svg").write_text(svg)
            print(f"wrote {name}.svg")
    print(f"wrote bench-table.md ({len(records)} profiles)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "results"))
