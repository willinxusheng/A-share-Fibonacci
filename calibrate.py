# -*- coding: utf-8 -*-
"""概率模型 walk-forward 校准（提升预测准确性的实证地基）。

设计动机：
- build_data.py 的概率 binder 三级降级（回测实证→历史浪幅校准→漂移模型）中，
  「回测实证」一级依赖 predictions_log.jsonl 逐日归档并在未来窗口评估；但在冻结数据
  沙箱里，归档记录永远落在末日、观察窗恒超出数据范围 → totalEvaluated 恒为 0，
  「最准一级」从未生效，概率只能靠「漂移模型 + 历史浪幅校准」。
- 本模块提供一套【可在冻结数据上真实运行】的 walk-forward 校准：对历史上足够早的锚点
  日 T（其后的 horizon 日数据在冻结集中真实存在），仅用 T 之前信息估计 vol/drift regime，
  用与 build_data.py 逐字一致的首达概率公式预测 P(触达 ±r 目标 | 自 T 起 horizon 日)，
  再与「T 之后 horizon 日真实是否触达」比对，输出校准曲线 / Brier / 斜率，并给出温度缩放
  建议 T。这是概率模型真正的「实证体检」，可直接用于修正漂移先验，使概率更贴近历史实测。

全部只读 data/，不写 FIB_DATA、不改生产概率——build_data.py 调用方决定是否应用修正。
"""
import math

import numpy as np
import pandas as pd

_WINDOWS = [20, 60, 120, 250]


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def first_passage_prob(a, mu, sigma, T):
    """与 build_data.py _enrich 逐字一致的首达概率（反射原理）。

    a: 带符号对数目标幅度(目标-锚点)；mu/sigma: 每期 drift/vol；T: 期数(交易日)。
    上行 a>0 / 下行 a<0 对称处理；零漂移退化为 2Φ(-|a|/(σ√T))（蒙特卡洛已校验误差<1.6%）。
    """
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
    """table: {w: v}，对 x(horizon) 在窗口间线性插值，越界取端点（与 build _vol_for/_drift_for 同）。"""
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


def run_calibration(df, vol_conf=1.0, daily_vol=None,
                    max_horizon=250, min_anchor_gap=20):
    """对 df(上证日线) 跑 walk-forward 校准，返回校准摘要 dict。

    与 build_data.py 的「漂移模型原始先验」逐字对齐：model_p = 100*首达概率，
    其中 mu = _drift_for(H)*vol_conf（波动率 regime 阻尼），sigma = _vol_for(H)，
    T = max(10, round(H))（真实 horizon，不做 exp_cap 截断——与 R74 后 build_data._enrich
    / audit50._expected 一致；旧式 T=clamp(round(H),10,exp_cap) 会让长周期目标的校准
    公式与线上部署公式脱节，可能误触发温度修正，故移除）。
    不含共振/历史封顶/地板——这些是为校准目标外的附加项，校准只针对
    「首达概率公式 + vol/drift 估计」本身的偏差。

    锚点 i 需满足 i+max_horizon < n（前瞻窗口数据存在）；且 i>=min_anchor_gap（前有数据估 regime）。
    目标幅度 r 正负各一组，horizon 取与 build 同口径的窗口匹配（H 越大用更长窗口 regime）。
    """
    ret = np.log(df["close"] / df["close"].shift(1))
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    n = len(df)
    # R90：walk-forward 自洽的 vol regime 阻尼（与 build_data 同口径，但取锚点 i 自身 regime）
    hv20_full = ret.rolling(20).std() * np.sqrt(244) * 100
    def _vol_scale_at(idx):
        if idx < 0 or idx >= len(hv20_full) or pd.isna(hv20_full.iloc[idx]):
            return 1.0
        # R123 修复前视泄漏：原 (hv20_full.dropna() < hv20_full.iloc[idx]) 用全序列(含未来)算分位，
        # walk-forward 锚点 idx 偷看未来 vol 分布来定 band scale，违背 R85 忠实 OOS 与第95行
        # “截至每交易日”口径(同处 vol/drift 已用 rolling 到 i)。改为截至 idx 的历史窗口
        # iloc[:idx+1]：与生产末日(iloc[:end+1]=全序列)语义一致，校准不再含未来信息。
        _pct = float((hv20_full.iloc[:idx + 1].dropna() < hv20_full.iloc[idx]).mean()) * 100
        return 1.15 if _pct >= 66 else (1.0 if _pct >= 33 else 0.88)

    # 预计算各窗口滚动 vol/drift（截至每交易日，含当日；信息口径与 build 一致）
    vol_by_w = {w: ret.rolling(w).std().values for w in _WINDOWS}
    drift_by_w = {w: ret.rolling(w).mean().values for w in _WINDOWS}

    r_grid = [0.03, 0.05, 0.08, 0.12, 0.15, 0.20]
    h_grid = [20, 40, 60, 90, 120, 180, 250]

    samples = []  # (model_p_raw, realized)
    i_start = min_anchor_gap
    i_end = n - max_horizon - 1
    if i_end <= i_start:
        return {"n": 0, "usable": False, "bins": [], "brier": None,
                "slope": None, "temperature": 1.0, "reliability": "样本不足"}

    for i in range(i_start, i_end + 1):
        base = close[i]
        for H in h_grid:
            if i + H >= n:
                continue
            w = min(_WINDOWS[-1], max(_WINDOWS[0], H))
            sigma = _interp({ww: vol_by_w[ww][i] for ww in _WINDOWS}, w)
            mu = _interp({ww: drift_by_w[ww][i] for ww in _WINDOWS}, w)
            if sigma is None or math.isnan(sigma) or sigma <= 0:
                continue
            # 与 build 逐字一致(R74 后)：mu 经 regime 阻尼、T 用真实 horizon(不截断)
            _exp = max(10, int(round(H)))
            _mu_eff = _interp({ww: drift_by_w[ww][i] for ww in _WINDOWS}, w) * vol_conf
            _sig = _interp({ww: vol_by_w[ww][i] for ww in _WINDOWS}, w)
            fut_hi = high[i + 1: i + 1 + H]
            fut_lo = low[i + 1: i + 1 + H]
            for r in r_grid:
                # R90：对齐 build_data._drift_prior_prob 的 band 定义（R89 修复后）——首达障碍=目标×band边，
                # 实测命中=触达 band 边(非旧式 0.1% 精确价容差)。_frac 取锚点 i 自身 vol regime
                # (walk-forward 自洽：部署在日 i 用日 i 的 vol)，使本校准真正验证"线上实际用的"定义。
                # 守卫(复刻 build_data._drift_prior_prob L1349/1358)：若 band 边已跨过锚点 base，
                # 则"当前已在 band 内"，首达概率恒=1.0。缺此守卫时：上行目标 a_up<0 会被
                # first_passage_prob 误判成【下行】首达、下行目标 a_dn>0 误判成【上行】首达
                # (因函数仅收 a 不含 base 无法识别 band 跨锚点)，致 44% 样本走错分支、realized≈1.0
                # 而 p 却为中等值 → Brier/斜率/可靠性结论失真(误诊"低概率区系统性低估")。
                _frac = min(_sig * math.sqrt(_exp) * _vol_scale_at(i), 0.235)
                # 上行目标：障碍=base*(1+r)*(1-_frac)（band 下缘）
                _bar_up = base * (1.0 + r) * (1.0 - _frac)
                if base >= _bar_up:
                    p_up = 1.0
                else:
                    p_up = first_passage_prob(math.log(_bar_up / base), _mu_eff, _sig, _exp)
                hit_up = (fut_hi.max() >= _bar_up)
                samples.append((p_up, 1.0 if hit_up else 0.0))
                # 下行目标：障碍=base*(1-r)*(1+_frac)（band 上缘）
                _bar_dn = base * (1.0 - r) * (1.0 + _frac)
                if base <= _bar_dn:
                    p_dn = 1.0
                else:
                    p_dn = first_passage_prob(math.log(_bar_dn / base), _mu_eff, _sig, _exp)
                hit_dn = (fut_lo.min() <= _bar_dn)
                samples.append((p_dn, 1.0 if hit_dn else 0.0))

    if not samples:
        return {"n": 0, "usable": False, "bins": [], "brier": None,
                "slope": None, "temperature": 1.0, "reliability": "样本不足"}

    ps = np.array([s[0] for s in samples])   # first_passage_prob 已返回 0-1 概率
    ys = np.array([s[1] for s in samples])
    # 钳制到 (0.001,0.999) 避免 logit 溢出
    ps = np.clip(ps, 0.001, 0.999)
    n_s = len(samples)
    brier = float(np.mean((ps - ys) ** 2))

    # 分 10 桶校准曲线
    edges = np.linspace(0.0, 1.0, 11)
    bins = []
    for b in range(10):
        if b < 9:
            m = (ps >= edges[b]) & (ps < edges[b + 1])
        else:
            m = ps >= edges[9]
        cnt = int(m.sum())
        if cnt > 0:
            bins.append({
                "pLo": round(float(edges[b]), 2),
                "pHi": round(float(edges[b + 1]), 2),
                "modelMean": round(float(ps[m].mean()), 3),
                "realized": round(float(ys[m].mean()), 3),
                "n": cnt,
            })

    # 校准斜率：logit 空间线性拟合 realized~model_p，斜率≈1 即良好
    z = np.log(ps / (1.0 - ps))
    if np.std(z) > 1e-6:
        slope = float(np.polyfit(z, ys, 1)[0])
    else:
        slope = None

    # 温度缩放建议：logit 空间 p_cal = sigmoid(logit(p)/T)。
    # 网格搜索 T∈[0.5,2.0] 最小化校准后 Brier；T>1→更不确定(拉向50)，T<1→更自信。
    best_T, best_b = 1.0, brier
    for T in np.linspace(0.5, 2.0, 31):
        cal = 1.0 / (1.0 + np.exp(-z / T))
        bT = float(np.mean((cal - ys) ** 2))
        if bT < best_b:
            best_b, best_T = bT, float(T)
    # 仅当改进显著(>2%)才建议非 1.0，避免噪声过拟合
    if (best_b + 1e-9) < brier * 0.98:
        temperature = round(best_T, 3)
    else:
        temperature = 1.0

    # 可靠性结论：以 Brier 最优温度为主，但 R90 增补「斜率严重偏离1」的独立告警——
    # Brier 最优 T 常停在 1.0(因高概率样本占多数且已校准)，但斜率<<1 揭示低概率/远端目标
    # 系统性低估命中率(首达公式对易达的小幅目标过保守)。此形状失准温度缩放无法单独修正
    # (T>1 救低概率区却伤高概率区)，故仅如实标注，供概率解读参考，不误触发温度修正。
    if temperature != 1.0:
        if slope is not None and slope > 1.0:
            reliability = "偏保守(模型低估命中率, 建议升温 T=%.2f)" % temperature
        else:
            reliability = "偏乐观(模型高估命中率, 建议降温 T=%.2f)" % temperature
    elif slope is not None and slope < 0.5:
        reliability = "形状失准(低概率区系统性低估命中率; Brier最优T=1但斜率=%.2f<<1, 漂移先验对远/低概率目标偏保守)" % slope
    else:
        reliability = "良好(Brier 最优, 无需温度修正)"

    return {
        "n": n_s,
        "usable": True,
        "bins": bins,
        "brier": round(brier, 4),
        "slope": round(slope, 3) if slope is not None else None,
        "temperature": temperature,
        "reliability": reliability,
    }


if __name__ == "__main__":
    import json
    import os
    BASE = os.path.dirname(os.path.abspath(__file__))
    df = pd.read_csv(os.path.join(BASE, "data", "sh000001.csv"),
                     parse_dates=["date"]).set_index("date")
    # 复刻 build 的 regime 阻尼与 exp_cap，使校准对象 = build 真实使用的漂移先验
    ret = np.log(df["close"] / df["close"].shift(1))
    daily_vol = float(ret.rolling(20).std().iloc[-1])
    hv20 = ret.rolling(20).std() * np.sqrt(244) * 100
    hv_pctile = float((hv20.dropna() < hv20.iloc[-1]).mean()) * 100
    _vol_bucket = "高" if hv_pctile >= 66 else ("中" if hv_pctile >= 33 else "低")
    _vol_scale = 1.15 if _vol_bucket == "高" else (1.0 if _vol_bucket == "中" else 0.88)
    _drift_conf = 0.60 if _vol_bucket == "高" else (0.85 if _vol_bucket == "中" else 1.0)
    res = run_calibration(df, vol_conf=_drift_conf, daily_vol=daily_vol)
    print(json.dumps(res, ensure_ascii=False, indent=2))
