import re
import json
from datetime import datetime

# 读本地 data/data.js（与 build_data / 页面同一真值源）；不再硬编码失效的远端 URL
src = open("data/data.js", encoding="utf-8").read()
D = json.loads(re.search(r"window\.FIB_DATA\s*=\s*(\{.*\})\s*;?\s*$", src, re.S).group(1))

W, H = 900, 420
mL, mR, mT, mB = 60, 40, 50, 60


def ts(d):
    return datetime.strptime(d, "%Y-%m-%d").timestamp()


x0 = ts("2026-05-01")
x1 = ts("2027-09-30")
y0 = 3600
# y1 自适应：覆盖所有绘制点位（含卖③≈5334）并留标签余量，避免任何目标被裁出画布上沿
_prices = [p["price"] for p in D["subForecast"]["points"]] + \
          [D["tradePlan"]["stopLine"]["price"], D["tradePlan"]["sellTargets"][0]["price"]]
y1 = (int(max(_prices) / 200) + 1) * 200 + 200


def sx(d):
    return mL + (ts(d) - x0) / (x1 - x0) * (W - mL - mR)


def sy(v):
    return H - mB - (v - y0) / (y1 - y0) * (H - mT - mB)


# actual price line from 2026-05-01
actual = []
for d, c in zip(D["kline"]["dates"], D["kline"]["close"]):
    if d >= "2026-05-01":
        actual.append((sx(d), sy(c)))
actual_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in actual)

# projection points
proj = [(sx(p["date"]), sy(p["price"])) for p in D["subForecast"]["points"]]
proj_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in proj)

# x ticks every 2 months
labels = []
d = datetime(2026, 5, 1)
while d <= datetime(2027, 9, 1):
    lab = d.strftime("%m月") if d.month != 1 else d.strftime("%Y年%m月")
    labels.append((d.strftime("%Y-%m-%d"), lab))
    month = d.month + 2
    year = d.year
    if month > 12:
        month -= 12
        year += 1
    d = datetime(year, month, 1)

tick_lines = ""
tick_texts = ""
for d, lab in labels:
    x = sx(d)
    tick_lines += f'<line x1="{x:.1f}" y1="{H - mB}" x2="{x:.1f}" y2="{H - mB + 5}" stroke="#999"/>'
    tick_texts += f'<text x="{x:.1f}" y="{H - mB + 22}" font-size="12" fill="#666" text-anchor="middle">{lab}</text>'

# y ticks（自适应覆盖到 y1）
yticks = list(range(y0, y1 + 1, 400))
ytick_svg = ""
for v in yticks:
    y = sy(v)
    ytick_svg += f'<line x1="{mL}" y1="{y:.1f}" x2="{W - mR}" y2="{y:.1f}" stroke="#eee"/>'
    ytick_svg += f'<text x="{mL - 8}" y="{y + 4:.1f}" font-size="12" fill="#666" text-anchor="end">{v}</text>'

# points and labels
colors = {"buy": "#2f9e44", "sell": "#c23531", "hold": "#16213e"}
symbols = ""
for p in D["subForecast"]["points"]:
    x = sx(p["date"])
    y = sy(p["price"])
    color = colors.get(p.get("side", "hold"), "#16213e")
    tag = p.get("tag", "")
    label = (tag + " " if tag else "") + p["label"] + " " + str(p["price"])
    symbols += f'<rect x="{x - 5}" y="{y - 5}" width="10" height="10" fill="{color}"/>'
    symbols += f'<text x="{x}" y="{y - 12}" font-size="11" fill="{color}" text-anchor="middle" font-weight="bold">{label}</text>'

# 派生子浪ⅴ日期（避免 SVG 文案硬编码漂值）
_v5 = next((p for p in D["subForecast"]["points"] if "子浪ⅴ" in p.get("label", "")), None)
_v5d = _v5["date"] if _v5 else "—"

svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <rect width="{W}" height="{H}" fill="#fff"/>
  {ytick_svg}
  {tick_lines}
  {tick_texts}
  <polyline points="{actual_pts}" fill="none" stroke="#242a35" stroke-width="2"/>
  <polyline points="{proj_pts}" fill="none" stroke="#16213e" stroke-width="2" stroke-dasharray="6,4"/>
  {symbols}
  <text x="{W / 2}" y="25" font-size="16" fill="#16213e" text-anchor="middle" font-weight="bold">浪⑤子浪走势预测（本地数据生成）</text>
  <text x="{W - 10}" y="{H - 10}" font-size="11" fill="#999" text-anchor="end">子浪ⅴ date = {_v5d}（应落在10月底）</text>
</svg>"""

open("subf_reference.svg", "w", encoding="utf-8").write(svg)
print("wrote subf_reference.svg")
