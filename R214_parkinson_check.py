# -*- coding: utf-8 -*-
"""R214：首达概率 vol 口径候选验证（R85 纪律：OOS 确降 Brier 才允许动引擎）。

背景（本轮深度回测发现）：calibrate.run_calibration 全历史 walk-forward 显示
原始漂移先验在低概率区系统性低估触达率（model 15% → 实测 47%，斜率 0.10）。
理论归因：首达事件由【盘中 high/low 触及】定义，但 sigma 用 close-close 对数收益估计——
close-close vol 天然低于日内高低点振幅（Parkinson vol），障碍模型因此把"摸得到"估成"摸不到"。

候选修复：sigma 换 Parkinson 高低价 vol：
    sigma_park = sqrt( E[ln(H/L)^2] / (4 ln 2) )   （滚动窗口均值，逐日）
band 的 _frac 同步换口径（与生产公式一致性：模型概率与展示带同源）。

本脚本只做忠实对照：同一批锚点/目标/实测，分别用 close-close 与 Parkinson 两套 sigma
跑首达概率 + band-edge 命中，报两套 Brier / 分桶校准。不改动任何生产文件。
"""
import math
import os

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def first_passage_prob(a, mu, sigma, T):
    """与 build_data.py _enrich / calibrate.py 逐字一致。"""
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
    b = -a
    d1 = (b + mu * T) / sv
    d2 = (mu * T - b) / sv
    return 1.0 - _norm_cdf(d1) + math.exp(exo) * _norm_cdf(d2)


_WINDOWS = [20, 60, 120, 250]


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


def run(df, vol_mode="cc", vol_conf=1.0, max_horizon=250, min_anchor_gap=20):
    """vol_mode: 'cc'=close-close（现口径）；'park'=Parkinson 高低价；'blend'=两者几何均值。"""
    ret = np.log(df["close"] / df["close"].shift(1))
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    n = len(df)
    hv20_full = ret.rolling(20).std() * np.sqrt(244) * 100

    def _vol_scale_at(idx):
        if idx < 0 or idx >= len(hv20_full) or pd.isna(hv20_full.iloc[idx]):
            return 1.0
        _pct = float((hv20_full.iloc[:idx + 1].dropna() < hv20_full.iloc[idx]).mean()) * 100
        return 1.15 if _pct >= 66 else (1.0 if _pct >= 33 else 0.88)

    # close-close 逐日 vol（与 calibrate 同）
    vol_cc = {w: ret.rolling(w).std().values for w in _WINDOWS}
    # Parkinson 逐日 vol：sqrt( rolling_mean(ln(H/L)^2) / (4 ln2) )
    _hl2 = np.log(df["high"] / df["low"]) ** 2
    vol_pk = {w: np.sqrt(_hl2.rolling(w).mean() / (4.0 * math.log(2.0))).values
              for w in _WINDOWS}
    drift_by_w = {w: ret.rolling(w).mean().values for w in _WINDOWS}

    def _pick(table, w, i):
        # w 为 horizon（可能落在窗口之间），与 calibrate 一致走 _interp 插值表
        if vol_mode == "cc":
            return _interp({ww: vol_cc[ww][i] for ww in _WINDOWS}, w)
        if vol_mode == "park":
            return _interp({ww: vol_pk[ww][i] for ww in _WINDOWS}, w)
        # blend：几何均值（vol 量级折中，避免 Parkinson 全量过激）
        a = _interp({ww: vol_cc[ww][i] for ww in _WINDOWS}, w)
        b = _interp({ww: vol_pk[ww][i] for ww in _WINDOWS}, w)
        if a is None or b is None or math.isnan(a) or math.isnan(b) or a <= 0 or b <= 0:
            return float("nan")
        return math.sqrt(a * b)

    r_grid = [0.03, 0.05, 0.08, 0.12, 0.15, 0.20]
    h_grid = [20, 40, 60, 90, 120, 180, 250]
    samples = []
    i_start, i_end = min_anchor_gap, n - max_horizon - 1
    for i in range(i_start, i_end + 1):
        base = close[i]
        for H in h_grid:
            if i + H >= n:
                continue
            w = min(_WINDOWS[-1], max(_WINDOWS[0], H))
            sig = _pick(None, w, i)
            mu = _interp({ww: drift_by_w[ww][i] for ww in _WINDOWS}, w) * vol_conf
            if sig is None or math.isnan(sig) or sig <= 0 or math.isnan(mu):
                continue
            _exp = max(10, int(round(H)))
            _frac = min(sig * math.sqrt(_exp) * _vol_scale_at(i), 0.235)
            fut_hi = high[i + 1: i + 1 + H]
            fut_lo = low[i + 1: i + 1 + H]
            for r in r_grid:
                a_up = math.log((1.0 + r) * (1.0 - _frac))
                p_up = first_passage_prob(a_up, mu, sig, _exp)
                hit_up = (fut_hi.max() >= base * (1.0 + r) * (1.0 - _frac))
                samples.append((p_up, 1.0 if hit_up else 0.0))
                a_dn = math.log((1.0 - r) * (1.0 + _frac))
                p_dn = first_passage_prob(a_dn, mu, sig, _exp)
                hit_dn = (fut_lo.min() <= base * (1.0 - r) * (1.0 + _frac))
                samples.append((p_dn, 1.0 if hit_dn else 0.0))

    ps = np.clip(np.array([s[0] for s in samples]), 0.001, 0.999)
    ys = np.array([s[1] for s in samples])
    brier = float(np.mean((ps - ys) ** 2))
    z = np.log(ps / (1.0 - ps))
    slope = float(np.polyfit(z, ys, 1)[0]) if np.std(z) > 1e-6 else None
    # 低概率区(0.1-0.4)校准残差：本轮重点观测区
    m = (ps >= 0.1) & (ps < 0.4)
    low_gap = float((ys[m].mean() - ps[m].mean())) if m.sum() > 0 else None
    return {"mode": vol_mode, "n": len(samples), "brier": round(brier, 4),
            "slope": round(slope, 3) if slope else None,
            "lowProb真实-模型": round(low_gap, 3) if low_gap is not None else None}


if __name__ == "__main__":
    import json
    df = pd.read_csv(os.path.join(BASE, "data", "sh000001.csv"),
                     parse_dates=["date"]).set_index("date")
    ret = np.log(df["close"] / df["close"].shift(1))
    hv20 = ret.rolling(20).std() * np.sqrt(244) * 100
    hv_pctile = float((hv20.dropna() < hv20.iloc[-1]).mean()) * 100
    _vol_bucket = "高" if hv_pctile >= 66 else ("中" if hv_pctile >= 33 else "低")
    _drift_conf = 0.60 if _vol_bucket == "高" else (0.85 if _vol_bucket == "中" else 1.0)
    print("当前 vol bucket =", _vol_bucket, " drift_conf =", _drift_conf)
    res = [run(df, mode, vol_conf=_drift_conf) for mode in ("cc", "park", "blend")]
    print(json.dumps(res, ensure_ascii=False, indent=2))
    b = {r["mode"]: r["brier"] for r in res}
    print("\n判定（R85）：候选须同时满足 brier 更低 且 slope 更接近 1，才允许进引擎。")
    print("  park  ΔBrier = %+.4f ; blend ΔBrier = %+.4f"
          % (b["park"] - b["cc"], b["blend"] - b["cc"]))
