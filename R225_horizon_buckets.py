# -*- coding: utf-8 -*-
"""R225：分 horizon / 分目标幅度的 OOS 准确性体检 + 二维校准提升空间量化。

第一部分：逐字复刻 calibrate.py 的 walk-forward 首达概率 OOS（first_passage_prob
+ band 命中定义，与 build_data._enrich / audit50._expected 逐字一致），但按
horizon 桶与幅度桶双维度输出 Brier/斜率/gap，定位结构性偏差。

第二部分：walk-forward 二维(history×幅度)经验校准 OOS——训练段估每组
(model 桶) 的 realized 均值映射、测试段应用，量化『若采用二维校准，Brier
能从 0.1752 降到多少』。这是『准确性提升空间』的严谨 OOS 上界估计。

全部只读 data/，不改引擎、不改生产概率。严守 R85：仅体检 + 候选量化，不部署任何修正。
"""
import math
import os

import numpy as np
import pandas as pd

_WINDOWS = [20, 60, 120, 250]


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def first_passage_prob(a, mu, sigma, T):
    if T <= 0:
        return 0.5
    sv = sigma * math.sqrt(T)
    if sv <= 0:
        return 0.5
    exo = max(-50.0, min(50.0, 2.0 * mu * a / (sigma ** 2 + 1e-12)))
    if a >= 0:
        d1 = (a - mu * T) / sv
        d2 = (-a - mu * T) / sv
        return 1.0 - _norm_cdf(d1) + math.exp(exo) * _norm_cdf(d2)
    else:
        b = -a
        d1 = (b + mu * T) / sv
        d2 = (mu * T - b) / sv
        return 1.0 - _norm_cdf(d1) + math.exp(exo) * _norm_cdf(d2)


def _interp(table, x):
    ws = sorted(table.keys())
    if x <= ws[0]:
        return table[ws[0]]
    if x >= ws[-1]:
        return table[ws[-1]]
    for i in range(len(ws) - 1):
        w0, w1 = ws[i], ws[i + 1]
        if w0 <= x <= w1:
            t = (x - w0) / (w1 - w0)
            return table[w0] * (1 - t) + table[w1] * t
    return table[ws[-1]]


def _vol_scale_at(hv20_full, idx):
    if idx < 0 or idx >= len(hv20_full) or pd.isna(hv20_full.iloc[idx]):
        return 1.0
    _pct = float((hv20_full.iloc[:idx + 1].dropna() < hv20_full.iloc[idx]).mean()) * 100
    return 1.15 if _pct >= 66 else (1.0 if _pct >= 33 else 0.88)


def _bucket_of(H):
    if H <= 40:
        return "短(<=40)"
    if H <= 90:
        return "中(40-90]"
    return "长(>90)"


def _rbucket_of(r):
    return "近(<=8%)" if r <= 0.08 else "远(>=12%)"


def _slope(ps, ys):
    ps = np.clip(ps, 0.001, 0.999)
    z = np.log(ps / (1.0 - ps))
    if np.std(z) > 1e-6:
        return float(np.polyfit(z, ys, 1)[0])
    return None


def _collect_recs(df, vol_conf=1.0, max_horizon=250, min_anchor_gap=20):
    """逐字复刻 calibrate.run_calibration 的样本生成，返回 (p, y, H, r) 列表。

    recs 按锚点 i 递增、再 H、再 r 顺序生成 → 列表顺序≈时间顺序，walk-forward 切分安全。
    """
    ret = np.log(df["close"] / df["close"].shift(1))
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    n = len(df)
    hv20_full = ret.rolling(20).std() * np.sqrt(244) * 100
    vol_by_w = {w: ret.rolling(w).std().values for w in _WINDOWS}
    drift_by_w = {w: ret.rolling(w).mean().values for w in _WINDOWS}
    r_grid = [0.03, 0.05, 0.08, 0.12, 0.15, 0.20]
    h_grid = [20, 40, 60, 90, 120, 180, 250]
    recs = []
    i_end = n - max_horizon - 1
    for i in range(min_anchor_gap, i_end + 1):
        base = close[i]
        for H in h_grid:
            if i + H >= n:
                continue
            w = min(_WINDOWS[-1], max(_WINDOWS[0], H))
            sigma = _interp({ww: vol_by_w[ww][i] for ww in _WINDOWS}, w)
            if sigma is None or math.isnan(sigma) or sigma <= 0:
                continue
            _exp = max(10, int(round(H)))
            _mu_eff = _interp({ww: drift_by_w[ww][i] for ww in _WINDOWS}, w) * vol_conf
            _sig = _interp({ww: vol_by_w[ww][i] for ww in _WINDOWS}, w)
            fut_hi = high[i + 1: i + 1 + H]
            fut_lo = low[i + 1: i + 1 + H]
            for r in r_grid:
                _frac = min(_sig * math.sqrt(_exp) * _vol_scale_at(hv20_full, i), 0.235)
                a_up = math.log((1.0 + r) * (1.0 - _frac))
                p_up = first_passage_prob(a_up, _mu_eff, _sig, _exp)
                hit_up = (fut_hi.max() >= base * (1.0 + r) * (1.0 - _frac))
                recs.append((p_up, 1.0 if hit_up else 0.0, H, r))
                a_dn = math.log((1.0 - r) * (1.0 + _frac))
                p_dn = first_passage_prob(a_dn, _mu_eff, _sig, _exp)
                hit_dn = (fut_lo.min() <= base * (1.0 - r) * (1.0 + _frac))
                recs.append((p_dn, 1.0 if hit_dn else 0.0, H, r))
    return recs


def _agg(recs, mask):
    if mask.sum() == 0:
        return None
    ps = np.clip(np.array([r[0] for r in recs])[mask], 0.001, 0.999)
    ys = np.array([r[1] for r in recs])[mask]
    mm = float(ps.mean())
    rz = float(ys.mean())
    sl = _slope(ps, ys)
    return {
        "n": int(mask.sum()),
        "brier": round(float(np.mean((ps - ys) ** 2)), 4),
        "modelMean": round(mm, 3),
        "realized": round(rz, 3),
        "gap": round(rz - mm, 3),
        "slope": round(sl, 3) if sl is not None else None,
    }


def run_bucketed(recs):
    arr = np.array([(r[0], r[1], r[2], r[3]) for r in recs])
    ps_all = np.clip(arr[:, 0].astype(float), 0.001, 0.999)
    ys_all = arr[:, 1].astype(float)
    overall = {
        "n": len(recs),
        "overall_brier": round(float(np.mean((ps_all - ys_all) ** 2)), 4),
        "overall_slope": (round(_slope(ps_all, ys_all), 3)
                          if _slope(ps_all, ys_all) is not None else None),
    }
    by_H, by_r = {}, {}
    for H in [20, 40, 60, 90, 120, 180, 250]:
        a = _agg(recs, arr[:, 2] == H)
        if a:
            by_H[int(H)] = a
    for r in [0.03, 0.05, 0.08, 0.12, 0.15, 0.20]:
        a = _agg(recs, np.isclose(arr[:, 3], r))
        if a:
            by_r[float(r)] = a
    hb, rb = {}, {}
    for tag in ["短(<=40)", "中(60-90)", "长(>=120)"]:
        hb[tag] = _agg(recs, np.array([_bucket_of(int(H)) == tag for H in arr[:, 2]]))
    for tag in ["近(<=8%)", "远(>=12%)"]:
        rb[tag] = _agg(recs, np.array([_rbucket_of(float(r)) == tag for r in arr[:, 3]]))
    return {**overall, "by_horizon_bucket": hb, "by_r_bucket": rb,
            "by_H": by_H, "by_r": by_r}


def walkforward_2d_calib(recs, test_frac=0.4):
    """walk-forward 二维(history×幅度)经验校准 OOS。

    训练段(前 1-test_frac)估每组 (H桶, r桶) 的 realized 均值映射；
    测试段应用该映射(直方图/经验校准)，对比『不校准』Brier。
    返回 baseline_brier / calib_brier / delta 与每组训练样本数。
    """
    k = int(len(recs) * (1 - test_frac))
    train, test = recs[:k], recs[k:]
    train_map = {}
    for p, y, H, r in train:
        key = (_bucket_of(int(H)), _rbucket_of(float(r)))
        train_map.setdefault(key, []).append(y)
    calib_map = {key: float(np.mean(v)) for key, v in train_map.items()}
    base_err, cal_err = [], []
    empty_groups = 0
    for p, y, H, r in test:
        key = (_bucket_of(int(H)), _rbucket_of(float(r)))
        cp = calib_map.get(key)
        if cp is None:
            cp = p
            empty_groups += 1
        base_err.append((p - y) ** 2)
        cal_err.append((cp - y) ** 2)
    return {
        "train_n": len(train),
        "test_n": len(test),
        "baseline_brier": round(float(np.mean(base_err)), 4),
        "calib_brier": round(float(np.mean(cal_err)), 4),
        "delta": round(float(np.mean(base_err) - np.mean(cal_err)), 4),
        "empty_groups": empty_groups,
        "groups": {f"{k[0]}|{k[1]}": round(v, 3) for k, v in sorted(calib_map.items())},
    }


if __name__ == "__main__":
    import json
    BASE = os.path.dirname(os.path.abspath(__file__))
    df = pd.read_csv(os.path.join(BASE, "data", "sh000001.csv"),
                     parse_dates=["date"]).set_index("date")
    ret = np.log(df["close"] / df["close"].shift(1))
    hv20 = ret.rolling(20).std() * np.sqrt(244) * 100
    hv_pctile = float((hv20.dropna() < hv20.iloc[-1]).mean()) * 100
    _vol_bucket = "高" if hv_pctile >= 66 else ("中" if hv_pctile >= 33 else "低")
    _drift_conf = 0.60 if _vol_bucket == "高" else (0.85 if _vol_bucket == "中" else 1.0)
    recs = _collect_recs(df, vol_conf=_drift_conf)
    out = run_bucketed(recs)
    oos = walkforward_2d_calib(recs)
    print("=== 第一部分：分桶 OOS 偏差 ===")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print("=== 第二部分：二维校准 OOS 提升空间 ===")
    print(json.dumps(oos, ensure_ascii=False, indent=2))
