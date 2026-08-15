# -*- coding: utf-8 -*-
"""跨市场广度入先验 OOS 复验（R167）。

忠实复刻 build_data._enrich 概率合成（首达概率 + 漂移先验 + 回测实证融合 + 历史浪幅校准），
隔离「是否把跨市场广度(_xbreadth)加进 _prior」这一变量：
  - 基线(生产现状): _prior = _p_drift + _breadth*_dir*5.0          （仅 A股宽基共振）
  - 处理(候选)    : _prior = _p_drift + _breadth*_dir*5.0 + _xbreadth*_dir*W   （+ 跨市场联动）
_xbreadth 来自 4 只外部 equity（恒生/恒生科技/标普500/纳斯达克，近20日方向符号均值，设计集总数归一）。
两种设计共享：同一 empirical 锚点池、同一漂移/波动 regime、同一目标集、同一 A股 _breadth。
差异仅来自「是否额外叠加跨市场项」。
逐测试日 walk-forward（需 A股 indexCompare + 4只跨市场 均有数据）：
  prob = 含/不含跨市场项的融合概率；actual = 该目标在 [t+1, t+exp] 首达(预测带)；Brier=(p-actual)^2。
平均 Brier 越低越优；仅当 Brier(处理) <= Brier(基线)（不恶化）才允许部署跨市场入先验。
候选权重 W∈{2,3,4} 各测一遍，报告最优且在阈值内者。
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

dates = D["kline"]["dates"]
ohlc = D["kline"]["ohlc"]
close = np.array([x[1] for x in ohlc], dtype=float)
high = np.array([x[3] for x in ohlc], dtype=float)
low = np.array([x[2] for x in ohlc], dtype=float)
df = pd.DataFrame({"close": close, "high": high, "low": low},
                  index=pd.to_datetime(dates))
df.index = df.index.strftime("%Y-%m-%d")
ret = np.log(df["close"] / df["close"].shift(1))
last_close = float(close[-1])
_nn = len(df)

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
        _lr = math.log(float(_p1["close"]) / float(_p0["close"]))
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

sell_targets = [{"name": t["name"], "price": float(t["price"]), "expDays": int(t["expDays"])}
                for t in D["tradePlan"]["sellTargets"]]
sub_rows = [{"wave": r["wave"], "target": float(r["target"]), "expDays": int(r["expDays"])}
            for r in D["subForecast"]["rows"]]

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

# ---- A股 宽基共振广度（生产现状，5只设计集）----
idx_map = {ic["name"]: {d: v for d, v in ic["data"]} for ic in D["indexCompare"]}
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


# ---- 跨市场 4 只外部 equity 原始数据（自 raw.md 解析，复刻 read_kline_md 列名映射）----
def _parts_of(line):
    return [p.strip() for p in line.strip().strip("|").split("|")]


def _read_kline_md(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        text = f.read()
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip().startswith("|") and "---" not in ln]
    if len(lines) < 2:
        return []
    header = {h.lower(): i for i, h in enumerate(_parts_of(lines[0]))}
    di = header.get("date")
    ci = header.get("last")
    if ci is None:
        ci = header.get("close")
    if di is None or ci is None:
        return []
    for ln in lines[1:]:
        parts = _parts_of(ln)
        if len(parts) < 2:
            continue
        # 防御(R170)：单行列数少于表头(rawn.md 偶发残缺行)时，parts[di]/parts[ci] 会
        # IndexError 拖垮 OOS 复验；越界即跳过该行（与 analyze.read_kline_md 同源修复）。
        if di >= len(parts) or ci >= len(parts):
            continue
        d = parts[di].strip()
        try:
            c = float(str(parts[ci]).replace(",", "").strip())
        except Exception:
            continue
        rows.append((d, c))
    return rows


XMK_FILES = {"恒生指数": "hkHSI_raw.md", "恒生科技": "hkHSTECH_raw.md",
             "标普500": "usINX_raw.md", "纳斯达克": "usIXIC_raw.md"}
xmk_map = {nm: {d: c for d, c in _read_kline_md(os.path.join(BASE, "data", fn))}
           for nm, fn in XMK_FILES.items()}
XMK_NAMES = list(XMK_FILES.keys())


def xbreadth_at(t):
    dt = dates[t]
    dt20 = dates[t - 20]
    signs = 0.0
    n = 0
    for nm in XMK_NAMES:
        m = xmk_map.get(nm)
        if not m or dt not in m or dt20 not in m:
            return None
        r = m[dt] / m[dt20] - 1.0
        signs += 1 if r > 0 else (-1 if r < 0 else 0)
        n += 1
    if n < len(XMK_NAMES):
        return None
    return signs / len(XMK_NAMES)


def enrich(cat, key, price, exp, breadth, xbreadth, W):
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
    _p_drift = max(2, min(98, _hit * 100))
    _hcap = _hist_calib(price, exp)
    # 基线: 仅 A股 breadth*5.0；处理: 再叠加 跨市场 xbreadth*W
    _prior = _p_drift + breadth * _dir * 5.0 + xbreadth * _dir * W
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


targets = [("sellTarget", t["name"], t["price"], t["expDays"]) for t in sell_targets] + \
          [("subwave", r["wave"], r["target"], r["expDays"]) for r in sub_rows]
max_exp = max(t[3] for t in targets)

print("=== 跨市场广度入先验 OOS 复验 (R167) ===")
print("empirical 锚点数 = %d；测试目标数 = %d" % (len(_anchors), len(targets)))

# 基线 Brier（W=0）先计算一次
brier_base = cnt = 0
per_base = {}
for t in range(20, _nn - max_exp):
    b = breadth_at(t, BREADTH5_NAMES)
    xb = xbreadth_at(t)
    if b is None or xb is None:
        continue
    for cat, key, price, exp in targets:
        ah = actual_hit(price, exp, t)
        if ah is None:
            continue
        p = enrich(cat, key, price, exp, b, 0.0, 0.0) / 100.0
        brier_base += (p - ah) ** 2
        cnt += 1
        per_base[(cat, key)] = per_base.get((cat, key), 0) + (p - ah) ** 2
print("有效(测试日,目标)对 = %d" % cnt)
print("Brier基线(仅A股breadth×5.0, W=0) = %.5f" % (brier_base / cnt))

# 各候选权重
best_W = None
best_delta = 1e9
for W in (2.0, 3.0, 4.0):
    brier_t = 0
    per_t = {}
    for t in range(20, _nn - max_exp):
        b = breadth_at(t, BREADTH5_NAMES)
        xb = xbreadth_at(t)
        if b is None or xb is None:
            continue
        for cat, key, price, exp in targets:
            ah = actual_hit(price, exp, t)
            if ah is None:
                continue
            p = enrich(cat, key, price, exp, b, xb, W) / 100.0
            brier_t += (p - ah) ** 2
            per_t[(cat, key)] = per_t.get((cat, key), 0) + (p - ah) ** 2
    _delta = (brier_t - brier_base) / cnt
    _verdict = "不恶化✓(可部署)" if _delta <= 1e-9 else "更差✗"
    print("Brier(W=%.1f) = %.5f  Δ=%.5f  -> %s" % (W, brier_t / cnt, _delta, _verdict))
    if _delta <= 1e-9 and _delta < best_delta:
        best_delta = _delta
        best_W = W

print("\n结论:", ("最优可部署权重 W=%s (ΔBrier=%.5f)" % (best_W, best_delta)) if best_W is not None
      else "所有候选权重均恶化 → 保持跨市场可见化、不入先验")
