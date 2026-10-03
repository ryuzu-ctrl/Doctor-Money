from datetime import date, timedelta
from io import BytesIO
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


BG = "#0A1232"
CYAN = "#4FE3E6"


def expense_charts(state: dict[str, Any]) -> tuple[BytesIO, BytesIO]:
    today = date.today()
    keys = [(today - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)]
    values = []
    by_day = {key: 0 for key in keys}
    by_category: dict[str, int] = {}
    for tx in state.get("txs", []):
        if tx.get("type") != "out" or tx.get("sv"):
            continue
        if tx.get("date") in by_day:
            by_day[tx["date"]] += int(tx.get("amt", 0))
        if tx.get("date", "") >= keys[0]:
            category = str(tx.get("cat", "lain"))
            by_category[category] = by_category.get(category, 0) + int(tx.get("amt", 0))
    values = [by_day[key] for key in keys]

    fig, axis = plt.subplots(figsize=(8, 4), facecolor=BG)
    axis.set_facecolor(BG)
    axis.plot(range(7), values, color=CYAN, linewidth=2.5, marker="o")
    axis.fill_between(range(7), values, color=CYAN, alpha=.18)
    axis.set_xticks(range(7), [date.fromisoformat(key).strftime("%d/%m") for key in keys], color="#A3B3CC")
    axis.tick_params(axis="y", colors="#A3B3CC")
    axis.grid(color="#FFFFFF", alpha=.12)
    for spine in axis.spines.values():
        spine.set_color("#FFFFFF")
        spine.set_alpha(.15)
    axis.set_title("Pengeluaran 7 Hari", color="#EAF2FF", loc="left")
    fig.tight_layout()
    week = BytesIO()
    fig.savefig(week, format="png", dpi=140, facecolor=BG)
    plt.close(fig)
    week.seek(0)

    categories = sorted(by_category.items(), key=lambda item: item[1], reverse=True)[:8]
    fig, axis = plt.subplots(figsize=(8, max(3, .42 * len(categories))), facecolor=BG)
    axis.set_facecolor(BG)
    if categories:
        axis.barh([item[0] for item in categories][::-1], [item[1] for item in categories][::-1], color=CYAN)
    axis.tick_params(colors="#A3B3CC")
    axis.grid(axis="x", color="#FFFFFF", alpha=.12)
    for spine in axis.spines.values():
        spine.set_visible(False)
    axis.set_title("Sebaran Kategori", color="#EAF2FF", loc="left")
    fig.tight_layout()
    distribution = BytesIO()
    fig.savefig(distribution, format="png", dpi=140, facecolor=BG)
    plt.close(fig)
    distribution.seek(0)
    return week, distribution