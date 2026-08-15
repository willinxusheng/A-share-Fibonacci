# -*- coding: utf-8 -*-
"""R224：线上 data.js 与模型数学的【自洽性审计】（前几轮未做，直接回应"准确性"）。

思路：data.js 是单源真值，但只发布最终 prob / bandPct / lo / hi，不发布其分量(_hr/_prior/_bn)。
本脚本用 LIVE K线 与 data.js 内的 probCalib / resonance.breadth，逐字复刻 build_data 的：
  - _frac = min(_vol_for(exp)*sqrt(exp)*_vol_scale, 0.235)        （区间带宽）
  - _drift_prior_prob (反射原理首达) + _recal_g (R217 分段校准)   （漂移先验+校准）
  - _empirical_rates (vol+MA20 匹配的 walk-forward 实证命中率)     （实证级）
  - 融合 _fused = (_bn*_hr + _FUSE_K*_prior)/(_bn+_FUSE_K)        （最终 prob）
然后与 data.js 发布的 prob / bandPct / lo / hi 逐一比对。
若全部吻合 → 发布数字确实等于引擎该算出的数字，"展示=评估"自洽，无准确性漂移 bug；
若某项偏差超容差 → 即真 bug（发布 artifact 与模型脱节，须修）。
严守 R85：只读复刻、不改引擎、不部署。
"""
import json
import math
import os
import re

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
S = open(os.path.join(BASE, "data", "data.js"), encoding="utf-8").read()
D = json.loads(re.search(r"window\.FIB_DATA\s*=\s*(\{.*\})\s*;?\s*$", S, re.S).group(1))
DF = pd.read_csv(os.path.join(BASE, "data", "sh000001.csv"),
                 parse_dates=["date"]).set_index("date").sort_index()

_last = float(D["lastClose"])
_calib = D["probCalib"]                # {"edges":[...11], "vals":[...10]}
_edges = np.array(_calib["edges"], dtype=float)
_vals = np.array(_calib["vals"], dtype=float)
_breadth = float(D.get("resonance", {}).get("breadth", 0.0))
_FUSE_K = 20.0

ret = np.log(DF["close"] / DF["close"].shift(1))
_vw = [20, 60, 120, 250]
_vol_by_w = {w: float(ret.rolling(w).std().iloc[-1]) for w in _vw}
_drift_by_w = {w: float(ret.rolling(w).mean().iloc[-1]) for w in _vw}
_daily_vol = float(ret.rolling(20).std().iloc[-1])
_hv20 = ret.rolling(20).std() * math.sqrt(244) * 100
_hv_pctile = float((_hv20 < _hv20.iloc[-1]).mean() * 100)
_vol_bucket = "高" if _hv_pctile >= 66 else ("中" if _hv_pctile >= 33 else "低")
_vol_scale = 1.15 if _vol_bucket == "高" else (1.0 if _vol_bucket == "中" else 0.88)
_drift_conf = 0.60 if _vol_bucket == "高" else (0.85 if _vol_bucket == "中" else 1.0)
print("vol regime: pctile=%.1f%% bucket=%s vol_scale=%.2f drift_conf=%.2f breadth=%.3f"
      % (_hv_pctile, _vol_bucket, _vol_scale, _drift_conf, _breadth))


def _vol_for(exp):
    w = min(_vw[-1], max(_vw[0], float(exp)))
    if w <= _vw[0]:
        return _vol_by_w[_vw[0]]
    if w >= _vw[-1]:
        return _vol_by_w[_vw[-1]]
    for i in range(len(_vw) - 1):
        a, b = _vw[i], _vw[i + 1]
        if a <= w <= b:
            t = (w - a) / (b - a)
            return _vol_by_w[a] * (1 - t) + _vol_by_w[b] * t
    return _vol_by_w[_vw[-1]]


def _drift_for(exp):
    w = min(_vw[-1], max(_vw[0], float(exp)))
    if w <= _vw[0]:
        return _drift_by_w[_vw[0]]
    if w >= _vw[-1]:
        return _drift_by_w[_vw[-1]]
    for i in range(len(_vw) - 1):
        a, b = _vw[i], _vw[i + 1]
        if a <= w <= b:
            t = (w - a) / (b - a)
            return _drift_by_w[a] * (1 - t) + _drift_by_w[b] * t
    return _drift_by_w[_vw[-1]]


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _drift_prior_prob(base, price, exp):
    _exp = max(10, int(round(exp)))
    _frac = min(_vol_for(_exp) * math.sqrt(_exp) * _vol_scale, 0.235)
    _mu_eff = _drift_for(_exp) * _drift_conf
    _sv = _vol_for(_exp) * math.sqrt(_exp) if _vol_for(_exp) > 0 else 1e-9
    _dir = 1 if price >= base else -1
    if _dir > 0:
        _bar = price * (1.0 - _frac)
        if base >= _bar:
            return 1.0, _bar
        _a = math.log(_bar / base)
        _d1 = (_a - _mu_eff * _exp) / _sv
        _d2 = (-_a - _mu_eff * _exp) / _sv
        _exo = max(-50.0, min(50.0, 2.0 * _mu_eff * _a / (_vol_for(_exp) ** 2 + 1e-12)))
        return 1.0 - _norm_cdf(_d1) + math.exp(_exo) * _norm_cdf(_d2), _bar
    else:
        _bar = price * (1.0 + _frac)
        if base <= _bar:
            return 1.0, _bar
        _a = math.log(_bar / base)
        _b = -_a
        _d1 = (_b + _mu_eff * _exp) / _sv
        _d2 = (_mu_eff * _exp - _b) / _sv
        _exo = max(-50.0, min(50.0, -2.0 * _mu_eff * _b / (_vol_for(_exp) ** 2 + 1e-12)))
        return 1.0 - _norm_cdf(_d1) + math.exp(_exo) * _norm_cdf(_d2), _bar


def _recal_g(p):
    _p = max(0.0, min(1.0, float(p)))
    if _p >= 0.98:
        return _p
    _idx = min(len(_vals) - 1, max(0, int(np.digitize(_p, _edges) - 1)))
    return _vals[_idx]


def _frac_for(exp):
    _exp = max(10, int(round(exp)))
    return min(_vol_for(_exp) * math.sqrt(_exp) * _vol_scale, 0.235)


def _empirical_rates():
    """复刻 build_data._empirical_rates：vol+MA20 匹配锚点，walk-forward 实证首达命中率(%)。"""
    _close = DF["close"].values
    _high = DF["high"].values
    _low = DF["low"].values
    _nn = len(DF)
    _dvol = ret.rolling(20).std()
    _ma = DF["close"].rolling(20).mean()
    _today_up = DF["close"].iloc[-1] > _ma.iloc[-1]
    _anchors = [i for i in range(_nn)
                if not math.isnan(_dvol.iloc[i])
                and 0.75 * _daily_vol <= _dvol.iloc[i] <= 1.25 * _daily_vol
                and not math.isnan(_ma.iloc[i])
                and (DF["close"].iloc[i] > _ma.iloc[i]) == _today_up]
    out = {}
    items = []
    for s in D["tradePlan"]["sellTargets"]:
        items.append(("sellTarget", s["name"], s["price"], s["expDays"]))
    for p in D["subForecast"]["points"]:
        if p["label"] == "浪⑤起":
            continue
        items.append(("subwave", p["label"], p["price"], p["expDays"]))
    for cat, key, price, exp in items:
        _ratio = price / _last
        _up = price >= _last
        _exp = max(1, int(round(exp)))
        # 注意：实证命中用的 _frac 与漂移/区间带宽不同——这里用 _exp=max(1,round(exp))，
        # 不套 _frac_for 的 max(10,...) 下限（那是区间/漂移路径的口径，R224 调试已确认）。
        _frac = min(_vol_for(_exp) * math.sqrt(_exp) * _vol_scale, 0.235)
        _wsum = _whit = _raw = 0
        for i in _anchors:
            if i + _exp >= _nn:
                continue
            _base = _close[i]
            _pref = _base * _ratio
            _fh = _high[i + 1:i + 1 + _exp]
            _fl = _low[i + 1:i + 1 + _exp]
            if _up:
                _hit = _fh.max() >= _pref * (1.0 - _frac)
            else:
                _hit = _fl.min() <= _pref * (1.0 + _frac)
            _wsum += 1.0
            if _hit:
                _whit += 1.0
            _raw += 1
        if _raw >= 10 and _wsum > 0:
            out[(cat, key)] = (round(_whit / _wsum * 100, 1), round(_wsum, 1))
    return out, len(_anchors)


# ---------- 跑检查 ----------
EMP, _n_anchors = _empirical_rates()
print("empirical anchors (vol+MA20 匹配): %d" % _n_anchors)

fails = []
print("\n=== Check 1: 区间带宽 bandPct / lo / hi vs 模型 _frac ===")
for grp in ("sellTargets",):
    for t in D["tradePlan"][grp]:
        _f = _frac_for(t["expDays"])
        bp = round(_f * 100, 1)
        lo = round(t["price"] - t["price"] * _f, 2)
        hi = round(t["price"] + t["price"] * _f, 2)
        ok_bp = abs(bp - t["bandPct"]) < 0.15
        ok_lo = abs(lo - t["lo"]) < 0.6
        ok_hi = abs(hi - t["hi"]) < 0.6
        flag = "" if (ok_bp and ok_lo and ok_hi) else "  <-- MISMATCH"
        if flag:
            fails.append(("%s band" % t["name"], bp, t["bandPct"], lo, t["lo"], hi, t["hi"]))
        print("  %-14s bandPct %.1f/%.1f  lo %.2f/%.2f  hi %.2f/%.2f%s"
              % (t["name"], bp, t["bandPct"], lo, t["lo"], hi, t["hi"], flag))
for p in D["subForecast"]["points"]:
    _f = _frac_for(p["expDays"])
    bp = round(_f * 100, 1)
    lo = round(p["price"] - p["price"] * _f, 2)
    hi = round(p["price"] + p["price"] * _f, 2)
    ok_bp = abs(bp - p["bandPct"]) < 0.15
    ok_lo = abs(lo - p["lo"]) < 0.6
    ok_hi = abs(hi - p["hi"]) < 0.6
    flag = "" if (ok_bp and ok_lo and ok_hi) else "  <-- MISMATCH"
    if flag:
        fails.append(("%s band" % p["label"], bp, p["bandPct"], lo, p["lo"], hi, p["hi"]))
    print("  %-10s bandPct %.1f/%.1f  lo %.2f/%.2f  hi %.2f/%.2f%s"
          % (p["label"], bp, p["bandPct"], lo, p["lo"], hi, p["hi"], flag))

print("\n=== Check 2: 漂移模型目标 prob vs (R217校准后先验) ===")
for p in D["subForecast"]["points"]:
    if p["probSrc"] != "漂移模型":
        continue
    _raw, _ = _drift_prior_prob(_last, p["price"], p["expDays"])
    _pd = max(2.0, min(98.0, _recal_g(_raw) * 100.0))
    _dir = 1 if p["price"] >= _last else -1
    _prior = max(2.0, min(98.0, _pd + _breadth * _dir * 5.0))
    _prob = round(max(2.0, min(98.0, _prior)), 1)
    flag = "" if abs(_prob - p["prob"]) < 0.6 else "  <-- MISMATCH"
    if flag:
        fails.append(("%s drift prob" % p["label"], _prob, p["prob"]))
    print("  %-10s recomputed prob=%.1f  published=%.1f%s" % (p["label"], _prob, p["prob"], flag))

print("\n=== Check 3: 回测实证目标 prob vs (融合 _hr⊕校准先验, K=20) ===")
for t in D["tradePlan"]["sellTargets"]:
    _hr, _bn = EMP.get(("sellTarget", t["name"]), (None, None))
    if _hr is None:
        print("  %-14s 无实证锚点(应走漂移/历史)，跳过" % t["name"])
        continue
    _raw, _ = _drift_prior_prob(_last, t["price"], t["expDays"])
    _pd = max(2.0, min(98.0, _recal_g(_raw) * 100.0))
    _dir = 1 if t["price"] >= _last else -1
    _prior = max(2.0, min(98.0, _pd + _breadth * _dir * 5.0))
    _fused = (_bn * _hr + _FUSE_K * _prior) / (_bn + _FUSE_K)
    _prob = round(max(2.0, min(98.0, _fused)), 1)
    flag = "" if abs(_prob - t["prob"]) < 0.6 else "  <-- MISMATCH"
    if flag:
        fails.append(("%s emp prob" % t["name"], _prob, t["prob"]))
    print("  %-14s _hr=%.1f(_bn=%.0f) recomputed=%.1f  published=%.1f%s"
          % (t["name"], _hr, _bn, _prob, t["prob"], flag))
for p in D["subForecast"]["points"]:
    if p["label"] == "浪⑤起" or p["probSrc"] != "回测实证":
        continue
    _hr, _bn = EMP.get(("subwave", p["label"]), (None, None))
    if _hr is None:
        print("  %-10s 无实证锚点，跳过" % p["label"])
        continue
    _raw, _ = _drift_prior_prob(_last, p["price"], p["expDays"])
    _pd = max(2.0, min(98.0, _recal_g(_raw) * 100.0))
    _dir = 1 if p["price"] >= _last else -1
    _prior = max(2.0, min(98.0, _pd + _breadth * _dir * 5.0))
    _fused = (_bn * _hr + _FUSE_K * _prior) / (_bn + _FUSE_K)
    _prob = round(max(2.0, min(98.0, _fused)), 1)
    flag = "" if abs(_prob - p["prob"]) < 0.6 else "  <-- MISMATCH"
    if flag:
        fails.append(("%s emp prob" % p["label"], _prob, p["prob"]))
    print("  %-10s _hr=%.1f(_bn=%.0f) recomputed=%.1f  published=%.1f%s"
          % (p["label"], _hr, _bn, _prob, p["prob"], flag))

print("\n=== R224 结论 ===")
if not fails:
    print("  PASS：线上 data.js 的区间带宽与融合概率均与模型数学逐字自洽，"
          "发布数字=引擎该算出的数字，无准确性漂移 bug。")
else:
    print("  FAIL：发现 %d 处不一致：" % len(fails))
    for f in fails:
        print("   ", f)
