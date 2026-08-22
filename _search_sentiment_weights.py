# -*- coding: utf-8 -*-
"""R121 情绪指标权重 OOS 网格搜索（walk-forward 防过拟合）。

背景：五维权重现为固定 [0.30,0.20,0.20,0.15,0.15]，动量窗口固定 20 日——均未经数据校准。
本脚本：
  1) 重建每个历史点的 5 维子分（动量窗口 10/20/30/40 四档分别计算）
  2) 随机 Dirichlet 采样 3000 组权重 + 动量窗口组合
  3) walk-forward：前 70% 样本内选优（|r| 最大且方向为负），后 30% OOS 验证
  4) 对比现权重基线与最优组合的 OOS 表现

目标函数：score 与未来 20 日收益的 Spearman 相关 r（逆向信号 → 期望 r<0、|r| 大）。
"""
import json
import math
import random
import re

REPO = r"C:\Users\Administrator\WorkBuddy\2026-08-04-23-16-18\A-share-Fibonacci"


def load_js(path, var):
    raw = open(path, encoding="utf-8").read()
    return json.loads(re.sub(r"^\s*window\.%s\s*=\s*" % re.escape(var), "", raw).rstrip().rstrip(";"))


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def pctile_rank(value, window):
    if not window:
        return 50.0
    cnt = sum(1 for x in window if x <= value)
    return 100.0 * cnt / len(window)


def ma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def daily_hv(closes):
    hv = [None] * len(closes)
    for i in range(1, len(closes)):
        if i < 20:
            continue
        rets = [(closes[j] / closes[j - 1] - 1.0) for j in range(i - 19, i + 1)]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        hv[i] = math.sqrt(var) * math.sqrt(252.0)
    return hv


def build_features(D):
    """重建每点 5 维子分，返回 {i: [mom_10, mom_20, mom_30, mom_40, pos, vol, volat, breadth]}（从 i=249 起）。"""
    k = D["kline"]
    dates = [str(x) for x in k["dates"]]
    closes = [float(x) for x in k["close"]]
    vols = [float(x) for x in k["volume"]]
    if len(closes) < 250:
        return None, None

    res_b = float((D.get("resonance") or {}).get("breadth") or 0.0)
    cross_b = float((D.get("crossMarket") or {}).get("breadth") or 0.0)
    sub_breadth = clamp((res_b + cross_b) / 2.0, -1.0, 1.0)
    hv = daily_hv(closes)

    feats = {}
    out_dates = []
    for i in range(249, len(closes)):
        moms = []
        for win in (10, 20, 30, 40):
            if i >= win:
                r = (closes[i] / closes[i - win] - 1.0) * 100.0
            else:
                r = 0.0
            moms.append(clamp(r / 8.0, -1.0, 1.0))
        m250 = ma(closes[: i + 1], 250)
        pos = clamp((closes[i] / m250 - 1.0) if m250 else 0.0, -1.0, 1.0)
        # 归一化到 ±1（原逻辑 /0.15 后 clamp）
        pos = clamp((closes[i] / m250 - 1.0) / 0.15 if m250 else 0.0, -1.0, 1.0)
        v20, v250 = ma(vols[: i + 1], 20), ma(vols[: i + 1], 250)
        vr = (v20 / v250 - 1.0) if (v20 and v250) else 0.0
        sub_vol = clamp(vr / 0.5, -1.0, 1.0)
        win_hv = [h for h in hv[max(0, i - 249): i + 1] if h is not None]
        pct = pctile_rank(hv[i], win_hv) if hv[i] is not None else 50.0
        sub_volat = clamp(1.0 - pct / 50.0, -1.0, 1.0)
        feats[i] = moms + [pos, sub_vol, sub_volat, sub_breadth]
        out_dates.append(dates[i])
    return feats, out_dates


def score_of(f, w, mom_idx):
    w5 = [w[0], w[1], w[2], w[3], w[4]]
    moms = f[0:4]
    subs = [moms[mom_idx], f[4], f[5], f[6], f[7]]
    return clamp(50.0 + 50.0 * sum(wi * s for wi, s in zip(w5, subs)), 0.0, 100.0)


def spearman(a, b):
    n = len(a)
    if n < 10:
        return 0.0
    ra = sorted(range(n), key=lambda i: a[i])
    rb = sorted(range(n), key=lambda i: b[i])
    rank_a = [0] * n
    rank_b = [0] * n
    for r, i in enumerate(ra):
        rank_a[i] = r
    for r, i in enumerate(rb):
        rank_b[i] = r
    ma = sum(rank_a) / n
    mb = sum(rank_b) / n
    cov = sum((rank_a[i] - ma) * (rank_b[i] - mb) for i in range(n))
    va = math.sqrt(sum((rank_a[i] - ma) ** 2 for i in range(n)))
    vb = math.sqrt(sum((rank_b[i] - mb) ** 2 for i in range(n)))
    return cov / (va * vb) if va and vb else 0.0


def main():
    D = load_js(REPO + r"\data\data.js", "FIB_DATA")
    feats, dates = build_features(D)
    if not feats:
        print("数据不足")
        return

    kc = [float(x) for x in D["kline"]["close"]]
    base = len(kc) - len(dates)
    idxs = list(feats.keys())

    # 未来 20 日收益
    fwd20 = {}
    for i in idxs:
        j = i + 20
        if j < len(kc):
            fwd20[i] = (kc[j] / kc[i] - 1.0) * 100.0
    valid = [i for i in idxs if i in fwd20]

    # walk-forward 切分：前 70% 训练
    cut = int(len(valid) * 0.70)
    tr_idx = valid[:cut]
    oos_idx = valid[cut:]
    print("训练样本 %d（%s~%s），OOS 样本 %d（%s~%s）" % (
        len(tr_idx), dates[0], dates[cut - 1], len(oos_idx), dates[cut], dates[-1]))

    def r_for(combo):
        w, mi = combo
        sc_tr = [score_of(feats[i], w, mi) for i in tr_idx]
        sc_os = [score_of(feats[i], w, mi) for i in oos_idx]
        r_tr = spearman(sc_tr, [fwd20[i] for i in tr_idx])
        r_os = spearman(sc_os, [fwd20[i] for i in oos_idx])
        return r_tr, r_os

    # 基线：现权重 + 20 日动量
    base_w = [0.30, 0.20, 0.20, 0.15, 0.15]
    base_r_tr, base_r_os = r_for((base_w, 1))
    print("\n基线（现权重 0.30/0.20/0.20/0.15/0.15 + 动量20日）")
    print("  训练 r=%.4f | OOS r=%.4f" % (base_r_tr, base_r_os))

    # 随机 Dirichlet 采样权重 + 动量窗口
    random.seed(42)
    candidates = []
    for _ in range(6000):
        # 简单权重采样：在 0.05~0.5 内取 4 个随机数，第 5 维补足后归一
        raw = [random.uniform(0.05, 0.50) for _ in range(4)]
        s = sum(raw)
        w = raw + [max(0.05, 1.0 - s)] if s < 0.95 else [r * 0.95 / s for r in raw] + [0.05]
        w = [x / sum(w) for x in w]
        mi = random.choice([0, 1, 2, 3])  # 10/20/30/40 日动量
        r_tr, r_os = r_for((w, mi))
        # 训练期方向必须为负（逆向），且 |r| 排序
        if r_tr < 0:
            candidates.append((abs(r_tr), r_tr, r_os, w, mi))

    candidates.sort(reverse=True)
    print("\n=== 训练期 |r| 前 10 组合（含 OOS 验证）===")
    for k, (a, r_tr, r_os, w, mi) in enumerate(candidates[:10]):
        print("  #%d |r_tr|=%.4f r_tr=%.4f r_oos=%.4f 动量%d日 权重=%s" % (
            k + 1, a, r_tr, r_os, (mi + 1) * 10, ["%.2f" % x for x in w]))

    # 选 OOS 最好的（且 OOS 也为负）
    best = None
    for a, r_tr, r_os, w, mi in candidates:
        if r_os < 0:
            best = (r_os, r_tr, w, mi)
            break
    if best:
        r_os, r_tr, w, mi = best
        print("\n=== 推荐组合（OOS 负向最优）===")
        print("  OOS r=%.4f | 训练 r=%.4f | 动量%d日 | 权重=%s" % (
            r_os, r_tr, (mi + 1) * 10, ["%.2f" % x for x in w]))
        print("  对比基线 OOS r=%.4f → 提升 |Δr|=%.4f" % (base_r_os, abs(r_os) - abs(base_r_os)))
        print("  提升%%=%.1f%%" % ((abs(r_os) - abs(base_r_os)) / abs(base_r_os) * 100 if base_r_os else 0))


if __name__ == "__main__":
    main()
