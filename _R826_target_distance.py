#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R826 诊断二：目标距离 vs 命中率 —— 检验"命中率是否实为大上涨幅探针"。

假设 H：predictions_log 里全部目标价都高于预测日收盘（"全上行目标族"），
        因此 dirCorrect 测的是"未来 N 天大盘涨幅够不够"，而非艾略特结构判断。

三重验证：
  V1 方向分解：每条记录 Δ=(px-close0)/close0 的正负分布（按 side 拆）。
  V2 距离分桶：|Δ| 分桶后命中率是否单调下降并在某桶恒 0。
  V3 反事实（placebo）：把目标价换成"现价 ×(1+δ)"（δ 取真实目标距离的同一分位），
     重算命中率。若与真实目标命中率同量级 ⇒ 目标数值本身几乎不含信息。
自校验：全量必须复现 18.9%（生产 19.0）。
"""
import csv
import json

HORIZON = 30
rows = list(csv.DictReader(open("data/sh000001.csv", encoding="utf-8")))
dates = [r["date"] for r in rows]
high = [float(r["high"]) for r in rows]
low = [float(r["low"]) for r in rows]
close = [float(r["close"]) for r in rows]
di = {d: i for i, d in enumerate(dates)}
recs = [json.loads(l) for l in open("data/predictions_log.jsonl", encoding="utf-8") if l.strip()]


def hit(rec, override_px=None):
    i0 = di.get(rec["date"])
    if i0 is None:
        return None
    px = float(override_px if override_px is not None else rec["price"])
    c0 = close[i0]
    hz = max(HORIZON, int(rec.get("expDays") or HORIZON))
    j1 = min(len(dates), i0 + 1 + hz)
    d = px - c0
    if j1 <= i0 + 1 or abs(d) <= 0.005 * c0:
        return None
    return (max(high[i0 + 1:j1]) >= c0 + 0.5 * d) if d > 0 else (min(low[i0 + 1:j1]) <= c0 + 0.5 * d)


def rate(sub, label, override=None):
    ok = n = 0
    for r in sub:
        v = hit(r, override(r) if override else None)
        if v is None:
            continue
        n += 1
        ok += bool(v)
    print("  %-34s %3d/%-3d = %5s%%" % (label, ok, n, round(ok / n * 100, 1) if n else "—"))
    return (ok, n)


# ---- 自校验 ----
ok, n = rate(recs, "全量（自校验，应 ≈19.0）")
assert abs(ok / n * 100 - 19.0) < 0.2, "自校验失败，脚本口径有误"
print()

print("=== V1 目标相对预测日收盘的方向（Δ 符号）===")
for s in ("sell", "buy", "hold"):
    sub = [r for r in recs if r.get("side") == s and r["date"] in di]
    up = sum(1 for r in sub if float(r["price"]) > close[di[r["date"]]])
    dn = sum(1 for r in sub if float(r["price"]) < close[di[r["date"]]])
    print("  %-5s 共 %3d | 目标高于当日收盘 %3d (%.0f%%) | 低于 %2d"
          % (s, len(sub), up, up / len(sub) * 100 if sub else 0, dn))

print("\n=== V2 目标距离分桶（|Δ| 相对收盘）vs 命中率 ===")
EDGES = [(0, 0.005), (0.005, 0.03), (0.03, 0.10), (0.10, 0.20), (0.20, 0.35), (0.35, 9)]
for a, b in EDGES:
    sub = []
    for r in recs:
        if r["date"] not in di:
            continue
        c0 = close[di[r["date"]]]
        g = abs(float(r["price"]) - c0) / c0
        if a <= g < b:
            sub.append(r)
    rate(sub, "[%5.1f%%, %5.1f%%)" % (a * 100, b * 100))

print("\n=== V3 反事实 placebo：目标 = 现价×(1+δ)，δ 取真实目标距离分位 ===")
import statistics
deltas = []
for r in recs:
    if r["date"] in di:
        c0 = close[di[r["date"]]]
        deltas.append(abs(float(r["price"]) - c0) / c0)
qs = statistics.quantiles(deltas, n=10)
print("  真实目标距离分位：p10=%.1f%% p50=%.1f%% p90=%.1f%%"
      % (qs[0] * 100, qs[4] * 100, qs[8] * 100))
for q, lab in ((qs[0], "p10"), (qs[4], "p50"), (qs[8], "p90")):
    rate(recs, "placebo 全部目标压到 +%.1f%%" % (q * 100),
         override=lambda r, q=q: close[di[r["date"]]] * (1 + q))

print("\n=== V4 sell 侧单独反事实：目标压到 +3% 会怎样 ===")
sub = [r for r in recs if r.get("side") == "sell"]
rate(sub, "sell 真实目标（4493 等）", )
rate(sub, "sell 目标改为 当日收盘×1.03",
     override=lambda r: close[di[r["date"]]] * 1.03)
rate(sub, "sell 目标改为 当日收盘×1.10",
     override=lambda r: close[di[r["date"]]] * 1.10)
