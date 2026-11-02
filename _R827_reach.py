#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R827 目标可达性诊断 v2（逐条独立复算，不 import 生产模块）

v1 教训：按「距离下界」分桶会把 3%~10% 全收进一个桶、桶内均值 ~9%，
读表时误以为"3% 目标在 30 日内只有 7.6% 可达"。必须逐条用各自的 (a, w) 算。

本 v2 核心命题：**方向命中率低，是判据错了，还是目标定得比市场能走的更远？**
做法：对每条记录 i，用其自身幅度 a_i 与观察窗 w_i，在全历史上统计
「w_i 个交易日内朝同方向走过 ≥ a_i 的比例」= rr_i（成熟度对齐：只取 i+w<=n-1 的起点）。
然后按 rr_i 分档，看各档的实际达成率。

自校验：① 三项总指标对表生产 data.js ② 分档人数合计 == totalDirEvaluated
"""
import csv
import json
import sys

import numpy as np

CSV = "data/sh000001.csv"
DATA = "data/data.js"
LOG = "data/predictions_log.jsonl"

rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
dates = [r["date"] for r in rows]
n = len(dates)
HI = np.array([float(r["high"]) for r in rows])
LO = np.array([float(r["low"]) for r in rows])
CL = np.array([float(r["close"]) for r in rows])
di = {d: i for i, d in enumerate(dates)}
print("CSV: %d 行  起 %s  止 %s  末收盘 %.2f" % (n, dates[0], dates[-1], CL[-1]))

recs = [json.loads(l) for l in open(LOG, encoding="utf-8") if l.strip()]
print("日志: %d 条  起 %s  止 %s" % (len(recs), recs[0]["date"], recs[-1]["date"]))

# ---------- 前向极值表（成熟度对齐：i+w <= n-1）----------
_fwd_max, _fwd_min = {}, {}


def fwd_max(w):
    if w not in _fwd_max:
        # arr[i] = max(HI[i+1 : i+1+w])；i+w<=n-1 ⇒ i<=n-1-w
        out = np.full(n, np.nan)
        for i in range(0, n - w):
            out[i] = HI[i + 1: i + 1 + w].max()
        _fwd_max[w] = out
    return _fwd_max[w]


def fwd_min(w):
    if w not in _fwd_min:
        out = np.full(n, np.nan)
        for i in range(0, n - w):
            out[i] = LO[i + 1: i + 1 + w].min()
        _fwd_min[w] = out
    return _fwd_min[w]


_reach_cache = {}


def reach_rate(up, a, w):
    """全历史：w 交易日内朝 up 方向走过 >= a 的比例（成熟度对齐）"""
    key = (bool(up), round(float(a), 5), int(w))
    if key in _reach_cache:
        return _reach_cache[key]
    arr = fwd_max(w) if up else fwd_min(w)
    need = CL * (1 + a) if up else CL * (1 - a)
    ok = (arr >= need) if up else (arr <= need)
    m = ~np.isnan(arr)
    res = float(ok[m].mean()) if m.any() else None
    _reach_cache[key] = res
    return res


# ---------- 判据复刻（与 backtest.py 逐字一致）----------
def judge(r):
    i0 = di.get(r["date"])
    if i0 is None:
        return None, False, None, None
    px = float(r["price"])
    c0 = CL[i0]
    w = max(30, int(r.get("expDays") or 30))
    j1 = min(n, i0 + 1 + w)
    matured = ((n - 1) - i0) >= w
    d = px - c0
    if j1 <= i0 + 1 or abs(d) <= 0.005 * c0:
        return None, matured, None, w
    ok = (HI[i0 + 1:j1].max() >= c0 + 0.5 * d) if d > 0 else (LO[i0 + 1:j1].min() <= c0 + 0.5 * d)
    return bool(ok), matured, d / c0, w


# ---------- 自校验 ----------
s = open(DATA, encoding="utf-8").read()
D = json.loads(s[s.index("{"): s.rindex("}") + 1])
bt = D["backtest"]
tot_n = tot_h = mat_n = mat_h = pend = early = 0
for r in recs:
    ok, matured, g, w = judge(r)
    if ok is None:
        continue
    tot_n += 1
    tot_h += ok
    if matured:
        mat_n += 1
        mat_h += ok
    elif ok:
        early += 1
    else:
        pend += 1


def lap(h, nn):
    return round((h + 1.0) / (nn + 2.0) * 100, 1) if nn else None


checks = [
    ("全窗口口径", lap(tot_h, tot_n), bt.get("dirRealizedHitRate")),
    ("成熟口径", lap(mat_h, mat_n), bt.get("dirRealizedHitRateMatured")),
    ("pending", pend, bt.get("dirPendingCount")),
    ("earlyHit", early, bt.get("dirEarlyHitCount")),
    ("totalEval", tot_n, bt.get("totalDirEvaluated")),
]
print("\n[自校验] 与生产对表")
bad = 0
for name, mine, prod in checks:
    ok = (mine == prod) if isinstance(mine, int) else (prod is not None and abs(mine - prod) < 0.15)
    bad += 0 if ok else 1
    print("  %-14s 复算=%-8s 生产=%-8s %s" % (name, mine, prod, "OK" if ok else "!!MISMATCH!!"))
if bad:
    print("自校验失败 %d 项 ⇒ 读数作废" % bad)
    sys.exit(2)

# ---------- 核心：逐条 rr + 按 rr 分档 ----------
BANDS = [(0.0, 0.05, "<5%"), (0.05, 0.15, "5~15%"), (0.15, 0.35, "15~35%"),
         (0.35, 0.60, "35~60%"), (0.60, 1.01, ">=60%")]


def band_of(rr):
    for lo, hi, lbl in BANDS:
        if lo <= rr < hi:
            return lbl
    return BANDS[-1][2]


agg = {b[2]: [0, 0] for b in BANDS}          # 成熟样本 [hits, n]
agg_all = {b[2]: [0, 0] for b in BANDS}      # 全部可判样本（含 pending/early）
rows_out = []
for r in recs:
    ok, matured, g, w = judge(r)
    if ok is None:
        continue
    rr = reach_rate(g > 0, abs(g), w)
    if rr is None:
        continue
    b = band_of(rr)
    agg_all[b][1] += 1
    if matured:
        agg[b][0] += ok
        agg[b][1] += 1
    rows_out.append((r["side"], r["date"], g, w, rr, ok, matured))

print("\n[核心] 按「目标在自身观察窗内的历史可达比例 rr」分档")
print("  可达比例    成熟样本(命中/总)   命中率   全部可判  需要日均速度中位")
for lo, hi, lbl in BANDS:
    h, nn = agg[lbl]
    if nn == 0:
        continue
    sub = [x for x in rows_out if band_of(x[4]) == lbl]
    dr = np.median([(1 + abs(x[2])) ** (1.0 / max(1, x[3])) - 1 for x in sub])
    print("  %-10s  %3d/%-3d             %5.1f%%    %3d       %.3f%%"
          % (lbl, h, nn, h / nn * 100 if nn else 0, agg_all[lbl][1], dr * 100))

# 合计校验
sum_mat = sum(agg[b[2]][1] for b in BANDS)
print("\n  [校验] 分档成熟样本合计 %d vs 生产 dirMaturedEvaluated %s  %s"
      % (sum_mat, bt.get("dirMaturedEvaluated"),
         "OK" if sum_mat == bt.get("dirMaturedEvaluated") else "!!MISMATCH!!"))

# ---------- 侧 × 可达性 ----------
print("\n[侧 × 可达性] 成熟样本")
from collections import defaultdict
side_agg = defaultdict(lambda: defaultdict(lambda: [0, 0]))
for side, dt, g, w, rr, ok, matured in rows_out:
    if matured:
        a = side_agg[str(side)][band_of(rr)]
        a[0] += ok
        a[1] += 1
print("  侧       " + "  ".join("%-10s" % b[2] for b in BANDS))
for side in sorted(side_agg):
    cells = []
    for b in BANDS:
        h, nn = side_agg[side][b[2]]
        cells.append("%-10s" % ("%d/%d" % (h, nn) if nn else "—"))
    print("  %-8s " % side + "  ".join(cells))

# ---------- 反事实：当前五个目标 ----------
print("\n[反事实] 线上 targets 五个价位（当前 expDays 字段缺失 ⇒ 一律按 30 日）")
last_close = D["lastClose"]
for t in D.get("targets", []):
    px = t.get("price")
    if px is None:
        continue
    w = int(t.get("expDays") or 30)
    a = px / last_close - 1
    rr = reach_rate(a > 0, abs(a), w)
    daily = (1 + abs(a)) ** (1.0 / max(1, w)) - 1
    verdict = "算术不可达" if (rr or 0) < 0.10 else ("偏紧" if (rr or 0) < 0.35 else "可达")
    print("  %-22s %8.2f  %+6.2f%%  窗%3d日  日均 %6.3f%%  可达 %.2f%%  ⇒ %s"
          % (t.get("name"), px, a * 100, w, daily * 100, (rr or 0) * 100, verdict))

print("\n[关键] 若窗口取子浪ⅴ 的 74 日（时间占比分配），同一目标的可达性：")
for t in D.get("targets", []):
    px = t.get("price")
    a = px / last_close - 1
    for w in (30, 60, 74, 120, 180):
        rr = reach_rate(a > 0, abs(a), w)
        if w == 30:
            print("  %-22s" % t.get("name"), end="")
        print("  w=%3d:%.1f%%" % (w, (rr or 0) * 100), end="")
    print()

print("\n自校验通过，读数有效。")
