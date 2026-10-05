# -*- coding: utf-8 -*-
"""R822 独立复算器（trust-but-verify）：不 import 生产模块，逐字重实现 backtest.evaluate 的判据，
用于（a）核对看板自报指标；（b）检验判据的判别力（是否退化为恒真）。

R822（2026-10-05）据此定位出四个问题：
  ① 旧方向判据与目标价无关（同日同 side 取值恒等），零信息基线 94.0%/99.0% → 无判别力；
  ② band 触达率 97.6% 系删失偏差（分母只含"已解决"样本）→ 全量口径 45.3%；
  ③ 精确命中率含 61.5% 重言式样本（目标价在预测当日已被自身收盘满足）→ 去平凡 54.4%；
  ④ 卖① expDays 双口径（归档/_empirical_rates 63 vs 展示 59）→ 同目标两个概率。

下方括号内"看板自报"均为 2026-09-30 数据下的**修复前**旧值，用于对照，非当前值。
只读：不写 data/。
"""
import json
import math
import os

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "data", "predictions_log.jsonl")
CSV = os.path.join(BASE, "data", "sh000001.csv")
HORIZON = 30

df = pd.read_csv(CSV, parse_dates=["date"]).set_index("date")
dates = [d.strftime("%Y-%m-%d") for d in df.index]
close = df["close"].values
high = df["high"].values
low = df["low"].values
idx = list(df.index)

# ---- 复刻 _vol_scale_at / _vol_for（含 R123 前视泄漏修复语义）----
_ret = np.log(df["close"] / df["close"].shift(1))
_hv20 = _ret.rolling(20).std() * np.sqrt(244) * 100
_VW = [20, 60, 120, 250]
_vol_by_w = {w: _ret.rolling(w).std().values for w in _VW}


def vol_scale_at(i):
    if i < 0 or i >= len(_hv20) or pd.isna(_hv20.iloc[i]):
        return 1.0
    pct = float((_hv20.iloc[: i + 1].dropna() < _hv20.iloc[i]).mean()) * 100
    return 1.15 if pct >= 66 else (1.0 if pct >= 33 else 0.88)


def vol_for(exp, i):
    w = min(_VW[-1], max(_VW[0], float(exp)))
    if w <= _VW[0]:
        return _vol_by_w[_VW[0]][i]
    if w >= _VW[-1]:
        return _vol_by_w[_VW[-1]][i]
    for a, b in zip(_VW, _VW[1:]):
        if a <= w <= b:
            t = (w - a) / (b - a)
            return _vol_by_w[a][i] * (1 - t) + _vol_by_w[b][i] * t
    return _vol_by_w[_VW[-1]][i]


recs = [json.loads(l) for l in open(LOG, encoding="utf-8") if l.strip()]
out = []
for r in recs:
    if not r.get("date"):
        continue
    i0 = dates.index(r["date"])
    exp = r.get("expDays") or HORIZON
    hz = max(HORIZON, int(exp))
    matured = (i0 + hz) <= (len(dates) - 1)
    c0 = float(close[i0])
    px = float(r["price"])
    v = vol_for(exp, i0)
    frac = 0.0
    if v is not None and not math.isnan(v) and v > 0:
        frac = min(v * math.sqrt(exp) * vol_scale_at(i0), 0.235)
    fut_hi = float(high[i0 + 1:].max()) if i0 + 1 < len(high) else None
    fut_lo = float(low[i0 + 1:].min()) if i0 + 1 < len(low) else None
    if r["side"] == "buy":
        thr = px * (1.0 + frac)
        hit = fut_lo is not None and fut_lo <= thr
        prec = fut_lo is not None and fut_lo <= px
        dir_ok = fut_lo is not None and fut_lo <= c0
        b1 = fut_lo is not None and fut_lo <= px * (1.0 + frac)  # same
    else:
        thr = px * (1.0 - frac)
        hit = fut_hi is not None and fut_hi >= thr
        prec = fut_hi is not None and fut_hi >= px
        dir_ok = fut_hi is not None and fut_hi >= c0
    # 恒真检测：阈值在【预测当日的收盘/最高最低】下已满足 → 无需任何未来走势即"命中"
    triv = (thr <= c0) if r["side"] == "sell" else (thr >= c0)
    out.append(dict(date=r["date"], key=r["key"], cat=r["cat"], side=r["side"],
                    px=px, c0=c0, exp=exp, frac=frac, thr=thr, hit=hit, prec=prec,
                    dir_ok=dir_ok, matured=matured, triv=triv,
                    gap=px / c0 - 1.0))

ev = [o for o in out if o["hit"]]
print("=== 独立复算（自实现判据）===")
print("总记录 %d | 已评估(命中) %d | 成熟 %d" % (len(out), len(ev), sum(o["matured"] for o in out)))
print("band 触达率 = %.1f%%   (看板自报 97.6%%)" % (100.0 * len(ev) / len(out)))
d = [o for o in out if o["dir_ok"]]
print("方向准确率 = %.1f%%   (看板自报 93.8%%)" % (100.0 * len(d) / len(out)))
pm = [o for o in out if o["matured"]]
pp = [o for o in pm if o["prec"]]
print("精确命中率(成熟样本 %d) = %.1f%%   (看板自报 75.2%%)" %
      (len(pm), 100.0 * len(pp) / len(pm) if pm else float("nan")))

print()
print("=== 判据判别力检验 ===")
triv = [o for o in out if o["triv"]]
print("① band 阈值在【预测当日】即已被满足(无需任何未来走势)的记录: %d/%d = %.1f%%" %
      (len(triv), len(out), 100.0 * len(triv) / len(out)))
fr = [o["frac"] for o in out]
print("② band 半宽 _frac: 中位 %.1f%%  最大 %.1f%%  触 0.235 上限占比 %.1f%%" %
      (100 * np.median(fr), 100 * max(fr), 100.0 * sum(1 for f in fr if f >= 0.2349) / len(fr)))

# 零信息基线：方向判据"未来最高 >= 今日收盘"(sell) 对【任意】日期的成立率
base_hi = 0
base_lo = 0
tot = 0
for i in range(len(dates) - 1):
    fh = high[i + 1:].max()
    fl = low[i + 1:].min()
    base_hi += (fh >= close[i])
    base_lo += (fl <= close[i])
    tot += 1
print("③ 零信息基线：随机挑一天，'未来最高≥当日收盘'成立率 %.1f%%；'未来最低≤当日收盘'成立率 %.1f%%" %
      (100.0 * base_hi / tot, 100.0 * base_lo / tot))

# 精确命中率零信息基线：任意一天，未来是否触及"当日收盘×(1±10%)"
for k in (0.05, 0.10, 0.20, 0.30):
    a = sum(1 for i in range(len(dates) - 1) if high[i + 1:].max() >= close[i] * (1 + k))
    b = sum(1 for i in range(len(dates) - 1) if low[i + 1:].min() <= close[i] * (1 - k))
    print("   零信息基线：未来触及 收盘%s%d%% 的概率 上行=%.1f%% 下行=%.1f%%" %
          ("+" if True else "", int(k * 100), 100.0 * a / tot, 100.0 * b / tot))

print()
print("=== 目标离现价的距离（gap = 目标/预测日收盘 - 1）===")
for k in sorted({(o["cat"], o["key"], o["side"]) for o in out}):
    g = [o["gap"] for o in out if (o["cat"], o["key"], o["side"]) == k]
    print("  %-38s gap 中位=%+7.2f%%  最新=%+7.2f%%" % (str(k), 100 * np.median(g), 100 * g[-1]))
