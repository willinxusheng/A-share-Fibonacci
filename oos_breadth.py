# -*- coding: utf-8 -*-
"""板块联动广度口径 OOS 复验（R166）：2只宽基 vs 5只宽基。

忠实复刻 build_data._enrich 概率合成（首达概率 + 漂移先验 + 回测实证融合 + 历史浪幅校准），
仅隔离 `_breadth` 这一个变量（2只设计={沪深300,创业板指} / 5只设计=上述+上证50+中证500+科创50）。
两种设计共享：同一 empirical 锚点池（walk-forward，设计无关）、同一漂移/波动 regime（生产全局当前值）、
同一目标集（生产 sellTargets + subForecast.rows）。差异仅来自 breadth 项进入 `_prior` 的 ±5 加权。
逐测试日 walk-forward（需 indexCompare 有数据，约 2024-09-18 起）：
  prob = 含 breadth 先验的融合概率；actual = 该目标在 [t+1, t+exp] 首达(预测带)；Brier=(p-actual)^2。
平均 Brier 越低越优；仅当 Brier5 <= Brier2（不恶化）才允许部署 5只设计。
"""
import re
import os
import json
import math
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))

src = open(os.path.join(BASE, "data", "data.js"), encoding="utf-8").read()
D = json.loads(re.search(r"window\.FIB_DATA\s*=\s*(\{.*\})\s*;?\s*$", src, re.S).group(1))

# ---- 生产校准表（build_data 写入 data.js.probCalib，逐字复用，避免重算漂移）----
# R217 分段校准：裸首达概率(0-1)→经验校准概率(0-1)，单调分桶，空桶恒等。
# 直接读生产写入的 edges/vals，与 build_data._enrich 零偏差（不重算 _fit_prior_recal，杜绝漂移）。
# 修复：此前 oos_breadth.enrich 直接用裸首达概率、缺失 _recal_g → 生产 OOS 守门员测的是
# 「未校准」概率，对 _recal_g 校准改动假绿（R85 最该警惕的类）。
_prob_calib = D.get("probCalib") or {}
_calib_edges = _prob_calib.get("edges")
_calib_vals = _prob_calib.get("vals")


def _recal_g(p):
    """逐字复刻 build_data._recal_g：裸首达概率→经验校准概率（修正低概率系统性低估，OOS Brier −27%）。"""
    if _calib_edges is None or _calib_vals is None:
        return p  # 旧数据缺校准表 → 恒等，fail-soft（不阻断）
    _p = max(0.0, min(1.0, float(p)))
    if _p >= 0.98:
        return _p
    _K = len(_calib_vals)
    _idx = min(_K - 1, max(0, int(np.digitize(_p, _calib_edges) - 1)))
    return _calib_vals[_idx]

dates = D["kline"]["dates"]
ohlc = D["kline"]["ohlc"]                      # build_data 生成顺序 [open, close, low, high]
close = np.array([x[1] for x in ohlc], dtype=float)
high = np.array([x[3] for x in ohlc], dtype=float)
low = np.array([x[2] for x in ohlc], dtype=float)
df = pd.DataFrame({"close": close, "high": high, "low": low},
                  index=pd.to_datetime(dates))
df.index = df.index.strftime("%Y-%m-%d")
ret = np.log(df["close"] / df["close"].shift(1))
last_close = float(close[-1])
_nn = len(df)

# ---- 波动/漂移 regime（生产全局当前值，两种设计共享）----
_vol_scale = float(D["volRegime"]["bandScale"])
_drift_conf = float(D["volRegime"]["driftConf"])
_vol_windows = [20, 60, 120, 250]
_drift_windows = [20, 60, 120, 250]
_vol_by_w = {w: float(ret.rolling(w).std().iloc[-1]) for w in _vol_windows}
_drift_by_w = {w: float(ret.rolling(w).mean().iloc[-1]) for w in _drift_windows}


def _vol_for(exp):
    w = min(_vol_windows[-1], max(_vol_windows[0], float(exp)))
    if w <= _vol_windows[0]:
        return _vol_by_w[_vol_windows[0]]
    if w >= _vol_windows[-1]:
        return _vol_by_w[_vol_windows[-1]]
    for i in range(len(_vol_windows) - 1):
        w0, w1 = _vol_windows[i], _vol_windows[i + 1]
        if w0 <= w <= w1:
            t = (w - w0) / (w1 - w0)
            return _vol_by_w[w0] * (1 - t) + _vol_by_w[w1] * t
    return _vol_by_w[_vol_windows[-1]]


def _drift_for(exp):
    w = min(_drift_windows[-1], max(_drift_windows[0], float(exp)))
    if w <= _drift_windows[0]:
        return _drift_by_w[_drift_windows[0]]
    if w >= _drift_windows[-1]:
        return _drift_by_w[_drift_windows[-1]]
    for i in range(len(_drift_windows) - 1):
        w0, w1 = _drift_windows[i], _drift_windows[i + 1]
        if w0 <= w <= w1:
            t = (w - w0) / (w1 - w0)
            return _drift_by_w[w0] * (1 - t) + _drift_by_w[w1] * t
    return _drift_by_w[_drift_windows[-1]]


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _frac_for(exp):
    _exp = max(10, int(round(exp)))
    return min(_vol_for(_exp) * math.sqrt(_exp) * _vol_scale, 0.235)


# ---- _hist_legs（复刻 build_data 175-188）----
st = json.load(open(os.path.join(BASE, "data", "structures.json"), encoding="utf-8"))
_hist_legs = []
for _i in range(len(st.get("zigzag", [])) - 1):
    _p0, _p1 = st["zigzag"][_i], st["zigzag"][_i + 1]
    try:
        _ld = max(1, len(pd.bdate_range(pd.Timestamp(_p0["date"]), pd.Timestamp(_p1["date"]))) - 1)
    except Exception:
        continue
    if _ld < 10:
        continue
    try:
        _lr = math.log(float(_p1["price"]) / float(_p0["price"]))
    except Exception:
        continue
    _hist_legs.append((_lr, _ld, str(_p0["date"]), str(_p1["date"])))


def _hist_calib(price, exp):
    _a = math.log(price / last_close)
    _dir = 1 if _a >= 0 else -1
    _at = abs(_a)

    def _rate(lo_b, hi_b):
        _n = _d = 0
        for _lr, _ld, *_ in _hist_legs:
            if _dir * _lr <= 0:
                continue
            _ttr = (_ld * _at / abs(_lr)) if abs(_lr) > 0 else _ld
            if not (lo_b <= _ttr <= hi_b):
                continue
            _d += 1
            if abs(_lr) >= _at:
                _n += 1
        return _n, _d

    MIN_LEGS = 4
    for _lo, _hi in ((0.5 * exp, 2.0 * exp), (0.25 * exp, 4.0 * exp), (0.0, 1e9)):
        _n, _d = _rate(_lo, _hi)
        if _d >= MIN_LEGS:
            return max(2, min(98, _n / _d * 100))
    return None


_FUSE_K = 20.0

# ---- 生产目标集 ----
sell_targets = [{"name": t["name"], "price": float(t["price"]), "expDays": int(t["expDays"])}
                for t in D["tradePlan"]["sellTargets"]]
sub_rows = [{"wave": r["wave"], "target": float(r["target"]), "expDays": int(r["expDays"])}
            for r in D["subForecast"]["rows"]]

# ---- empirical 锚点池（复刻 build_data 981-985 + 1007-1062，设计无关，计算一次）----
daily_vol = float(ret.rolling(20).std().iloc[-1])
_dvol_series = ret.rolling(20).std()
_band = (0.75, 1.25)
_ma_series = df["close"].rolling(20).mean()
_today_trend_up = bool(df["close"].iloc[-1] > _ma_series.iloc[-1])
_anchors = [i for i in range(len(df))
            if not math.isnan(_dvol_series.iloc[i])
            and _band[0] * daily_vol <= _dvol_series.iloc[i] <= _band[1] * daily_vol
            and not math.isnan(_ma_series.iloc[i])
            and (df["close"].iloc[i] > _ma_series.iloc[i]) == _today_trend_up]


def _empirical_rates():
    _c = df["close"].values
    _h = df["high"].values
    _l = df["low"].values
    _items = []
    for s in sell_targets:
        _items.append(("sellTarget", s["name"], s["price"], s["expDays"]))
    for r in sub_rows:
        _items.append(("subwave", r["wave"], r["target"], r["expDays"]))
    _out = {}
    for cat, key, price, exp in _items:
        _ratio = price / last_close
        _up = price >= last_close
        _exp = max(1, int(round(exp)))
        _frac = _frac_for(exp)
        _wsum = _whit = _raw = 0.0
        for i in _anchors:
            if i + _exp >= _nn:
                continue
            _base = _c[i]
            _pref = _base * _ratio
            _fh = _h[i + 1:i + 1 + _exp]
            _fl = _l[i + 1:i + 1 + _exp]
            if _up:
                _hit = _fh.max() >= _pref * (1.0 - _frac)
            else:
                _hit = _fl.min() <= _pref * (1.0 + _frac)
            _wsum += 1.0
            if _hit:
                _whit += 1.0
            _raw += 1
        if _raw >= 10 and _wsum > 0:
            _out[(cat, key)] = (round(_whit / _wsum * 100.0, 1), round(_wsum, 1))
    return _out


bt_lookup = _empirical_rates()

# ---- 指数 ret20 序列（来自 indexCompare 归一化序列）----
idx_map = {ic["name"]: {d: v for d, v in ic["data"]} for ic in D["indexCompare"]}
BREADTH2_NAMES = {"沪深300", "创业板指"}
BREADTH5_NAMES = {"沪深300", "创业板指", "上证50", "中证500", "科创50"}


def breadth_at(t, names):
    dt = dates[t]
    dt20 = dates[t - 20]
    signs = 0.0
    n = 0
    for nm in names:
        m = idx_map.get(nm)
        if not m or dt not in m or dt20 not in m:
            return None
        r = m[dt] / m[dt20] - 1.0
        signs += 1 if r > 0 else (-1 if r < 0 else 0)
        n += 1
    if n < len(names):
        return None
    return signs / len(names)


def enrich(cat, key, price, exp, breadth):
    _exp = max(10, int(round(exp)))
    _frac = _frac_for(exp)
    _dir = 1 if price >= last_close else -1
    _mu_eff = _drift_for(_exp) * _drift_conf
    _sv = _vol_for(_exp) * math.sqrt(_exp) if _vol_for(_exp) > 0 else 1e-9
    if _dir > 0:
        _barrier = price * (1.0 - _frac)
        if last_close >= _barrier:
            _hit = 1.0
        else:
            _a = math.log(_barrier / last_close)
            _d1 = (_a - _mu_eff * _exp) / _sv
            _d2 = (-_a - _mu_eff * _exp) / _sv
            _exo = max(-50.0, min(50.0, 2.0 * _mu_eff * _a / (_vol_for(_exp) ** 2 + 1e-12)))
            _hit = 1.0 - _norm_cdf(_d1) + math.exp(_exo) * _norm_cdf(_d2)
    else:
        _barrier = price * (1.0 + _frac)
        if last_close <= _barrier:
            _hit = 1.0
        else:
            _a = math.log(_barrier / last_close)
            _b = -_a
            _d1 = (_b + _mu_eff * _exp) / _sv
            _d2 = (_mu_eff * _exp - _b) / _sv
            _exo = max(-50.0, min(50.0, -2.0 * _mu_eff * _b / (_vol_for(_exp) ** 2 + 1e-12)))
            _hit = 1.0 - _norm_cdf(_d1) + math.exp(_exo) * _norm_cdf(_d2)
    _hit = _recal_g(_hit)   # R217 分段校准，逐字复刻 build_data._enrich（修正低概率系统性低估）
    _p_drift = max(2, min(98, _hit * 100))
    _hcap = _hist_calib(price, exp)
    _prior = _p_drift + breadth * _dir * 5.0
    _bt = bt_lookup.get((cat, key))
    if _bt is not None and _bt[0] is not None:
        _hr, _bn = _bt
        _fused = (_bn * _hr + _FUSE_K * _prior) / (_bn + _FUSE_K)
        _prob = round(max(2, min(98, _fused)), 1)
    else:
        if _hcap is not None and _hcap < _prior:
            _prob = round(_hcap, 1)
        else:
            _prob = round(max(2, min(98, _prior)), 1)
    return _prob


def actual_hit(price, exp, t):
    # 评估口径对齐【生产概率自身】而非 backtest.py 跟踪指标：
    # 生产概率的实证锚点率(_empirical_rates:1138/1142)命中窗口 = max(1, round(exp))，
    # 展示带/首达 horizon(_enrich:1345/1349) = max(10, round(exp))。两者即概率预测的"触达"定义。
    # backtest.py 的 max(HORIZON=30, expDays) 是【独立的纵向触达率跟踪日志】口径，与概率校准无关——
    # 若 OOS 用它(30天窗口)会让命中率系统性抬高、校准验证过度乐观(自欺)，故**不**对齐它。
    # 窗口用 max(1,exp) 对齐实证锚点率；band 用 _frac_for(exp)=max(10,exp) 对齐 _enrich 展示带。
    _up = price >= last_close
    _exp = max(1, int(round(exp)))
    lo = t + 1
    hi = t + _exp
    if hi >= _nn:
        return None
    _frac = _frac_for(exp)
    if _up:
        return 1 if high[lo:hi + 1].max() >= price * (1.0 - _frac) else 0
    else:
        return 1 if low[lo:hi + 1].min() <= price * (1.0 + _frac) else 0


# ---- walk-forward Brier ----
targets = [("sellTarget", t["name"], t["price"], t["expDays"]) for t in sell_targets] + \
          [("subwave", r["wave"], r["target"], r["expDays"]) for r in sub_rows]
max_exp = max(t[3] for t in targets)


def compute_production_oos_brier():
    """生产啮合式 walk-forward OOS Brier（逐字复刻 build_data._enrich：含 empirical 融合/_FUSE_K/_hist_calib/_breadth 共振）。

    单一真源：selfcheck/oos_guard 的"生产线守门员"复用本函数，使 OOS 闸门真正守护生产概率引擎
    （而非仅守护裸首达公式）。返回 (brier2, brier5, cnt, per2, per5)。
    """
    brier2 = brier5 = cnt = 0
    per2 = {}
    per5 = {}
    for t in range(20, _nn - max_exp):
        b2 = breadth_at(t, BREADTH2_NAMES)
        b5 = breadth_at(t, BREADTH5_NAMES)
        if b2 is None or b5 is None:
            continue
        for cat, key, price, exp in targets:
            ah = actual_hit(price, exp, t)
            if ah is None:
                continue
            p2 = enrich(cat, key, price, exp, b2) / 100.0
            p5 = enrich(cat, key, price, exp, b5) / 100.0
            brier2 += (p2 - ah) ** 2
            brier5 += (p5 - ah) ** 2
            cnt += 1
            per2[(cat, key)] = per2.get((cat, key), 0) + (p2 - ah) ** 2
            per5[(cat, key)] = per5.get((cat, key), 0) + (p5 - ah) ** 2
    return brier2, brier5, cnt, per2, per5


if __name__ == "__main__":
    brier2, brier5, cnt, per2, per5 = compute_production_oos_brier()
    print("=== 板块联动广度口径 OOS 复验 (R166) ===")
    print("empirical 锚点数 = %d；测试目标数 = %d；有效(测试日,目标)对 = %d" % (len(_anchors), len(targets), cnt))
    if cnt == 0:
        print("⚠️ 有效(测试日,目标)对为 0，无样本可评估 OOS，复验中止（请检查 empirical 锚点与 targets 是否覆盖测试区间）。")
        raise SystemExit(1)
    print("Brier2 (2只宽基=%s) = %.5f" % (sorted(BREADTH2_NAMES), brier2 / cnt))
    print("Brier5 (5只宽基=%s) = %.5f" % (sorted(BREADTH5_NAMES), brier5 / cnt))
    _delta = (brier5 - brier2) / cnt
    print("ΔBrier(Brier5-Brier2) = %+.5f  -> %s" % (_delta, "5只更差✗" if _delta > 1e-9 else "5只不恶化✓(可部署)"))
    print("\n--- 逐目标 Brier(5只 - 2只)，正=5只更差 ---")
    for (cat, key, price, exp) in targets:
        k = (cat, key)
        d = (per5.get(k, 0) - per2.get(k, 0))
        print("  %-14s %-12s  Δ=%.5f" % (cat, key, d))
