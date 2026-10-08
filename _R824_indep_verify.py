# -*- coding: utf-8 -*-
"""R824 独立复算器（trust-but-verify，当前口径）：不 import 生产模块，逐字重实现
backtest.evaluate / dir_baseline / episode_reps 的判据与口径，用于核对看板自报指标。

与 _R822_indep_verify.py 的区别：R822 版复现的是**旧判据**（dirCorrect 不含目标价、
band 无窗口截断），保留作历史对照；本版复算**当前生产口径**并断言与 data/backtest.json 一致。

复算项（口径逐字对照 backtest.py）：
  ① band 三口径 ——
     realizedHitRate    : 分子=hit，分母=【已解决集】 evaluated = hit ∪ (未命中且窗口已闭合)
     hitRateAll         : 分子=hit，分母=【全量】 elapsedDays>0（未闭合未命中计入分母）
     hitRateMaturedOnly : 成熟样本内命中率（对称、无删失偏差）
  ② 方向推进率 dirRealizedHitRate（R822 判据）：Δ=px−close0，窗 max(30,expDays) 内
     max/min 朝 Δ 推进 ≥ 50%·|Δ|
  ③ 零信息基线 dirBaseRate / dirBaseRateRecent（逐字重实现 dir_baseline 的滚动口径）
  ④ 精确命中率去平凡 preciseRealizedHitRateNonTrivial（剔除"预测当日即被自身收盘满足"）
  ⑤ 独立目标口径（episode_reps）：同一 (cat,key,目标价) 收敛为一次观测
只读 data/，不写任何文件。
"""
import json
import math
import os

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(BASE, "data", "predictions_log.jsonl")
CSV = os.path.join(BASE, "data", "sh000001.csv")
BTJSON = os.path.join(BASE, "data", "backtest.json")
HORIZON = 30
MIN_SAMPLE = 3

df = pd.read_csv(CSV, parse_dates=["date"]).set_index("date")
dates = [d.strftime("%Y-%m-%d") for d in df.index]
idx = list(df.index)
n = len(df)
close = df["close"].values

# ---- 逐字重实现 _vol_scale_at / _vol_for ----
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
DS = set(dates)
out = []
for r in recs:
    if not r.get("date") or r["date"] not in DS:
        continue
    o = dict(r)
    i0 = dates.index(r["date"])
    exp = r.get("expDays") or HORIZON
    hz = max(HORIZON, int(exp))
    matured = (i0 + hz) <= (n - 1)
    c0 = float(close[i0])
    px = float(r["price"])
    v = vol_for(exp, i0)
    frac = 0.0
    if v is not None and not math.isnan(v) and v > 0:
        frac = min(v * math.sqrt(exp) * vol_scale_at(i0), 0.235)
    fut = df.iloc[i0 + 1: n]                       # hit 用【全部未来】，不受 hz 截断
    if fut.empty:
        o.update(dict(evaluated=False, hit=False, preciseHit=None))
    else:
        if r["side"] == "buy":
            best = float(fut["low"].min())
            hit = best <= px * (1.0 + frac)
            preciseHit = best <= px
        else:
            best = float(fut["high"].max())
            hit = best >= px * (1.0 - frac)
            preciseHit = best >= px
        # evaluated = 命中 ∪ (未命中且窗口已闭合)  ← 这就是"已解决集"（删失偏差来源）
        evaluated = bool(hit or (i0 + hz <= n - 1))
        o.update(dict(evaluated=evaluated, hit=hit,
                      preciseHit=(True if preciseHit else (False if matured else None))))
    # ---- R822 方向判据：与生产一致，**无条件**写入 dirGap/dirWindow ----
    # 哪怕 fut.empty 的末日记录也要写：win 为空只令 dirCorrect=None，但 dirGap/dirWindow 仍参与
    # dir_baseline 的分组（生产实测 370/370 条均有 dirGap）。曾误置于 else 分支内 ⇒ 少 9 个分组、
    # 基线偏 0.1~0.2pp —— 独立复算器与生产"看似只差一点"的经典假绿陷阱。
    win = df.iloc[i0 + 1: min(n, i0 + 1 + hz)]
    delta = px - c0
    if win.empty or abs(delta) <= 0.005 * c0:
        dir_correct = None
    elif delta > 0:
        dir_correct = bool(float(win["high"].max()) >= c0 + 0.5 * delta)
    else:
        dir_correct = bool(float(win["low"].min()) <= c0 + 0.5 * delta)
    is_buy = (r["side"] == "buy")
    o.update(dict(matured=matured, dirCorrect=dir_correct, delta=delta, dirWindow=hz,
                  dirGap=round(delta / c0, 6), elapsedDays=(n - 1) - i0,
                  trivialPrecise=((px >= c0) if is_buy else (px <= c0)),
                  frac=frac, truncated=bool(fut.empty)))
    out.append(o)


def pct(k, d):   # Laplace 收缩，与生产一致
    return round((k + 1.0) / (d + 2.0) * 100, 1) if d else None


# ---- ① band 三口径 ----
ev = [o for o in out if o["evaluated"]]
hits = [o for o in ev if o["hit"]]
_with_fut = [o for o in out if (o.get("elapsedDays") or 0) > 0]
_all_hits = [o for o in _with_fut if o["hit"]]
_mat = [o for o in out if o["matured"]]
_mat_hits = [o for o in _mat if o["hit"]]
realized = pct(len(hits), len(ev))
hit_all = pct(len(_all_hits), len(_with_fut))
hit_mat = pct(len(_mat_hits), len(_mat)) if len(_mat) >= MIN_SAMPLE else None

# ---- ② 方向推进率 ----
d_eval = [o for o in out if o["dirCorrect"] is not None]
d_hit = [o for o in d_eval if o["dirCorrect"]]
dir_rate = pct(len(d_hit), len(d_eval))

# ---- ③ 零信息基线（逐字重实现 dir_baseline）----
_cl = df["close"].values
_hs = pd.Series(df["high"].values)
_ls = pd.Series(df["low"].values)
_cache = {}


def _fwd_max(w):
    k = ("hi", int(w))
    if k not in _cache:
        _cache[k] = _hs[::-1].rolling(int(w), min_periods=1).max()[::-1].shift(-1).values
    return _cache[k]


def _fwd_min(w):
    k = ("lo", int(w))
    if k not in _cache:
        _cache[k] = _ls[::-1].rolling(int(w), min_periods=1).min()[::-1].shift(-1).values
    return _cache[k]


_valid = np.zeros(n, dtype=bool)
_valid[: n - 1] = True
per, vals, recent_vals = {}, [], []
for o in out:
    g = o.get("dirGap")
    w = o.get("dirWindow")
    if g is None or not w or abs(g) <= 0.005:
        continue
    key = (round(float(g), 4), int(w))
    if key not in per:
        want_up = key[0] > 0
        arr = _fwd_max(key[1]) if want_up else _fwd_min(key[1])
        need = _cl * (1.0 + 0.5 * key[0])
        ok = (arr >= need) if want_up else (arr <= need)
        m = _valid & ~np.isnan(arr)
        per[key] = (float(ok[m].mean()) if m.any() else None, ok, m)
        # ⚠ 必须写在「key not in per」块内（每个 key 只投一票）。置于块外会按该 key 在日志中
        # 的**出现次数**加权——同一目标连续 28 日的记录会把它的基线值放大 28 倍（实测偏 0.1pp）。
        if per[key][0] is not None:
            vals.append(per[key][0])
print("  [debug] per key 数 = %d, vals = %d" % (len(per), len(vals)))
base_glob = round(float(np.mean(vals)) * 100, 1) if vals else None
_i0s = [dates.index(o["date"]) for o in out if o.get("date") in DS]
_start = max(0, min(min(_i0s) if _i0s else n - 1, n - 1 - 60))
_rec_valid = np.zeros(n, dtype=bool)
_rec_valid[_start: n - 1] = True
for key, (b, ok, m) in per.items():
    mm = m & _rec_valid
    if mm.any():
        recent_vals.append(float(ok[mm].mean()))
base_recent = round(float(np.mean(recent_vals)) * 100, 1) if recent_vals else None

# ---- ④ 精确率去平凡 ----
_pnt = [o for o in out if o["preciseHit"] is not None and not o["trivialPrecise"]]
_pnt_hits = [o for o in _pnt if o["preciseHit"]]
_triv_n = sum(1 for o in out if o["preciseHit"] is not None and o["trivialPrecise"])
precise_nt = pct(len(_pnt_hits), len(_pnt)) if len(_pnt) >= MIN_SAMPLE else None

# ---- ⑤ 独立目标口径 ----
_DUP = {("subwave", "子浪ⅴ")}
by = {}
for o in out:
    if not o["truncated"] and (o.get("cat"), o.get("key")) not in _DUP:
        if o["date"] and o["evaluated"]:
            k = (o.get("cat"), o.get("key"), round(float(o["price"]), 2))
            cur = by.get(k)
            if cur is None or (o.get("elapsedDays") or 0) > (cur.get("elapsedDays") or 0):
                by[k] = o
n_eff = len(by)

bt = json.load(open(BTJSON, encoding="utf-8"))
print("=== R824 独立复算 vs 生产 backtest.json ===")
rows = [
    ("totalLogged", len(out), bt.get("totalLogged")),
    ("realizedHitRate(band 已实现/删失口径)", realized, bt.get("realizedHitRate")),
    ("hitRateAll(band 全量口径)", hit_all, bt.get("hitRateAll")),
    ("hitRateMaturedOnly", hit_mat, bt.get("hitRateMaturedOnly")),
    ("dirRealizedHitRate(方向推进率)", dir_rate, bt.get("dirRealizedHitRate")),
    ("dirBaseRate(零信息基线·全历史)", base_glob, bt.get("dirBaseRate")),
    ("dirBaseRateRecent(零信息基线·同期)", base_recent, bt.get("dirBaseRateRecent")),
    ("preciseRealizedHitRateNonTrivial", precise_nt, bt.get("preciseRealizedHitRateNonTrivial")),
    ("preciseNonTrivialEvaluated", len(_pnt), bt.get("preciseNonTrivialEvaluated")),
    ("preciseTrivialCount", _triv_n, bt.get("preciseTrivialCount")),
]
bad = 0
for name, mine, prod in rows:
    if isinstance(mine, float) and isinstance(prod, float):
        ok = abs(mine - prod) < 0.15
    else:
        ok = (mine == prod)
    if not ok:
        bad += 1
    print("  %-38s 复算=%-8s 生产=%-8s %s" % (name, mine, prod, "✓" if ok else "✗ 不一致"))

print()
print("  独立目标数 (episode_reps 复算) = %d  | 总记录 %d" % (n_eff, len(out)))
print("  ⇒ 行级记录 %d 条中，独立目标仅 %d 个（%.0f%% 为同一目标的重复观测）"
      % (len(out), n_eff, 100.0 * (1 - n_eff / len(out))))
print()
if bad:
    print("!! 有 %d 项与生产不一致 —— 独立复算与生产口径存在偏差，结论须先查此偏差 !!" % bad)
else:
    print("独立复算结论：全部 %d 项与生产一致 ✓（复算器可信，可据此判读）" % len(rows))
