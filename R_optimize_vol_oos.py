# -*- coding: utf-8 -*-
"""波动率估计器升级 OOS 验证（R285）：对比不同 sigma 估计器对首达概率预测准确性的影响。

背景：build_data._vol_for 当前用 ret.rolling(w).std()（简单等权滚动标准差）作底层波动率估计，
对每个 horizon 窗口 w 取最近 w 日样本方差。该估计对波动率聚集性(GARCH 效应)无建模、对近期
波动变化反应滞后。候选：RiskMetrics EWMA(λ=0.94) 单一轨迹（捕捉波动率时变），以及 EWMA 与
长期方差混合（保留 term structure 的均值回归精神）。

方法（严守 R85 忠实 OOS，复刻 R217 框架）：
- 复用 build_samples 的样本生成（锚点 i、目标 r、horizon H → 裸 model_p + 未来 H 日真实触达 y），
  首达概率用与 build_data._drift_prior_prob 逐字一致的反射原理公式。
- 仅隔离变量：sigma 估计器（baseline=rolling term structure / ewma / ewma_mr），mu、vol_scale、drift 不变。
- 锚点按时间排序，前 60% 训练拟合校准映射（分桶 + PAVA），后 40% 作 OOS 验证测 Brier。
- 判定：OOS Brier 降 >2% 才算真实提升（否则诚实标注"无免费午餐"，不进引擎）。

结论驱动：若某候选确降 Brier，则改 build_data._vol_by_w 落实；否则保留现状（R85 诚实纪律）。
"""
import os
import math
import numpy as np
import pandas as pd

_WINDOWS = [20, 60, 120, 250]
_R_GRID = [0.03, 0.05, 0.08, 0.12, 0.15, 0.20]
_H_GRID = [20, 40, 60, 90, 120, 180, 250]
_MAX_H = 250
_MIN_GAP = 20
_LAMBDA = 0.94          # RiskMetrics
_PHI = 0.10             # EWMA 与长期方差混合比（均值回归分量）


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def first_passage(base, price, T, sigma, mu_eff, vol_scale):
    """与 build_data._drift_prior_prob 逐字一致的反射原理首达概率（裸，0-1）。"""
    _exp = max(10, int(round(T)))
    _frac = min(sigma * math.sqrt(_exp) * vol_scale, 0.235)
    _sig = sigma * math.sqrt(_exp) if sigma > 0 else 1e-9
    _dir = 1 if price >= base else -1
    if _dir > 0:
        _barrier = price * (1.0 - _frac)
        if base >= _barrier:
            return 1.0
        _a = math.log(_barrier / base)
        _d1 = (_a - mu_eff * _exp) / _sig
        _d2 = (-_a - mu_eff * _exp) / _sig
        _exo = max(-50.0, min(50.0, 2.0 * mu_eff * _a / (sigma ** 2 + 1e-12)))
        return 1.0 - _norm_cdf(_d1) + math.exp(_exo) * _norm_cdf(_d2)
    else:
        _barrier = price * (1.0 + _frac)
        if base <= _barrier:
            return 1.0
        _a = math.log(_barrier / base)
        _b = -_a
        _d1 = (_b + mu_eff * _exp) / _sig
        _d2 = (mu_eff * _exp - _b) / _sig
        _exo = max(-50.0, min(50.0, -2.0 * mu_eff * _b / (sigma ** 2 + 1e-12)))
        return 1.0 - _norm_cdf(_d1) + math.exp(_exo) * _norm_cdf(_d2)


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


def build_vol_models(ret, n):
    """返回三种 sigma 模型的查询函数 sigma_model(name, w, i)。"""
    # baseline: rolling std per window（当前 build_data 实现）
    roll = {w: ret.rolling(w).std().values for w in _WINDOWS}
    # EWMA 单一轨迹（RiskMetrics）
    ewma_var = np.empty(n); ewma_var[:] = np.nan
    sq = ret.values ** 2
    # 初始化为前 60 日样本方差
    ewma_var[_MIN_GAP] = np.nanvar(ret.values[:_MIN_GAP])
    for i in range(_MIN_GAP + 1, n):
        ewma_var[i] = _LAMBDA * ewma_var[i - 1] + (1 - _LAMBDA) * sq[i]
    ewma = np.sqrt(ewma_var)
    longrun_var = float(np.nanvar(ret.values))
    ewma_mr = np.sqrt((1 - _PHI) * ewma_var + _PHI * longrun_var)

    def baseline(w, i):
        # 复刻 build_data._vol_for：在窗口间插值，取锚点 i 处的 horizon-matched 滚动 std
        return _interp({ww: roll[ww][i] for ww in _WINDOWS}, w)

    def ewma_only(w, i):
        return ewma[i]

    def ewma_meanrev(w, i):
        return ewma_mr[i]

    return {"baseline": baseline, "ewma": ewma_only, "ewma_mr": ewma_meanrev}, ewma


def build_samples(df, sigma_model):
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    n = len(df)
    ret = np.log(df["close"] / df["close"].shift(1))
    hv20_full = ret.rolling(20).std() * np.sqrt(244) * 100
    drift_by_w = {w: ret.rolling(w).mean().values for w in _WINDOWS}

    def _vol_scale_at(idx):
        if idx < 0 or idx >= len(hv20_full) or pd.isna(hv20_full.iloc[idx]):
            return 1.0
        _pct = float((hv20_full.iloc[:idx + 1].dropna() < hv20_full.iloc[idx]).mean()) * 100
        return 1.15 if _pct >= 66 else (1.0 if _pct >= 33 else 0.88)

    hv20 = ret.rolling(20).std() * np.sqrt(244) * 100
    hv_pctile = float((hv20.dropna() < hv20.iloc[-1]).mean()) * 100
    _vol_bucket = "高" if hv_pctile >= 66 else ("中" if hv_pctile >= 33 else "低")
    _drift_conf = 0.60 if _vol_bucket == "高" else (0.85 if _vol_bucket == "中" else 1.0)

    samples = []
    for i in range(_MIN_GAP, n - _MAX_H - 1):
        base = close[i]
        for H in _H_GRID:
            if i + H >= n:
                continue
            sigma = sigma_model(H, i)
            if sigma is None or math.isnan(sigma) or sigma <= 0:
                continue
            _exp = max(10, int(round(H)))
            mu_eff = _interp({ww: drift_by_w[ww][i] for ww in _WINDOWS}, H) * _drift_conf
            vs = _vol_scale_at(i)
            fut_hi = high[i + 1:i + 1 + H]
            fut_lo = low[i + 1:i + 1 + H]
            for r in _R_GRID:
                _frac = min(sigma * math.sqrt(_exp) * vs, 0.235)
                p_up = first_passage(base, base * (1.0 + r), H, sigma, mu_eff, vs)
                hit_up = (fut_hi.max() >= base * (1.0 + r) * (1.0 - _frac))
                samples.append((i, p_up, 1.0 if hit_up else 0.0))
                p_dn = first_passage(base, base * (1.0 - r), H, sigma, mu_eff, vs)
                hit_dn = (fut_lo.min() <= base * (1.0 - r) * (1.0 + _frac))
                samples.append((i, p_dn, 1.0 if hit_dn else 0.0))
    return np.array(samples, dtype=float), _vol_bucket, _drift_conf


def bucket_cal_train(p_tr, y_tr, k=10, min_cnt=40, pseudo=5.0):
    edges = np.linspace(0.0, 1.0, k + 1)
    vals = []
    for b in range(k):
        lo, hi = edges[b], edges[b + 1]
        m = (p_tr >= lo) & (p_tr < hi)
        if b == k - 1:
            m = p_tr >= edges[k - 1]
        cnt = int(m.sum())
        vals.append((y_tr[m].mean() * cnt + 0.5 * pseudo) / (cnt + pseudo) if cnt >= min_cnt else None)
    filled = []
    last = 0.5
    for v in vals:
        if v is None:
            filled.append(last)
        else:
            filled.append(v); last = v
    for i in range(len(filled) - 1, -1, -1):
        if vals[i] is None:
            filled[i] = last
        else:
            last = vals[i]

    def f(p):
        p = np.clip(p, 0.0, 1.0)
        idx = np.clip(np.digitize(p, edges) - 1, 0, k - 1)
        return np.clip(np.array([filled[int(ix)] for ix in idx]), 0.001, 0.999)
    return f


def isotonic_pava_train(p_tr, y_tr):
    idx = np.argsort(p_tr)
    ps = p_tr[idx]; ys = y_tr[idx]
    blocks = []
    for i in range(len(ps)):
        blocks.append([ys[i], 1])
        while len(blocks) >= 2 and blocks[-1][0] < blocks[-2][0] - 1e-12:
            b2 = blocks.pop(); b1 = blocks.pop()
            blocks.append([(b1[0] * b1[1] + b2[0] * b2[1]) / (b1[1] + b2[1]), b1[1] + b2[1]])
    bnds = []
    cum = 0
    for blk in blocks:
        cum += blk[1]
        bnds.append((ps[cum - blk[1]], ps[cum - 1], blk[0]))

    def f(p):
        p = np.clip(np.atleast_1d(p), bnds[0][0], bnds[-1][1])
        out = np.empty_like(p, dtype=float)
        for j, pv in enumerate(p):
            for lo, hi, mv in bnds:
                if lo - 1e-9 <= pv <= hi + 1e-9:
                    out[j] = mv; break
            else:
                out[j] = 0.5
        return np.clip(out, 0.001, 0.999)
    return f


def main():
    BASE = os.path.dirname(os.path.abspath(__file__))
    df = pd.read_csv(os.path.join(BASE, "data", "sh000001.csv"), parse_dates=["date"]).set_index("date")
    vol_models, _ = build_vol_models(np.log(df["close"] / df["close"].shift(1)), len(df))

    brier = lambda p, y: float(np.mean((np.clip(p, 0.001, 0.999) - y) ** 2))
    results = {}
    for name, model in vol_models.items():
        arr, vb, dc = build_samples(df, model)
        anchor = arr[:, 0].astype(int)
        p_raw = arr[:, 1]; y = arr[:, 2]
        uniq = np.unique(anchor)
        n_tr = int(len(uniq) * 0.6)
        tr_set = set(uniq[:n_tr])
        tr_mask = np.array([a in tr_set for a in anchor])
        p_tr, y_tr = p_raw[tr_mask], y[tr_mask]
        p_val, y_val = p_raw[~tr_mask], y[~tr_mask]
        raw_val = brier(p_val, y_val)
        f_b = bucket_cal_train(p_tr, y_tr)
        b_buck = brier(f_b(p_val), y_val)
        f_i = isotonic_pava_train(p_tr, y_tr)
        b_iso = brier(f_i(p_val), y_val)
        results[name] = (raw_val, b_buck, b_iso)
        print("[%-8s] n=%d vb=%s dc=%.2f | 原始=%.4f 分桶=%.4f PAVA=%.4f" %
              (name, len(arr), vb, dc, raw_val, b_buck, b_iso))

    base_raw, base_buck, base_iso = results["baseline"]
    print("\n=== OOS 验证集 Brier 对比（越低越准；基线=baseline 滚动 term structure）===")
    for name, (rv, bb, bi) in results.items():
        best = min(rv, bb, bi)
        delta = (best / base_iso - 1) * 100
        tag = "[优] 优于基线" if best < base_iso * 0.98 else "[=] 无显著改善"
        print("  %-8s 最佳=%.4f (Δ=%.1f%%) %s" % (name, best, delta, tag))
    best_overall = min(min(rv, bb, bi) for rv, bb, bi in results.values())
    if best_overall < base_iso * 0.98:
        print("\n结论：存在确降 Brier(>2%%) 的波动率估计器 → 可落实进 build_data._vol_by_w（R85 通过）。")
    else:
        print("\n结论：无免费午餐——候选波动率估计器在 OOS 上未显著优于 baseline 滚动 term structure。"
              "term structure 的均值回归假设有价值，保留现状（R85 诚实纪律，不进引擎）。")


if __name__ == "__main__":
    main()
