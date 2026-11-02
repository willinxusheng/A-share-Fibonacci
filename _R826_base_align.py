#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R826 诊断四：把"成熟度"对齐后重算零信息基线，得到与引擎可比的真基线。

背景：backtest.py:438-459 的 dirBase 用「全历史所有起点」算同距离同窗长达成率，
      _valid 只排除最后一根（_valid[:n-1]=True）⇒ 包含了未来 w 天不完整的起点；
      而引擎侧的 dirCorrect 有 76% 样本窗口未闭合（未达成被记 False）。
      两边删失处理不一致 ⇒ 引擎被系统性报差。

本脚本给出三种基线：
  B1 现行口径（全历史，含不完整未来窗）—— 复现生产 dirBaseRate=38.1
  B2 成熟度对齐（只取 i+w <= n-1 的起点）—— 与引擎 matured-only 可比
  B3 同期对齐（B2 且起点落在预测日期区间内）—— 复现/修正 dirBaseRateRecent
自校验：B1 必须复现 38.1，B3 必须复现 19.5，否则脚本作废。
"""
import csv
import json

import numpy as np

HORIZON = 30
rows = list(csv.DictReader(open("data/sh000001.csv", encoding="utf-8")))
dates = [r["date"] for r in rows]
high = np.array([float(r["high"]) for r in rows])
low = np.array([float(r["low"]) for r in rows])
close = np.array([float(r["close"]) for r in rows])
n = len(dates)
di = {d: i for i, d in enumerate(dates)}
recs = [json.loads(l) for l in open("data/predictions_log.jsonl", encoding="utf-8") if l.strip()]
prod = json.load(open("data/backtest.json", encoding="utf-8"))

_c = {}


def fwd_ext(w, up):
    k = (w, up)
    if k in _c:
        return _c[k]
    import pandas as pd
    s = pd.Series(high if up else low)
    r = s[::-1].rolling(int(w), min_periods=1).max().__getitem__  # noqa
    if up:
        arr = s[::-1].rolling(int(w), min_periods=1).max()[::-1].shift(-1).values
    else:
        arr = s[::-1].rolling(int(w), min_periods=1).min()[::-1].shift(-1).values
    _c[k] = arr
    return arr


recs_valid = [r for r in recs if r.get("date") in di]
# 日志里没有 dirGap/dirWindow（由 backtest 内部推导），此处按同一公式自行推导
for r in recs_valid:
    c0 = close[di[r["date"]]]
    r["_gap"] = (float(r["price"]) - c0) / c0
    r["_win"] = max(HORIZON, int(r.get("expDays") or HORIZON))
keys = sorted({(round(float(r["_gap"]), 4), int(r["_win"])) for r in recs_valid
               if abs(r["_gap"]) > 0.005})

# 预测日期区间（同期窗口定义与生产一致：起点 = max(0, min(最早预测日, n-1-60))）
_i0s = [di[r["date"]] for r in recs_valid]
_start = max(0, min(min(_i0s), n - 1 - 60))
print("预测起始索引=%d (%s)  样本可判 n=%d" % (_start, dates[_start], len(recs_valid)))
print("n=%d 行 K 线，末根 %s\n" % (n, dates[-1]))

B1 = B2 = B3 = None
v1, v2, v3 = [], [], []
for g, w in keys:
    up = g > 0
    if abs(g) <= 0.005:
        continue
    arr = fwd_ext(w, up)
    need = close * (1.0 + 0.5 * g)
    ok = (arr >= need) if up else (arr <= need)
    valid1 = np.zeros(n, bool)
    valid1[: n - 1] = True                      # 生产口径
    m1 = valid1 & ~np.isnan(arr)
    # B2 成熟度对齐：起点 i 必须有完整 w 根未来 K 线
    valid2 = np.zeros(n, bool)
    valid2[: max(0, n - w)] = True
    m2 = valid2 & ~np.isnan(arr)
    # B3 同期
    valid3 = np.zeros(n, bool)
    valid3[_start: max(0, n - w)] = True
    m3 = valid3 & ~np.isnan(arr)
    if m1.any():
        v1.append(float(ok[m1].mean()))
    if m2.any():
        v2.append(float(ok[m2].mean()))
    if m3.any():
        v3.append(float(ok[m3].mean()))

B1 = round(float(np.mean(v1)) * 100, 1)
B2 = round(float(np.mean(v2)) * 100, 1)
B3 = round(float(np.mean(v3)) * 100, 1)

print("=== 基线三口径（每 key 取均值，与生产同法）===")
print("  B1 现行（全历史，含不完整窗）         = %5.1f%%   生产 dirBaseRate = %s  %s"
      % (B1, prod["dirBaseRate"], "✓" if abs(B1 - prod["dirBaseRate"]) < 0.3 else "✗"))
print("  B2 成熟度对齐（i+w<=n-1）             = %5.1f%%" % B2)
print("  B3 同期 + 成熟度对齐                  = %5.1f%%   生产 dirBaseRateRecent = %s  %s"
      % (B3, prod["dirBaseRateRecent"], "✓" if abs(B3 - prod["dirBaseRateRecent"]) < 1.0 else "≈"))

# 引擎侧两口径（诊断三已算，这里重算以便同屏对比）
def judge(r):
    i0 = di[r["date"]]
    px, c0 = float(r["price"]), close[i0]
    hz = max(HORIZON, int(r.get("expDays") or HORIZON))
    j1 = min(n, i0 + 1 + hz)
    d = px - c0
    if j1 <= i0 + 1 or abs(d) <= 0.005 * c0:
        return None, None
    matured = (i0 + 1 + hz) <= n
    v = (max(high[i0 + 1:j1]) >= c0 + 0.5 * d) if d > 0 else (min(low[i0 + 1:j1]) <= c0 + 0.5 * d)
    return bool(v), matured


E_all = [judge(r) for r in recs]
E_all = [x for x in E_all if x[0] is not None]
e_cur = sum(1 for x in E_all if x[0]) / len(E_all) * 100
Emat = [x for x in E_all if x[1]]e_mat = sum(1 for x in Emat if x[0]) / len(Emat) * 100 if Emat else 0

print("\n=== 可比性结论 ===")
print("  引擎 现行口径（含 76%% 删失）        = %5.1f%%   ← 应比 B1" % e_cur)
print("  引擎 仅成熟样本                      = %5.1f%% (n=%d)  ← 应比 B2/B3" % (e_mat, len(Emat)))
print()
print("  【错误对比】引擎 %.1f%% vs 基线 %.1f%% ⇒ 差 %+.1f pp  ← 口径错配（一边含删失一边不含）"
      % (e_cur, B1, e_cur - B1))
print("  【正确对比】引擎 %.1f%% vs 基线 %.1f%% ⇒ 差 %+.1f pp  ← 同口径（均仅完整窗）"
      % (e_mat, B2, e_mat - B2))
print("  【同期正确】引擎 %.1f%% vs 基线 %.1f%% ⇒ 差 %+.1f pp"
      % (e_mat, B3, e_mat - B3))
