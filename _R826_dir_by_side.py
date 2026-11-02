#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R826 独立诊断：方向推进率按 side / 时间段 / 类别拆分（不 import 生产模块）。

目的：回答"修正（R825 state 改为 danger）之后，预测还在错吗？错在哪一侧？"
口径严格复刻 backtest.py:337-347：
  Δ = px - close0；窗 = max(HORIZON=30, expDays) 个交易日；朝 Δ 推进 >= 50%|Δ| 记达成。
自校验：先跑全量，必须复现生产 dirRealizedHitRate=19.0 / 样本可判 458 条，否则本脚本作废。
"""
import csv
import json
from collections import Counter, defaultdict

HORIZON = 30
CSV = "data/sh000001.csv"
LOG = "data/predictions_log.jsonl"
PROD = "data/backtest.json"

rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
dates = [r["date"] for r in rows]
high = [float(r["high"]) for r in rows]
low = [float(r["low"]) for r in rows]
close = [float(r["close"]) for r in rows]
di = {d: i for i, d in enumerate(dates)}

recs = [json.loads(l) for l in open(LOG, encoding="utf-8") if l.strip()]


def judge(rec):
    i0 = di.get(rec["date"])
    if i0 is None:
        return None
    px, c0 = float(rec["price"]), close[i0]
    hz = max(HORIZON, int(rec.get("expDays") or HORIZON))
    j1 = min(len(dates), i0 + 1 + hz)
    d = px - c0
    if j1 <= i0 + 1 or abs(d) <= 0.005 * c0:
        return None
    w_hi = max(high[i0 + 1:j1])
    w_lo = min(low[i0 + 1:j1])
    return (w_hi >= c0 + 0.5 * d) if d > 0 else (w_lo <= c0 + 0.5 * d)


def stat(sub, label):
    ok = n = 0
    for r in sub:
        v = judge(r)
        if v is None:
            continue
        n += 1
        ok += bool(v)
    pct = round(ok / n * 100, 1) if n else None
    print("  %-28s %4d/%-4d = %5s%%" % (label, ok, n, pct))
    return ok, n


print("=== A 段自校验（必须复现生产值，否则作废）===")
ok, n = stat(recs, "全量")
prod = json.load(open(PROD, encoding="utf-8"))
pv = prod.get("dirRealizedHitRate")
print("  生产 dirRealizedHitRate =", pv, "| 复算 =", round(ok / n * 100, 1) if n else None)
okb, nb = stat([r for r in recs if r.get("baseCase", True)], "全量·仅 baseCase")
print("  生产(含全部)对比：复算全量 %.1f" % (ok / n * 100))
assert abs(ok / n * 100 - pv) < 0.15, "自校验失败：复算与生产不一致，脚本口径有误"
print("  ✓ 自校验通过\n")

print("=== B 按 side 拆分（全量）===")
for s in ("sell", "buy", "hold"):
    stat([r for r in recs if r.get("side") == s], s)

print("\n=== C 按时间段拆分（R825 修正上线前/后）===")
CUT = "2026-10-24"
for tag, flt in (("修正前 (<=10-23)", lambda r: r["date"] <= "2026-10-23"),
                 ("修正后 (>=10-26)", lambda r: r["date"] >= "2026-10-26")):
    sub = [r for r in recs if flt(r)]
    print(" [%s] 共 %d 条" % (tag, len(sub)))
    for s in ("sell", "buy", "hold"):
        stat([r for r in sub if r.get("side") == s], s)

print("\n=== D 修正后新增样本明细（逐条可判）===")
new = [r for r in recs if r["date"] >= "2026-10-26"]
agg = defaultdict(lambda: [0, 0, set(), set()])
for r in new:
    v = judge(r)
    a = agg[(r.get("cat"), r.get("key"), r.get("side"))]
    a[2].add(round(float(r["price"]), 2))
    if v is not None:
        a[1] += 1
        a[0] += bool(v)
    a[3].add(r["date"])
for k, a in sorted(agg.items(), key=lambda x: -x[1][1]):
    print("  %-10s %-10s %-5s 目标价=%s  可判 %d 条 命中 %d  观测日 %d 个"
          % (k[0], k[1], k[2], sorted(a[2]), a[1], a[0], len(a[3])))

print("\n=== E 目标价 vs 现价 的方向合理性（sell 目标必须高于现价）===")
last = close[-1]
print("  最新收盘 =", last)
bad = 0
for r in recs:
    px = float(r["price"])
    side = r.get("side")
    if side == "sell" and px < last:
        bad += 1
    if side == "buy" and px > last:
        bad += 1
print("  方向与现价矛盾的记录数 =", bad, "/", len(recs))
for r in [r for r in recs if r.get("side") == "sell" and float(r["price"]) < last][:5]:
    print("    sell 目标低于现价:", r["date"], r["key"], r["price"])
