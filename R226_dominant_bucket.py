# -*- coding: utf-8 -*-
"""R226：线上主导档「回测实证」_hr 的分桶 OOS 偏差体检。

动机（R225 遗留的关键未决问题）：R225 发现"近/小幅 + 长周期目标被系统性严重低估"
（r=3% gap=+0.423、长周期 +0.26，OOS 可降 Brier −36%），但该偏差定位在『漂移模型
原始先验』档。线上 8/9 目标走『回测实证』档 _hr（来自 _empirical_rates 的 walk-forward
经验频率，是不同机制）。本脚本严格复用 R221 的 _empirical_rates 样本生成（=线上主导档
口径：固定 vol_scale、按 (dir,r,H) 桶的历史实证命中率作模型 p），在 60/40 时间外切分
的验证集上按 H桶×r桶 分桶输出 gap/slope/Brier，回答：

  → 线上主导档 _hr 是否也有"近目标低估"？若有 → 真影响线上用户的提升空间；若否 →
    R225 偏差仅限漂移档(线上仅 1 个低概率目标)，现有线上展示已是最优。

全部只读 data/，不改引擎、不改生产概率。严守 R85：仅体检，不部署。
"""
import importlib.util
import os

import numpy as np
import pandas as pd

_BASE = os.path.dirname(os.path.abspath(__file__))


def _load_r221():
    spec = importlib.util.spec_from_file_location(
        "r221", os.path.join(_BASE, "R221_empcal_check.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _hb(H):
    if H <= 40:
        return "短(<=40)"
    if H <= 90:
        return "中(40-90]"
    return "长(>90)"


def _rb(r):
    return "近(<=8%)" if r <= 0.08 else "远(>=12%)"


def _slope(ps, ys):
    ps = np.clip(ps, 0.001, 0.999)
    z = np.log(ps / (1.0 - ps))
    if np.std(z) > 1e-6:
        return float(np.polyfit(z, ys, 1)[0])
    return None


def _agg(p, y, m):
    if m.sum() == 0:
        return None
    pp = np.clip(p[m], 0.001, 0.999)
    yy = y[m]
    sl = _slope(pp, yy)
    return {
        "n": int(m.sum()),
        "brier": round(float(np.mean((pp - yy) ** 2)), 4),
        "modelMean": round(float(pp.mean()), 3),
        "realized": round(float(yy.mean()), 3),
        "gap": round(float(yy.mean() - pp.mean()), 3),
        "slope": round(sl, 3) if sl is not None else None,
    }


def main():
    import json
    import re
    M = _load_r221()
    df = pd.read_csv(os.path.join(_BASE, "data", "sh000001.csv"),
                     parse_dates=["date"]).set_index("date")
    vol_scale = M.vol_scale_from_data()
    rows = M.generate_samples(df, vol_scale)

    # 锚点时间切分（同 R221：前 60% 训练、后 40% 验证）
    anchors = sorted(set(r[0] for r in rows))
    n_tr = int(len(anchors) * 0.6)
    tr_set = set(anchors[:n_tr])
    from collections import defaultdict
    bucket_y = defaultdict(list)
    for r in rows:
        if r[0] in tr_set:
            bucket_y[(r[1], r[2], r[3])].append(r[4])
    hr_train = {k: (sum(v) / len(v) if v else 0.5) for k, v in bucket_y.items()}

    p_tr, y_tr, p_val, y_val = [], [], [], []
    Hv, rv = [], []
    for r in rows:
        _hr = hr_train.get((r[1], r[2], r[3]), 0.5)
        if r[0] in tr_set:
            p_tr.append(_hr); y_tr.append(r[4])
        else:
            p_val.append(_hr); y_val.append(r[4])
            Hv.append(r[3]); rv.append(r[2])
    p_val = np.array(p_val); y_val = np.array(y_val)
    Hv = np.array(Hv); rv = np.array(rv)

    brier = lambda p, y: float(np.mean((np.clip(p, 0.001, 0.999) - y) ** 2))
    print("vol_scale=%.3f  训练样本 %d  验证样本 %d" % (vol_scale, len(p_tr), len(p_val)))
    print("=== 主导档 _hr 整体 OOS 验证 Brier（复验 R221）===")
    print("原始 _hr(未校准): 训练 %.4f / 验证 %.4f" % (brier(p_tr, y_tr), brier(p_val, y_val)))

    def _bucket_of_H(H):
        return _hb(int(H))
    def _bucket_of_r(r):
        return _rb(float(r))

    print("\n=== 按幅度 r 分桶（主导档 _hr，验证集）===")
    by_r = {}
    for r in [0.03, 0.05, 0.08, 0.12, 0.15, 0.20]:
        a = _agg(p_val, y_val, np.isclose(rv, r))
        if a:
            by_r[float(r)] = a
            print(" r=%.2f : %s" % (r, a))
    print("\n=== 按周期 H 分桶（主导档 _hr，验证集）===")
    by_H = {}
    for H in [20, 40, 60, 90, 120, 180, 250]:
        a = _agg(p_val, y_val, Hv == H)
        if a:
            by_H[int(H)] = a
            print(" H=%3d : %s" % (H, a))
    print("\n=== 合并桶（H×r 二维）===")
    hb, rb = {}, {}
    for tag in ["短(<=40)", "中(40-90]", "长(>90)"]:
        a = _agg(p_val, y_val, np.array([_bucket_of_H(H) == tag for H in Hv]))
        if a:
            hb[tag] = a; print(" %s : %s" % (tag, a))
    for tag in ["近(<=8%)", "远(>=12%)"]:
        a = _agg(p_val, y_val, np.array([_bucket_of_r(r) == tag for r in rv]))
        if a:
            rb[tag] = a; print(" %s : %s" % (tag, a))

    # 判定：近/小幅桶是否有显著低估（gap>0.1 视为系统性偏差）
    near = rb.get("近(<=8%)")
    far = rb.get("远(>=12%)")
    print("\n=== 结论判据（近桶 gap 是否显著>0.1）===")
    if near and near["gap"] > 0.1:
        print("⚠ 主导档 _hr 在『近/小幅』桶也系统性低估(gap=%.3f) → 线上8/9目标存在真实偏差，R225发现的提升空间可落地。" % near["gap"])
    else:
        print("主导档 _hr 在『近/小幅』桶无显著低估(gap=%s, 远桶 gap=%s) → R225偏差仅限漂移模型档(线上仅1低概率目标)，主导档已是最优，不部署。" %
              (near["gap"] if near else None, far["gap"] if far else None))


if __name__ == "__main__":
    main()
