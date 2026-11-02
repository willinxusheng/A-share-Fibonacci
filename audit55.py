#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit55 · 回测口径可比性门禁（R826 建立，第 12 道）

守的是「指标之间能否互相比较」这件事本身，而不是某个数字对不对：

背景（2026-11-02 深度回测实证）：
  ① 方向推进率把「观察窗未闭合且尚未走完 50% 目标距离」记为未做到 ⇒ 删失偏差，系统性偏低。
     实测 507 条可判样本中 343 条(68%)属此列，全窗口口径 18.7% vs 仅成熟口径 57.2%（差 38.5pp）。
  ② 零信息基线用「全历史所有起点」且只排除最后一根 ⇒ 含大量未来窗残缺起点。
     观察窗长 w 普遍 > 同期区间 60 天时，同期内【没有任何】完整未来窗（实测 w>=61 时完整起点数=0），
     同期基线被压到 19.5%，恰与同样被删失压低的引擎值接近 ⇒ 得出"引擎≈零信息基线（无预测价值）"的伪结论。
     同口径重算后同期基线 32.2% vs 引擎成熟口径 57.2%。
  ③ 一旦有人把三态改回两态、或把基线退回旧口径，指标会静默退回"看似自洽但不可比"的状态——本门禁拦这个。

断言（全部从 data/ 独立复算，不 import 生产 backtest 模块）：
  A1 三态恒等式：dirPendingCount + dirMaturedEvaluated == totalDirEvaluated
  A2 双口径并存且显著不同（差 > 5pp）；若相等 ⇒ 三态化没生效
  A3 基线口径元数据自洽：validKeys + skippedKeys == totalKeys，且 validKeys > 0
  A4 独立复算 dirRealizedHitRateMatured 与生产一致（tol 0.3pp）
  A5 独立复算 dirBaseRateRecent（成熟对齐）与生产一致（tol 0.3pp）
  A6 根因断言：所有 w > 同期区间长度的 key 必须被计入 skippedKeys（否则说明对齐没生效）
退出码 0 = 全通过。
"""
import csv
import json
import os
import sys

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(BASE, "data", "sh000001.csv")
LOG = os.path.join(BASE, "data", "predictions_log.jsonl")
BT = os.path.join(BASE, "data", "backtest.json")
HORIZON = 30

fail = []


def chk(ok, tag, detail):
    print(("  [PASS] " if ok else "  [FAIL] ") + tag + " :: " + detail)
    if not ok:
        fail.append(tag)


rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
dates = [r["date"] for r in rows]
high = np.array([float(r["high"]) for r in rows])
low = np.array([float(r["low"]) for r in rows])
close = np.array([float(r["close"]) for r in rows])
n = len(dates)
di = {d: i for i, d in enumerate(dates)}
recs = [json.loads(l) for l in open(LOG, encoding="utf-8") if l.strip()]
bt = json.load(open(BT, encoding="utf-8"))

# ---- 独立复算 ----
def judge(r):
    i0 = di.get(r["date"])
    if i0 is None:
        return None, None
    px, c0 = float(r["price"]), close[i0]
    hz = max(HORIZON, int(r.get("expDays") or HORIZON))
    j1 = min(n, i0 + 1 + hz)
    d = px - c0
    if j1 <= i0 + 1 or abs(d) <= 0.005 * c0:
        return None, None
    matured = (i0 + 1 + hz) <= n
    v = (max(high[i0 + 1:j1]) >= c0 + 0.5 * d) if d > 0 else (min(low[i0 + 1:j1]) <= c0 + 0.5 * d)
    return bool(v), matured


mat_h = mat_n = pend = early = all_n = 0
gaps = {}
for r in recs:
    v, m = judge(r)
    if v is None:
        continue
    all_n += 1
    if m:
        mat_n += 1
        mat_h += bool(v)
    elif v is True:
        early += 1          # 未成熟但已达成：确定事实，不进任何比率（否则近期样本只留成功者）
    else:
        pend += 1
    c0 = close[di[r["date"]]]
    g = (float(r["price"]) - c0) / c0
    w = max(HORIZON, int(r.get("expDays") or HORIZON))
    if abs(g) > 0.005:
        gaps[(round(g, 4), w)] = True

_rate_mat = round((mat_h + 1.0) / (mat_n + 2.0) * 100, 1) if mat_n else None

# 同期区间（与生产一致：起点 = max(0, min(最早预测日, n-1-60))）
_i0s = [di[r["date"]] for r in recs if r["date"] in di]
_start = max(0, min(min(_i0s) if _i0s else n - 1, n - 1 - 60))
span = (n - 1) - _start


def fwd_arr(w, up):
    import pandas as pd
    s = pd.Series(high if up else low)
    if up:
        return s[::-1].rolling(int(w), min_periods=1).max()[::-1].shift(-1).values
    return s[::-1].rolling(int(w), min_periods=1).min()[::-1].shift(-1).values


vals = []
skipped = 0
long_keys_unskipped = []
for (g, w), _ in gaps.items():
    up = g > 0
    arr = fwd_arr(w, up)
    need = close * (1.0 + 0.5 * g)
    ok = (arr >= need) if up else (arr <= need)
    full = np.zeros(n, dtype=bool)
    full[: max(0, n - w)] = True
    win = np.zeros(n, dtype=bool)
    win[_start: n - 1] = True
    m = full & win & ~np.isnan(arr)
    if m.any():
        vals.append(float(ok[m].mean()))
    else:
        skipped += 1
        if w <= span:
            long_keys_unskipped.append((g, w))
_base_recent = round(float(np.mean(vals)) * 100, 1) if vals else None

print("=== audit55 · 回测口径可比性 ===")
print("  可判样本 %d ｜ 成熟对齐可判 %d ｜ 删失(pending) %d ｜ 同期区间 %s..%s (%d 天)"
      % (all_n, mat_n, pend, dates[_start], dates[-1], span))

# A1 三态恒等式（对称成熟 + 删失 + 早达成 = 全窗口可判）
_tot = bt.get("totalDirEvaluated")
_mn = bt.get("dirMaturedEvaluated")
_pn = bt.get("dirPendingCount")
_en = bt.get("dirEarlyHitCount")
chk(_tot is not None and _mn is not None and _pn is not None and _en is not None
    and (_pn + _mn + _en) == _tot,
    "A1 三态恒等式(对称)",
    "pending(%s) + matured(%s) + earlyHit(%s) = %s ｜ totalDirEvaluated = %s"
    % (_pn, _mn, _en, (_pn or 0) + (_mn or 0) + (_en or 0), _tot))

# A1b 与独立复算一致
chk(_pn == pend and _mn == mat_n and _en == early,
    "A1b 独立复算一致",
    "复算 pending=%d matured=%d early=%d ｜ 生产 %s/%s/%s" % (pend, mat_n, early, _pn, _mn, _en))

# A2 双口径并存且显著不同
_ra = bt.get("dirRealizedHitRate")
_rm = bt.get("dirRealizedHitRateMatured")
chk(_ra is not None and _rm is not None and abs(_rm - _ra) > 5.0,
    "A2 双口径显著不同",
    "全窗口=%s 仅成熟=%s 差=%s pp（须 >5；相等说明三态化失效）"
    % (_ra, _rm, round(abs((_rm or 0) - (_ra or 0)), 1)))

# A3 基线元数据自洽
_vk = bt.get("dirBaseRecentValidKeys")
_sk = bt.get("dirBaseRecentSkippedKeys")
_tk = bt.get("dirBaseTotalKeys")
chk(_vk is not None and _sk is not None and _tk is not None and (_vk + _sk) == _tk and _vk > 0,
    "A3 基线元数据自洽",
    "valid(%s) + skipped(%s) = %s ｜ totalKeys=%s" % (_vk, _sk, (_vk or 0) + (_sk or 0), _tk))

# A4 独立复算成熟口径
chk(_rm is not None and _rate_mat is not None and abs(_rm - _rate_mat) <= 0.3,
    "A4 复算成熟口径一致", "复算=%s 生产=%s" % (_rate_mat, _rm))

# A5 独立复算同期基线
_b1 = bt.get("dirBaseRateRecent")
chk(_b1 is not None and _base_recent is not None and abs(_b1 - _base_recent) <= 0.3,
    "A5 复算同期基线一致", "复算=%s 生产=%s" % (_base_recent, _b1))

# A6 根因断言：所有 w > 同期跨度的 key 在同期内必然没有完整未来窗，因此生产侧的
#    skippedKeys 必须 **>= 这类 key 的数量**。若生产退回旧口径（不剔除，直接拿残缺窗采样），
#    skippedKeys 会是 0/缺失 ⇒ 此处失败。
#    ⚠ 早先版本写成"我自己复算一遍剔除逻辑、断言复算结果里没有漏网的"——那是**恒真断言**
#    （我复算的本来就是剔除后的结果），抓不到生产实现退化。现改为与生产上报的计数对账。
_long_keys = sum(1 for (_g, _w) in gaps if _w > span)
chk(_sk is not None and _long_keys > 0 and _sk >= _long_keys,
    "A6 长窗 key 全被剔除",
    "同期跨度 %d 天 ｜ w>%d 的 key 数 = %d ｜ 生产 skippedKeys = %s（须 >= 前者）"
    % (span, span, _long_keys, _sk))

# A7 旧口径字段保留（供对照，防止有人直接删掉掩盖）
chk(bt.get("dirBaseRateAllWindow") is not None,
    "A7 旧口径保留对照", "dirBaseRateAllWindow=%s（修正前口径，仅供对照）" % bt.get("dirBaseRateAllWindow"))

print()
if fail:
    print("audit55 结论：%d 项失败 → %s" % (len(fail), ", ".join(fail)))
    sys.exit(1)
print("audit55 结论：回测口径可比性 7/7 通过 ✓")
