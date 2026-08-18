# -*- coding: utf-8 -*-
"""R_时间模型_OOS：诚实实测 expDays（卖点触达时间）预测精度（落实项 ②b 的地基）。

背景：记忆里记着"expDays 系统性偏短 MAPE 33%"，但生产回测日志 predictions_log.jsonl
共 90 条、days_to_hit 全 0（coldStart=true）——即该偏差从未被真实命中数据验证过，
是理论推测。本脚本用 walk-forward（无前视）把生产时间模型(_horizon_for 的腿节奏派生)
在真实历史上逐锚点复刻，并比对真实触达天数，给出可证伪的 MAPE / 偏置。

方法（严守 R85 忠实 OOS，杜绝未来信息泄漏）：
- 复刻 build_data._hist_legs（structures.json 的 8% zigzag 完成腿，腿≥10 交易日）。
- 按腿终点日期排序；第 k 条腿终点作锚点 d，预测只用"在 d 之前已完成"的腿（past_legs）。
- 对每个目标幅度 r（卖点典型幅度网格），复刻 _horizon_for：
    同向、|腿对数收益|≥r 的完成腿，TTR=腿时长×(r/|lr|)，取中位数；
    样本<4 则按同向最近腿幅度-时长比例外推(exp=r/rate)。
- 真实触达天数：自锚点 d 在 kline 上找首次 high≥close_d*(1+r)（上）的首个交易日差。
- 聚合 MAPE=mean(|pred-actual|/actual)、偏置=mean(pred-actual)、按 r 分桶。

判据：若 MAPE 确实高且有系统性偏短（偏置<0 显著），再设计 OOS 验证过的修正；
否则如实报告"生产时间模型在历史 OOS 上精度可接受，33% 偏短指控未被证实"。
"""
import os
import json
import math
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))


def load_legs():
    """复刻 build_data._hist_legs（lines 178-191）。"""
    st = json.load(open(os.path.join(BASE, "data", "structures.json"), encoding="utf-8"))
    legs = []
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
        legs.append((_lr, _ld, str(_p0["date"]), str(_p1["date"])))
    return legs


def horizon_for(amp, past_legs):
    """复刻 build_data._horizon_for 核心（lines 789-820），输入目标对数幅度 amp。"""
    _dir = 1 if amp >= 0 else -1
    _at = abs(amp)
    _ttr = sorted(ld * (_at / abs(lr)) for lr, ld, *_ in past_legs
                  if _dir * lr > 0 and abs(lr) >= _at and abs(lr) > 0)
    if len(_ttr) >= 4:
        return _ttr[len(_ttr) // 2]
    # 外推：取幅度最接近目标的同向腿，按时长-幅度比例外推
    _near = sorted([(abs(lr), ld) for lr, ld, *_ in past_legs if _dir * lr > 0],
                   key=lambda t: abs(t[0] - _at))
    if _near:
        _lr0, _ld0 = _near[0]
        return _ld0 * (_at / abs(_lr0))
    return None


def main():
    legs = load_legs()
    print("历史完成腿数: %d" % len(legs))
    df = pd.read_csv(os.path.join(BASE, "data", "sh000001.csv"), parse_dates=["date"]).set_index("date")
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    n = len(dates)
    dpos = {d: i for i, d in enumerate(dates)}

    # 腿按终点日期排序（walk-forward：past_legs = 在锚点之前已完成的腿）
    legs_sorted = sorted(legs, key=lambda t: t[3])
    r_grid = [0.05, 0.08, 0.12, 0.15, 0.20]
    max_horizon = 330

    # 公平锚点：在全交易日网格上均匀取（贴近生产"对今日价格估 expDays"的真实场景），
    # 而非 zigzag 拐点（拐点是趋势转折点，会人为放大"低估触达时间"的不公平偏差）。
    recs = []  # (r, pred, actual)
    step = 15
    for i0 in range(250, n - max_horizon, step):
        d_anchor = dates[i0]
        # 仅用锚点之前已完成的腿（无前视）
        past = [t for t in legs_sorted if t[3] <= d_anchor]
        if len(past) < 4:
            continue
        c0 = close[i0]
        for r in r_grid:
            pred = horizon_for(math.log(1.0 + r), past)
            if pred is None or pred <= 0:
                continue
            tgt = c0 * (1.0 + r)
            actual = None
            for j in range(i0 + 1, min(i0 + 1 + max_horizon, n)):
                if high[j] >= tgt:
                    actual = j - i0
                    break
            if actual is None:
                continue  # 窗口内未触达，不计入（保守：仅评已实现的）
            recs.append((r, pred, actual))

    recs = np.array(recs)
    print("有效 (锚点×目标) 样本: %d" % len(recs))
    if len(recs) == 0:
        print("无足够样本，退出")
        return
    r = recs[:, 0]; pred = recs[:, 1]; actual = recs[:, 2]
    mape = np.mean(np.abs(pred - actual) / actual)
    bias = np.mean(pred - actual)
    bias_pct = bias / np.mean(actual) * 100
    print("\n=== 时间模型 OOS 实测（越低越准）===")
    print("整体 MAPE        : %.1f%%" % (mape * 100))
    print("整体偏置(pred-act): %.1f 交易日 (%.1f%% of mean actual=%.1f)" %
          (bias, bias_pct, np.mean(actual)))
    print("pred 中位/mean   : %.1f / %.1f" % (np.median(pred), pred.mean()))
    print("actual 中位/mean : %.1f / %.1f" % (np.median(actual), actual.mean()))
    print("\n--- 按目标幅度 r 分桶 ---")
    for rr in r_grid:
        m = r == rr
        if m.sum() == 0:
            continue
        mp = np.mean(np.abs(pred[m] - actual[m]) / actual[m])
        bb = np.mean(pred[m] - actual[m])
        print("  r=%.2f  n=%4d  MAPE=%.1f%%  偏置=%+.1f 交易日" % (rr, int(m.sum()), mp * 100, bb))

    # ---- 时间校准 OOS 验证（R85：证明修复真降 MAPE 才考虑进引擎）----
    # 按锚点时间顺序 60/40 切分；训练集拟合「预测TTR→真实天数」保序(PAVA)映射，
    # 验证集测校准前后 MAPE。若显著下降，则时间校准是经 OOS 验证的可落实修复。
    n_tr = int(len(recs) * 0.6)
    tr = recs[:n_tr]; val = recs[n_tr:]
    p_tr, a_tr = tr[:, 1], tr[:, 2]
    p_v, a_v = val[:, 1], val[:, 2]
    mape = lambda p, a: float(np.mean(np.abs(p - a) / a))
    base_mape = mape(p_v, a_v)
    # PAVA 保序校准（预测大→真实大）
    idx = np.argsort(p_tr)
    ps, as_ = p_tr[idx], a_tr[idx]
    blocks = []
    for i in range(len(ps)):
        blocks.append([float(as_[i]), 1])
        while len(blocks) >= 2 and blocks[-1][0] < blocks[-2][0] - 1e-12:
            b2 = blocks.pop(); b1 = blocks.pop()
            m = (b1[0] * b1[1] + b2[0] * b2[1]) / (b1[1] + b2[1])
            blocks.append([m, int(b1[1] + b2[1])])
    bounds = []  # (p_lo, p_hi, cal_val)
    cum = 0
    for blk in blocks:
        p_lo = ps[cum]; p_hi = ps[cum + blk[1] - 1]; cum += blk[1]
        bounds.append((p_lo, p_hi, blk[0]))

    def cal(p):
        p = float(p)
        if p <= bounds[0][0]:
            return bounds[0][2]
        if p >= bounds[-1][1]:
            return bounds[-1][2]
        for lo, hi, v in bounds:
            if lo - 1e-9 <= p <= hi + 1e-9:
                return v
        return p
    cal_v = np.array([cal(x) for x in p_v])
    cal_mape = mape(cal_v, a_v)
    print("\n=== 时间校准 OOS 验证（60/40 时间切分）===")
    print("校准前验证 MAPE : %.1f%%" % (base_mape * 100))
    print("PAVA校准后 MAPE : %.1f%%  (Δ=%.1f%%, %s)" %
          (cal_mape * 100, (cal_mape / base_mape - 1) * 100,
           "显著改善" if cal_mape < base_mape * 0.9 else "无显著改善"))
    print("结论：时间校准%s经 OOS 验证的可落实修复（R85），可考虑进引擎。"
          % ("是" if cal_mape < base_mape * 0.9 else "非"))


if __name__ == "__main__":
    main()
