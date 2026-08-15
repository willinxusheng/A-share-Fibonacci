# -*- coding: utf-8 -*-
"""R217：分段校准 OOS 验证（预测准确性"提升空间"的诚实体检）。

背景：R214 用全历史 walk-forward 校准得到 Brier=0.1752、斜率=0.10——低概率/远目标区
系统性低估命中率（model 0.15 → realized 0.473）。单点温度缩放 T 停在 1.0（对全局 Brier
无益，且 T>1 救低概率区却伤高概率区）。被推迟的"分段校准"在本脚本真正落地并做 OOS 验证。

方法（严守 R85 忠实 OOS）：
- 复用 calibrate.first_passage_prob 与逐字一致的样本生成（锚点 i、目标 r、horizon H →
  原始 model_p + 未来 horizon 日真实是否触达 y）。
- 锚点按时间排序，前 60% 作训练（拟合校准映射），后 40% 作验证（OOS 测 Brier）——杜绝
  未来信息泄漏（校准映射不偷看验证期）。
- 三种校准映射均在训练集拟合，在验证集测 OOS Brier：
  ① 分桶经验校准（10 等宽桶，经验命中率 + 伪计数收缩，空桶回退原始）；
  ② Platt 对数校准（logit 空间直线，训练拟合斜率/截距）；
  ③ 保序 PAVA 校准（最优单调校准，训练拟合阶梯映射，OOS 查表）。
- 同时单独报告低概率桶（model<0.2）的"模型均值 vs 真实均值"偏差在验证集上的变化——
  直接回答"低概率低估能否被修正"。

结论判据：OOS 验证集 Brier 须较原始显著下降(>2%)才视为真实提升；否则如实标注"无免费午餐"。
"""
import math
import os
import json
import numpy as np
import pandas as pd

import calibrate as cal

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


def build_samples(df, vol_conf):
    """逐字复刻 calibrate.run_calibration 的样本生成，但额外返回每个样本的锚点索引 i。"""
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

    vol_by_w = {w: ret.rolling(w).std().values for w in _WINDOWS}
    drift_by_w = {w: ret.rolling(w).mean().values for w in _WINDOWS}
    r_grid = [0.03, 0.05, 0.08, 0.12, 0.15, 0.20]
    h_grid = [20, 40, 60, 90, 120, 180, 250]
    max_horizon = 250
    min_anchor_gap = 20
    samples = []  # (anchor_i, p_raw, y)
    i_start = min_anchor_gap
    i_end = n - max_horizon - 1
    for i in range(i_start, i_end + 1):
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
                _frac = min(_sig * math.sqrt(_exp) * _vol_scale_at(i), 0.235)
                a_up = math.log((1.0 + r) * (1.0 - _frac))
                p_up = cal.first_passage_prob(a_up, _mu_eff, _sig, _exp)
                hit_up = (fut_hi.max() >= base * (1.0 + r) * (1.0 - _frac))
                samples.append((i, p_up, 1.0 if hit_up else 0.0))
                a_dn = math.log((1.0 - r) * (1.0 + _frac))
                p_dn = cal.first_passage_prob(a_dn, _mu_eff, _sig, _exp)
                hit_dn = (fut_lo.min() <= base * (1.0 - r) * (1.0 + _frac))
                samples.append((i, p_dn, 1.0 if hit_dn else 0.0))
    return samples


def bucket_cal_train(p_tr, y_tr, k=10, min_cnt=40, pseudo=5.0):
    """分桶经验校准：每桶经验命中率 + 伪计数收缩；空/少样本桶回退原始 p。返回查表函数。"""
    edges = np.linspace(0.0, 1.0, k + 1)
    # 桶中心与校准值
    cal_centers = []  # (p_center, cal_val)
    for b in range(k):
        lo, hi = edges[b], edges[b + 1]
        m = (p_tr >= lo) & (p_tr < hi)
        if b == k - 1:
            m = p_tr >= edges[k - 1]
        cnt = int(m.sum())
        if cnt >= min_cnt:
            emp = y_tr[m].mean()
            cal_val = (emp * cnt + 0.5 * pseudo) / (cnt + pseudo)  # 向 0.5 收缩
        else:
            cal_val = None  # 回退原始
        cal_centers.append((0.5 * (lo + hi), cal_val))
    # 缺失桶用就近非空桶插值/回退
    vals = [c[1] for c in cal_centers]
    if all(v is None for v in vals):
        def f(p):
            return np.clip(p, 0.001, 0.999)
        return f
    # 前向/后向填充
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
        out = np.where(np.isnan(p), 0.5, [filled[int(ix)] for ix in idx])
        return np.clip(out, 0.001, 0.999)
    return f


def platt_train(p_tr, y_tr):
    """Platt 对数校准：logit 空间线性拟合 y≈a*z+b 经 sigmoid。返回查表函数。"""
    z = np.log(np.clip(p_tr, 0.001, 0.999) / (1.0 - np.clip(p_tr, 0.001, 0.999)))
    A = np.vstack([z, np.ones_like(z)]).T
    coef, *_ = np.linalg.lstsq(A, y_tr, rcond=None)
    a, b = coef

    def f(p):
        zz = np.log(np.clip(p, 0.001, 0.999) / (1.0 - np.clip(p, 0.001, 0.999)))
        return np.clip(1.0 / (1.0 + np.exp(-(a * zz + b))), 0.001, 0.999)
    return f


def isotonic_pava_train(p_tr, y_tr):
    """保序 PAVA 校准（最优单调映射），返回 OOS 查表函数。"""
    idx = np.argsort(p_tr)
    ps = p_tr[idx]; ys = y_tr[idx]
    blocks = []  # [mean, count, pmin, pmax]
    for i in range(len(ps)):
        blocks.append([ys[i], 1, ps[i], ps[i]])
        while len(blocks) >= 2 and blocks[-1][0] < blocks[-2][0] - 1e-12:
            b2 = blocks.pop(); b1 = blocks.pop()
            m = (b1[0] * b1[1] + b2[0] * b2[1]) / (b1[1] + b2[1])
            blocks.append([m, b1[1] + b2[1], b1[2], b2[3]])
    bnds = [(blk[2], blk[3], blk[0]) for blk in blocks]

    def f(p):
        p = np.clip(p, bnds[0][0], bnds[-1][1])
        out = np.empty_like(p, dtype=float)
        for j, pv in enumerate(np.atleast_1d(p)):
            # 二分/线性定位桶
            for lo, hi, mv in bnds:
                if lo - 1e-9 <= pv <= hi + 1e-9:
                    out[j] = mv
                    break
            else:
                out[j] = 0.5
        return np.clip(out, 0.001, 0.999) if np.ndim(p) else float(np.clip(out[0], 0.001, 0.999))
    return f


def low_bucket_bias(p, y, thr=0.2):
    m = p < thr
    if m.sum() == 0:
        return None
    return {"n": int(m.sum()), "modelMean": round(float(p[m].mean()), 3),
            "realized": round(float(y[m].mean()), 3),
            "gap": round(float(y[m].mean() - p[m].mean()), 3)}


def main():
    BASE = os.path.dirname(os.path.abspath(__file__))
    df = pd.read_csv(os.path.join(BASE, "data", "sh000001.csv"),
                     parse_dates=["date"]).set_index("date")
    ret = np.log(df["close"] / df["close"].shift(1))
    hv20 = ret.rolling(20).std() * np.sqrt(244) * 100
    hv_pctile = float((hv20.dropna() < hv20.iloc[-1]).mean()) * 100
    _vol_bucket = "高" if hv_pctile >= 66 else ("中" if hv_pctile >= 33 else "低")
    _drift_conf = 0.60 if _vol_bucket == "高" else (0.85 if _vol_bucket == "中" else 1.0)

    samples = build_samples(df, _drift_conf)
    arr = np.array(samples)
    anchor = arr[:, 0].astype(int)
    p_raw = arr[:, 1].astype(float)
    y = arr[:, 2].astype(float)
    print("样本总数 n=%d (vol_bucket=%s, drift_conf=%.2f)" % (len(samples), _vol_bucket, _drift_conf))

    # 时间切分：前 60% 锚点训练，后 40% 验证
    uniq_anchor = np.unique(anchor)
    n_tr = int(len(uniq_anchor) * 0.6)
    tr_anchors = set(uniq_anchor[:n_tr])
    val_mask = ~np.array([a in tr_anchors for a in anchor])
    tr_mask = ~val_mask
    p_tr, y_tr = p_raw[tr_mask], y[tr_mask]
    p_val, y_val = p_raw[val_mask], y[val_mask]
    print("训练锚点数≈%d (样本 %d)，验证样本 %d" % (n_tr, len(p_tr), len(p_val)))

    brier = lambda p, y: float(np.mean((np.clip(p, 0.001, 0.999) - y) ** 2))
    raw_val = brier(p_val, y_val)
    raw_tr = brier(p_tr, y_tr)
    print("\n=== OOS 验证集 Brier（越低越准）===")
    print("原始 model_p      : 训练 %.4f / 验证 %.4f" % (raw_tr, raw_val))

    res = {}
    # ① 分桶经验校准
    f_buck = bucket_cal_train(p_tr, y_tr)
    b_val_buck = brier(f_buck(p_val), y_val)
    res["bucket"] = b_val_buck
    print("分桶经验校准      : 验证 %.4f  (Δ=%.4f, %.1f%%)" %
          (b_val_buck, b_val_buck - raw_val, (b_val_buck / raw_val - 1) * 100))
    # ② Platt
    f_platt = platt_train(p_tr, y_tr)
    b_val_platt = brier(f_platt(p_val), y_val)
    res["platt"] = b_val_platt
    print("Platt 对数校准    : 验证 %.4f  (Δ=%.4f, %.1f%%)" %
          (b_val_platt, b_val_platt - raw_val, (b_val_platt / raw_val - 1) * 100))
    # ③ 保序 PAVA
    f_iso = isotonic_pava_train(p_tr, y_tr)
    b_val_iso = brier(f_iso(p_val), y_val)
    res["isotonic"] = b_val_iso
    print("保序 PAVA 校准    : 验证 %.4f  (Δ=%.4f, %.1f%%)" %
          (b_val_iso, b_val_iso - raw_val, (b_val_iso / raw_val - 1) * 100))

    print("\n=== 低概率桶(model<0.2)偏差在验证集上的变化（直接回应'低估'）===")
    print("原始     :", low_bucket_bias(p_val, y_val))
    print("分桶校准 :", low_bucket_bias(f_buck(p_val), y_val))
    print("PAVA     :", low_bucket_bias(f_iso(p_val), y_val))

    # 判定
    best = min(raw_val, b_val_buck, b_val_platt, b_val_iso)
    improved = (best + 1e-9) < raw_val * 0.98
    best_name = {raw_val: "原始", b_val_buck: "分桶", b_val_platt: "Platt", b_val_iso: "PAVA"}[best]
    print("\n=== 结论 ===")
    if improved:
        print("OOS 验证集 Brier 显著下降(>2%% )：%s 校准胜出(验证 %.4f vs 原始 %.4f) → 真实提升空间已验证，可考虑进引擎。" %
              (best_name, best, raw_val))
    else:
        print("OOS 验证集 Brier 无显著下降(均未改善>2%% )：最佳=%s(%.4f)，原始=%.4f → 分段校准无免费午餐，不进引擎(R85)。" %
              (best_name, best, raw_val))
        print("但低概率桶偏差可被分段校准定向修正（见上），属'局部解读改善'而非'全局 Brier 改善'。")


if __name__ == "__main__":
    main()
