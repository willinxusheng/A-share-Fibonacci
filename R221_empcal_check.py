# -*- coding: utf-8 -*-
"""R221：主导档（walk-forward 实证命中率 _hr）分段校准 OOS 验证。

背景：R214/R217 只验证了"漂移先验"侧（线上仅 1 个目标走该档）。线上 8/9 目标走
「回测实证」档——其预测值 _hr 来自 build_data._empirical_rates 的 walk-forward 经验频率。
R90 注释已记录该 _hr 系统性低估命中率（均值 0.13 vs 实测 0.25），仅用增大 _FUSE_K 部分缓解。
本脚本对主导档 _hr 本身做分段校准（如 R217 对先验那样），用严格时间外样本验证 Brier 是否下降。

方法（严守 R85 忠实 OOS，与 R217 同框架）：
- 复刻 _empirical_rates 的样本生成：锚点 i、方向、目标幅度 r、horizon H → 未来 H 日真实触达 y。
- 锚点按时间排序，前 60% 作训练（拟合校准映射 + 计算"模型在训练期学到的实证命中率 _hr_train"），
  后 40% 作验证（用 _hr_train 预测验证期触达，OOS 测 Brier）——杜绝未来信息泄漏。
- 三种校准映射均在训练集拟合，在验证集测 OOS Brier：分桶经验校准 / Platt / 保序 PAVA。
- 单独报告低概率桶（model<0.2）的"模型均值 vs 真实均值"偏差在验证集上的变化。

结论判据：OOS 验证集 Brier 须较原始显著下降(>2%)才视为真实提升；否则如实标注"无免费午餐"。
"""
import math
import os
import json
import numpy as np
import pandas as pd

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


def vol_scale_from_data():
    BASE = os.path.dirname(os.path.abspath(__file__))
    src = open(os.path.join(BASE, "data", "data.js"), encoding="utf-8").read()
    D = json.loads(__import__("re").search(r'window\.FIB_DATA\s*=\s*(\{.*\})\s*;?\s*$', src, __import__("re").S).group(1))
    return float(D["volRegime"]["bandScale"])


def generate_samples(df, vol_scale):
    ret = np.log(df["close"] / df["close"].shift(1))
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    n = len(df)
    vol_by_w = {w: ret.rolling(w).std().values for w in _WINDOWS}
    r_grid = [0.03, 0.05, 0.08, 0.12, 0.15, 0.20]
    h_grid = [20, 40, 60, 90, 120, 180, 250]
    max_horizon = 250
    min_anchor_gap = 20
    # 每行：(anchor_i, dir(1 up/-1 dn), r, H, y_touch)
    rows = []
    for i in range(min_anchor_gap, n - max_horizon - 1 + 1):
        base = close[i]
        for H in h_grid:
            if i + H >= n:
                continue
            w = min(_WINDOWS[-1], max(_WINDOWS[0], H))
            sigma = _interp({ww: vol_by_w[ww][i] for ww in _WINDOWS}, w)
            if sigma is None or math.isnan(sigma) or sigma <= 0:
                continue
            _exp = max(10, int(round(H)))
            _frac = min(sigma * math.sqrt(_exp) * vol_scale, 0.235)
            fut_hi = high[i + 1: i + 1 + H]
            fut_lo = low[i + 1: i + 1 + H]
            for r in r_grid:
                hit_up = (fut_hi.max() >= base * (1.0 + r) * (1.0 - _frac))
                hit_dn = (fut_lo.min() <= base * (1.0 - r) * (1.0 + _frac))
                rows.append((i, 1, r, H, 1.0 if hit_up else 0.0))
                rows.append((i, -1, r, H, 1.0 if hit_dn else 0.0))
    return rows


def bucket_cal_train(p_tr, y_tr, k=10, min_cnt=40, pseudo=5.0):
    edges = np.linspace(0.0, 1.0, k + 1)
    cal_centers = []
    for b in range(k):
        lo, hi = edges[b], edges[b + 1]
        m = (p_tr >= lo) & (p_tr < hi)
        if b == k - 1:
            m = p_tr >= edges[k - 1]
        cnt = int(m.sum())
        if cnt >= min_cnt:
            emp = y_tr[m].mean()
            cal_val = (emp * cnt + 0.5 * pseudo) / (cnt + pseudo)
        else:
            cal_val = None
        cal_centers.append((0.5 * (lo + hi), cal_val))
    vals = [c[1] for c in cal_centers]
    if all(v is None for v in vals):
        return lambda p: np.clip(p, 0.001, 0.999)
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
    z = np.log(np.clip(p_tr, 0.001, 0.999) / (1.0 - np.clip(p_tr, 0.001, 0.999)))
    A = np.vstack([z, np.ones_like(z)]).T
    coef, *_ = np.linalg.lstsq(A, y_tr, rcond=None)
    a, b = coef

    def f(p):
        zz = np.log(np.clip(p, 0.001, 0.999) / (1.0 - np.clip(p, 0.001, 0.999)))
        return np.clip(1.0 / (1.0 + np.exp(-(a * zz + b))), 0.001, 0.999)
    return f


def isotonic_pava_train(p_tr, y_tr):
    idx = np.argsort(p_tr)
    ps = p_tr[idx]; ys = y_tr[idx]
    blocks = []
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
            placed = False
            for lo, hi, mv in bnds:
                if lo - 1e-9 <= pv <= hi + 1e-9:
                    out[j] = mv; placed = True; break
            if not placed:
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
    vol_scale = vol_scale_from_data()
    rows = generate_samples(df, vol_scale)
    print("样本总数 n=%d (bandScale=%.3f)" % (len(rows), vol_scale))

    # 锚点时间切分
    anchors = sorted(set(r[0] for r in rows))
    n_tr = int(len(anchors) * 0.6)
    tr_set = set(anchors[:n_tr])
    # 每个 (dir,r,H) 桶在训练期的实证命中率 _hr_train（模型用历史学到的预测）
    from collections import defaultdict
    bucket_y = defaultdict(list)
    for r in rows:
        if r[0] in tr_set:
            bucket_y[(r[1], r[2], r[3])].append(r[4])
    hr_train = {k: (sum(v) / len(v) if v else 0.5) for k, v in bucket_y.items()}

    # 组装训练/验证样本：p = 该桶的 _hr_train，y = 该样本真实触达
    p_tr, y_tr, p_val, y_val = [], [], [], []
    for r in rows:
        _hr = hr_train.get((r[1], r[2], r[3]), 0.5)
        if r[0] in tr_set:
            p_tr.append(_hr); y_tr.append(r[4])
        else:
            p_val.append(_hr); y_val.append(r[4])
    p_tr = np.array(p_tr); y_tr = np.array(y_tr)
    p_val = np.array(p_val); y_val = np.array(y_val)
    print("训练样本 %d，验证样本 %d" % (len(p_tr), len(p_val)))

    brier = lambda p, y: float(np.mean((np.clip(p, 0.001, 0.999) - y) ** 2))
    raw_val = brier(p_val, y_val)
    raw_tr = brier(p_tr, y_tr)
    print("\n=== OOS 验证集 Brier（越低越准；主导档 _hr 本身）===")
    print("原始 _hr(未校准)   : 训练 %.4f / 验证 %.4f" % (raw_tr, raw_val))

    f_buck = bucket_cal_train(p_tr, y_tr)
    b_val_buck = brier(f_buck(p_val), y_val)
    print("分桶经验校准       : 验证 %.4f  (Δ=%.4f, %.1f%%)" %
          (b_val_buck, b_val_buck - raw_val, (b_val_buck / raw_val - 1) * 100))
    f_platt = platt_train(p_tr, y_tr)
    b_val_platt = brier(f_platt(p_val), y_val)
    print("Platt 对数校准     : 验证 %.4f  (Δ=%.4f, %.1f%%)" %
          (b_val_platt, b_val_platt - raw_val, (b_val_platt / raw_val - 1) * 100))
    f_iso = isotonic_pava_train(p_tr, y_tr)
    b_val_iso = brier(f_iso(p_val), y_val)
    print("保序 PAVA 校准     : 验证 %.4f  (Δ=%.4f, %.1f%%)" %
          (b_val_iso, b_val_iso - raw_val, (b_val_iso / raw_val - 1) * 100))

    print("\n=== 低概率桶(model<0.2)偏差在验证集上的变化 ===")
    print("原始     :", low_bucket_bias(p_val, y_val))
    print("分桶校准 :", low_bucket_bias(f_buck(p_val), y_val))
    print("PAVA     :", low_bucket_bias(f_iso(p_val), y_val))

    best = min(raw_val, b_val_buck, b_val_platt, b_val_iso)
    improved = (best + 1e-9) < raw_val * 0.98
    best_name = {raw_val: "原始", b_val_buck: "分桶", b_val_platt: "Platt", b_val_iso: "PAVA"}[best]
    print("\n=== 结论 ===")
    if improved:
        print("主导档 _hr OOS Brier 显著下降(>2%% )：%s 校准胜出(验证 %.4f vs 原始 %.4f) → 真实提升空间已验证，可考虑进引擎（在融合前对 _hr 校准）。" %
              (best_name, best, raw_val))
    else:
        print("主导档 _hr OOS Brier 无显著下降(>2%% )：最佳=%s(%.4f)，原始=%.4f → 分段校准对主导档无免费午餐，不进引擎(R85)。" %
              (best_name, best, raw_val))
        print("提示：R90 已用 _FUSE_K=20 把融合拉向校准良好的先验(实证率系统性低估)；若本测试也证伪分段校准，则该低估已被融合充分吸收，无需额外动作。")


if __name__ == "__main__":
    main()
