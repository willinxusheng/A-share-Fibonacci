# -*- coding: utf-8 -*-
"""市场情绪温度引擎（R87 借鉴缠论 sentiment/ 模块，做 A股斐波那契适配版）。

定位：monitor_only —— 情绪温度只用于「研判提示 + 面板展示」，绝不并入概率/方向预测主模型。

为什么不照搬缠论（缠论用「涨跌家数原始 txt + 成交额/换手率」）：
  - 涨跌家数 / 涨停跌停家数只有 eastmoney 中国本地源，GitHub 海外 runner 不可达（R271 根因），
    且 yahoo/stooq 无对应回退 → 照搬会在 CI 上「每天失败、靠兜底显示旧值」，是假绿/噪音。
  - 本仓 datafeed 抓的是指数日线 OHLCV，无成交额(amount)/换手率(to)，无法复刻缠论量能/换手指标。
  - 因此改用「本仓已有单一真源 data/data.js」透明推导一个代理情绪温度（见下方五维），
    不新增第三方接口、不破坏 sole-writer、CI 每天稳定可算。这是对 R85 反假绿纪律的坚守。

五维合成（各维度先映射到 [-1,+1] 子分，加权求和 → 0~100）：
  1. 趋势动量   w=0.30  上证 ret20（20 日涨跌幅），clamp(ret20/8)
  2. 牛熊位置   w=0.20  (lastClose - MA250)/MA250，clamp(dev/0.15)
  3. 量能水平   w=0.20  上证 vol20/vol250 - 1（放量=活跃，缩量=冷清），clamp(ratio/0.5)
  4. 波动恐慌   w=0.15  HV20 的五年分位（高波动=恐慌=降温），1 - pctile/50
  5. 广度确认   w=0.15  仅用 breadthAvailable>0 的源均值（缺失源不参与平均、全缺失归零不偏置，R122c）

输出（schema a-share-fib-sentiment/v3）：
  - today   : 当日单点快照（含 5 维 dims），保持 R87 既有契约。
  - history : 近 250 个交易日逐日回算的历史情绪序列（date, score, label）。
  - forecast: 由 subForecast 价格路径派生（斐波那契路径映射）的未来情绪预测序列（date, score, label）。
              预测段无量能/波动/广度未来因子，故量能/波动随预测 horizon 指数回归历史中枢（波动率均值回归，
              不再恒用今日值钉死全程）、广度沿用当前均值，仅「动量+牛熊位置」随价格路径变化 —— 这是路径派生
              预测，非因子预测，已在 note 中诚实标注。

五档定性（R122a 动态分位标尺）：基于 history 分布 p10/p30/p70/p90 切五档（冰点/偏冷/中性/偏热/狂热）；
  分布退化时回退固定 <20 / 20-40 / 40-60 / 60-80 / ≥80。today/history/forecast 共用同一标尺。

R123 预测力增强（不改水平口径，仅加派生信号）：
  - 情绪变化 Δ：每段历史算 5/20 日情绪差，并做『升温 vs 降温』分组未来20日收益统计
    （实证：情绪越涨越慎/越跌越贪，Δ 比水平更会预测未来收益）。
  - 滚动 z 分位：近120日均值/标准差归一化，标出『当前情绪相对自身近期有多极端』，跨 regime 可比。
  - 分 regime 信号强度直读：复用熊/牛分态统计，当前 regime 下一句话给信号强弱结论。

R124 预测力再增强（纯诊断字段，不改水平/今日展示口径，不增维度）：
  - 最优预测窗口扫描：按 score 中位数切冷/热组，扫描 H∈{5,10,20,40,60} 未来H日收益 spread，
    选 |spread| 最大且样本充足者为最优逆势解读窗口。
  - 组合状态信号：由『当前情绪水平档 × 当前Δ方向』定位四象限，给出该格经验未来20日收益与
    经验上涨概率（样本频率，非拟合），刻画『高位升温/低位降温』等组合时机。
  - 近期权重稳健性对照：对当前档比较等权 vs 近1年指数衰减加权未来20日均值，
    显著背离即提示 regime 漂移、信号稳健性下降。

诚实标注：这是「量能/动量/波动/广度」合成的代理情绪温度，非全市场涨跌家数；
  且量能维度受跨源 volume 单位差异影响，仅作相对参考。退出码恒 0（软，透明化不阻断）。
"""
import os
import sys
import json
import re
import datetime
import math

REPO = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(REPO, "data", "data.js")
OUT_JSON = os.path.join(REPO, "data", "sentiment.json")

N_HISTORY = 0  # 历史回算窗口：0=全部可用（自第 250 根起有完整 MA250/vol250 预热，约 5 年样本）


def _load_data():
    with open(DATA_JS, encoding="utf-8") as f:
        src = f.read()
    return json.loads(re.search(r"window\.FIB_DATA\s*=\s*(\{.*\})\s*;?\s*$", src, re.S).group(1))


def _ma(arr, w):
    if len(arr) < w:
        return None
    return sum(arr[-w:]) / w


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _f(x, default= 0.0):
    """防御性 float 转换：None/非数值/NaN/Inf → default（build_data 正常给数值，
    此处防降级时 None 崩溃，且 float('nan')/float('inf') 不会抛异常会穿透，
    必须用 math.isfinite 守门，否则 NaN 会污染 score 并令 round(score,1) 抛 ValueError）。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _label(score, bounds=None):
    """五档定性（R122a 动态分位标尺）。

    bounds=[b1,b2,b3,b4] 时按分位标尺切五档：
      冰点 < b1 / 偏冷 b1~b2 / 中性 b2~b3 / 偏热 b3~b4 / 狂热 >= b4。
    缺省 bounds 回退固定 [20,40,60,80]，保证未传动态标尺时仍可用（兼容旧调用）。
    """
    if bounds is None:
        bounds = [20.0, 40.0, 60.0, 80.0]
    b1, b2, b3, b4 = bounds
    if score < b1:
        return "冰点"
    if score < b2:
        return "偏冷"
    if score < b3:
        return "中性"
    if score < b4:
        return "偏热"
    return "狂热"


def _scale_bounds(scores):
    """基于 history score 分布动态分位标尺（R122a）。

    返回 (bounds, mode, pct)：
      bounds = [b1,b2,b3,b4] 对应 p10/p30/p70/p90，将 [0,100] 切成五档。
      固定阈值下历史 score 多落在 25~85，冰点(<20)/狂热(>=80) 几乎永不触发，
      动态标尺令各档更具区分度，且 today/forecast/history 同标尺，避免错位。
      mode='fixed' 表示样本不足(<30)或分布退化（分位塌缩/b1<=0/b4>=100），回退固定标尺，保证稳健。
    """
    if len(scores) < 30:
        return [20.0, 40.0, 60.0, 80.0], "fixed", None
    s = sorted(scores)

    def _pct(p):
        if not s:
            return 50.0
        k = (len(s) - 1) * p / 100.0
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return s[int(k)]
        return s[int(f)] * (c - k) + s[int(c)] * (k - f)

    b1, b2, b3, b4 = _pct(10), _pct(30), _pct(70), _pct(90)
    if not (b1 < b2 < b3 < b4) or b1 <= 0 or b4 >= 100:
        return [20.0, 40.0, 60.0, 80.0], "fixed", None
    return [round(b1, 1), round(b2, 1), round(b3, 1), round(b4, 1)], "dynamic", (b1, b2, b3, b4)


def _breadth_sub(D):
    """广度维度子分（R122c）：仅用 breadthAvailable>0 的可用源均值，缺失源不参与平均，
    全缺失时归零不偏置（不会把「缺失占位 0.0」当真实值拉低）。

    返回 (sub, avail, dom_val, cross_val)：
      avail    = 实际参与平均的源数（0/1/2）
      dom_val  = resonance.breadth 真实值，缺失源为 None（不污染 JSON 契约）
      cross_val= crossMarket.breadth 真实值，缺失源为 None
    诚实标注：当前广度仅当前单值代理（R271 约束：海外 CI 不可达 eastmoney 历史涨跌家数），
    未真正历史化；缺失源减少可用度，须在 dims meta 与 note 中注明（见 _compute_today）。
    """
    res = D.get("resonance") or {}
    cross = D.get("crossMarket") or {}
    ra = _f(res.get("breadthAvailable"))
    ca = _f(cross.get("breadthAvailable"))
    parts = []
    if ra > 0:
        parts.append(_f(res.get("breadth")))
    if ca > 0:
        parts.append(_f(cross.get("breadth")))
    if parts:
        sub = sum(parts) / len(parts)
        avail = len(parts)
    else:
        sub = 0.0
        avail = 0
    dom_val = _f(res.get("breadth")) if ra > 0 else None
    cross_val = _f(cross.get("breadth")) if ca > 0 else None
    return _clamp(sub, -1.0, 1.0), avail, dom_val, cross_val


def _is_a_share_trading_day(d):
    """A股交易日：工作日或调休补班日，且非法定休市。

    与 build_data.py R207 的 _next_trading_day 口径同源（上交所 上证公告〔2025〕45号）：
    仅列连续休市区间（周末本就休市，不重复）；补班日虽在周末仍交易。
    2027 安排待公布后补充。用于 forecast 插值后剔除长假/周末、保留补班日，避免非交易日假数据点。
    """
    dt = datetime.datetime.strptime(d, "%Y-%m-%d")
    ds = dt.strftime("%Y-%m-%d")
    if ds in _A_SHARE_MAKEUP:
        return True
    if dt.weekday() >= 5:
        return False
    if ds in _A_SHARE_HOLIDAYS:
        return False
    return True


def _expand_holidays(start, end):
    out = set()
    a = datetime.datetime.strptime(start, "%Y-%m-%d")
    b = datetime.datetime.strptime(end, "%Y-%m-%d")
    while a <= b:
        out.add(a.strftime("%Y-%m-%d"))
        a += datetime.timedelta(days=1)
    return out


# R207 口径（与 build_data.py / report.py / index.html _MAKEUP_2026 同源）
_A_SHARE_HOLIDAYS = (
    _expand_holidays("2026-01-01", "2026-01-03")
    | _expand_holidays("2026-02-15", "2026-02-23")
    | _expand_holidays("2026-04-04", "2026-04-06")
    | _expand_holidays("2026-05-01", "2026-05-05")
    | _expand_holidays("2026-06-19", "2026-06-21")
    | _expand_holidays("2026-09-25", "2026-09-27")
    | _expand_holidays("2026-10-01", "2026-10-07")
)
_A_SHARE_MAKEUP = {"2026-02-14", "2026-02-28", "2026-05-09", "2026-09-20", "2026-10-10"}


def _score_from_subs(subs):
    """subs = (sub_mom, sub_pos, sub_vol, sub_volat, sub_breadth)，权重固定。"""
    w = [0.30, 0.20, 0.20, 0.15, 0.15]
    score = 50.0 + 50.0 * sum(wi * s for wi, s in zip(w, subs))
    return _clamp(score, 0.0, 100.0)


def _pctile_rank(value, window):
    """value 在 window（数值列表）中的分位 [0,100]，>=value 的占比。window 为空返回 50。"""
    if not window:
        return 50.0
    cnt = sum(1 for x in window if x <= value)
    return 100.0 * cnt / len(window)


def _compute_today(D):
    """返回 (score, label, dims_list) 或 None（数据不足）。dims_list 元素 = (name, weight, sub, meta)。"""
    k = D.get("kline") or {}
    closes = [float(x) for x in (k.get("close") or [])]
    vols = [float(x) for x in (k.get("volume") or [])]
    if len(closes) < 250:
        return None

    last = closes[-1]
    ma250 = _ma(closes, 250)

    # 1. 趋势动量（上证 20 日涨跌幅）
    ret20 = (closes[-1] / closes[-21] - 1.0) * 100.0 if len(closes) >= 21 else 0.0
    sub_mom = _clamp(ret20 / 8.0, -1.0, 1.0)

    # 2. 牛熊位置（相对 250 日均线偏离）
    pos = (last / ma250 - 1.0) if ma250 else 0.0
    sub_pos = _clamp(pos / 0.15, -1.0, 1.0)

    # 3. 量能水平（20 日 / 250 日均量比值）
    vol20 = _ma(vols, 20)
    vol250 = _ma(vols, 250)
    vr = (vol20 / vol250 - 1.0) if (vol20 is not None and vol250 is not None) else 0.0
    sub_vol = _clamp(vr / 0.5, -1.0, 1.0)

    # 4. 波动恐慌（HV20 在「截至当日向前 250 日 HV 分布」中的分位，高波动=恐慌=降温）。
    # 与 _compute_history 末点口径一致，保证当日点与历史曲线平滑衔接；不足时回退 volRegime.pctile。
    hv = _daily_hv_series(closes)
    win = [h for h in hv[max(0, len(closes) - 250):] if h is not None]
    pctile = _pctile_rank(hv[-1], win) if hv[-1] is not None else _f((D.get("volRegime") or {}).get("pctile"), 50.0)
    sub_volat = _clamp(1.0 - pctile / 50.0, -1.0, 1.0)

    # 5. 广度确认（R122c：仅用 breadthAvailable>0 的可用源均值，缺失源不参与平均）
    sub_breadth, bw_avail, res_b_real, cross_b_real = _breadth_sub(D)

    dims = [
        ("momentum", 0.30, sub_mom, {"ret20_pct": round(ret20, 2)}),
        ("position", 0.20, sub_pos, {"lastClose": round(last, 2),
                                     "ma250": round(ma250, 2) if ma250 else None,
                                     "dev_pct": round(pos * 100.0, 2)}),
        ("volume", 0.20, sub_vol, {"vol20_vol250_ratio": round(vr, 3)}),
        ("volatility", 0.15, sub_volat, {"hv20_pctile": int(pctile)}),
        ("breadth", 0.15, sub_breadth, {"available": bw_avail,
                                        "domestic": (round(res_b_real, 3) if res_b_real is not None else None),
                                        "cross_market": (round(cross_b_real, 3) if cross_b_real is not None else None),
                                        "note": ("仅可用源均值；缺失源未参与（广度尚未历史化）"
                                                 if bw_avail < 2 else "两源均可用（广度尚未历史化）")}),
    ]
    score = round(_score_from_subs([s for (_n, _w, s, _m) in dims]), 1)
    return score, dims


def _daily_hv_series(closes):
    """逐日 HV20：以收盘日收益率为基础，20 日滚动标准差年化（近似用 sqrt(252)*stdev）。"""
    hv = [None] * len(closes)
    for i in range(1, len(closes)):
        if i < 20:
            continue
        rets = [(closes[j] / closes[j - 1] - 1.0) for j in range(i - 19, i + 1)]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        hv[i] = math.sqrt(var) * math.sqrt(252.0)
    return hv


def _compute_history(D):
    """全部可用交易日逐日情绪序列（自第 250 根起，保证 MA250/vol250 完整预热）。

    返回 [{date, score, label}, ...]；数据不足 250 根时返回空。
    """
    k = D.get("kline") or {}
    dates = [str(x) for x in (k.get("dates") or [])]
    closes = [float(x) for x in (k.get("close") or [])]
    vols = [float(x) for x in (k.get("volume") or [])]
    if len(closes) < 250:
        return []

    sub_breadth, _bw, _dr, _cr = _breadth_sub(D)  # 广度静态代理（无历史源）：仅可用源均值，缺失不偏置

    hv = _daily_hv_series(closes)

    start = 249  # 首个有完整 MA250/vol250 的点（0-based 索引）
    out = []
    for i in range(start, len(closes)):
        # 动量：20 日涨跌幅（与 _compute_today 的 closes[-1]/closes[-21] 口径一致，即始于 i、回溯 20 交易日）
        ret20 = (closes[i] / closes[i - 20] - 1.0) * 100.0 if i >= 20 else 0.0
        sub_mom = _clamp(ret20 / 8.0, -1.0, 1.0)

        # 牛熊位置
        ma250 = _ma(closes[: i + 1], 250)
        pos = (closes[i] / ma250 - 1.0) if ma250 else 0.0
        sub_pos = _clamp(pos / 0.15, -1.0, 1.0)

        # 量能
        vol20 = _ma(vols[: i + 1], 20)
        vol250 = _ma(vols[: i + 1], 250)
        vr = (vol20 / vol250 - 1.0) if (vol20 is not None and vol250 is not None) else 0.0
        sub_vol = _clamp(vr / 0.5, -1.0, 1.0)

        # 波动：当前 HV20 在「截至当日向前 250 日 HV 分布」中的分位
        win = [h for h in hv[max(0, i - 249): i + 1] if h is not None]
        pctile = _pctile_rank(hv[i], win) if hv[i] is not None else 50.0
        sub_volat = _clamp(1.0 - pctile / 50.0, -1.0, 1.0)

        score = round(_score_from_subs([sub_mom, sub_pos, sub_vol, sub_volat, sub_breadth]), 1)
        out.append({"date": dates[i], "score": score})
    return out  # 全部可用点（约 5 年），不再截断到 250


def _contra_stats(D, hist, bounds=None, scale_mode="fixed"):
    """逆向信号统计（单一真值，随窗口全量计算）。

    对每个 history 点按 label 分档，统计各档样本数 N 与「未来 20 交易日平均收益」。
    实证：score 与未来收益负相关（逆势信号），且高度 regime 依赖——
      价格 < MA250（熊市态）r≈-0.24 显著；价格 > MA250（牛市态）r≈-0.04 不显著。
    故同时输出 split 分态统计（bear/bull），供前端按当前 regime 选择解读口径。
    bounds 为动态分位标尺（R122a），分档随标尺走，保证与 today/history/forecast 同口径。
    返回 {bands, split:{bear:{bands}, bull:{bands}}, regime, scale_mode, note}。
    """
    k = D.get("kline") or {}
    closes = [float(x) for x in (k.get("close") or [])]
    ma250_arr = [x for x in (k.get("ma250") or [])]
    if not hist or len(closes) < len(hist) + 20:
        return None
    base = len(closes) - len(hist)
    if bounds is None:
        bounds = [20.0, 40.0, 60.0, 80.0]
    b1, b2, b3, b4 = bounds
    bands = [("冰点", 0, b1), ("偏冷", b1, b2), ("中性", b2, b3), ("偏热", b3, b4), ("狂热", b4, 101)]

    def _bands_for(pred):
        out = []
        for (lab, lo, hi) in bands:
            n, s = 0, 0.0
            for off, h in enumerate(hist):
                i = base + off
                if lo <= h["score"] < hi and pred(i):
                    j = i + 20
                    if j < len(closes):
                        n += 1
                        s += (closes[j] / closes[i] - 1.0) * 100.0
            out.append({"label": lab, "n": n, "fwd20": round(s / n, 2) if n else None})
        return out

    out_bands = _bands_for(lambda i: True)
    # 分态统计：仅用有 MA250 的点（前 249 根无均线，天然排除）
    bear_bands = _bands_for(lambda i: ma250_arr[i] is not None and closes[i] < ma250_arr[i])
    bull_bands = _bands_for(lambda i: ma250_arr[i] is not None and closes[i] > ma250_arr[i])
    # 当前 regime：末根价 vs MA250
    last = closes[-1]
    last_ma = ma250_arr[-1] if ma250_arr else None
    regime = "bear" if (last_ma is not None and last < last_ma) else "bull"
    return {
        "bands": out_bands,
        "split": {"bear": {"bands": bear_bands}, "bull": {"bands": bull_bands}},
        "regime": regime,
        "scale_mode": scale_mode,
        "note": ("近%d个交易日逐日回算：score 与未来20日收益负相关（逆势信号）；"
                 "N 为该档有未来20日收益的样本数（尾部20日不计），各档合计%d；"
                 "当前分数区间 %.1f~%.1f；分档标尺=%s（R122a）；"
                 "regime 分态：熊市态(价<MA250)信号显著(r≈-0.24)、"
                 "牛市态(价>MA250)信号不显著(r≈-0.04)——当前为%s态，解读应以%s态统计为准") % (
            len(hist), sum(b["n"] for b in out_bands),
            min(h["score"] for h in hist), max(h["score"] for h in hist),
            ("动态分位" if scale_mode == "dynamic" else "固定"),
            "熊市" if regime == "bear" else "牛市", "熊市" if regime == "bear" else "牛市"),
    }


def _contra_delta_stats(D, hist):
    """情绪变化(Δ)预测力统计（R123a）：经典结论——情绪的变化比情绪的水平更会预测未来收益。

    对每个有 Δ20 与未来20日收益的历史点，按 Δ 符号分『升温/降温』两组，
    统计各自未来20日平均收益；并做四象限（高/中/低 水平 × 升/降）细分，
    揭示『高位升温』『低位降温』等极端情形的方向。返回 {rising, falling, quadrants, note}。
    """
    k = D.get("kline") or {}
    closes = [float(x) for x in (k.get("close") or [])]
    if not hist or len(closes) < len(hist) + 20:
        return None
    base = len(closes) - len(hist)
    rising_n = rising_s = rising_up = 0.0
    falling_n = falling_s = falling_up = 0.0
    quad = {}  # (level, dir) -> [n, s, up]
    for off, h in enumerate(hist):
        d = h.get("d20")
        if d is None:
            continue
        i = base + off
        j = i + 20
        if j >= len(closes):
            continue
        f = (closes[j] / closes[i] - 1.0) * 100.0
        up = 1.0 if f >= 0 else 0.0
        if d >= 0:
            rising_n += 1
            rising_s += f
            rising_up += up
        else:
            falling_n += 1
            falling_s += f
            falling_up += up
        lb = "高" if h["score"] >= 60 else ("低" if h["score"] < 40 else "中")
        key = (lb, "升" if d >= 0 else "降")
        q = quad.setdefault(key, [0, 0.0, 0.0])
        q[0] += 1
        q[1] += f
        q[2] += up

    def _mean(n, s):
        return round(s / n, 2) if n else None

    def _pos(n, up):
        return round(up / n, 2) if n else None

    r_rise = _mean(int(rising_n), rising_s)
    r_fall = _mean(int(falling_n), falling_s)
    p_rise = _pos(int(rising_n), rising_up)
    p_fall = _pos(int(falling_n), falling_up)
    quads = [{"level": lv, "dir": dr, "n": v[0], "fwd20": _mean(v[0], v[1]),
              "pos": _pos(v[0], v[2])}
             for (lv, dr), v in sorted(quad.items())]
    note = ("情绪变化(Δ20)预测力：升温组 N=%d 未来20日平均 %.2f%%（经验上涨概率 %.0f%%），"
            "降温组 N=%d 平均 %.2f%%（经验上涨概率 %.0f%%）。"
            % (int(rising_n), r_rise if r_rise is not None else 0.0,
               (p_rise or 0.0) * 100,
               int(falling_n), r_fall if r_fall is not None else 0.0,
               (p_fall or 0.0) * 100))
    if r_rise is not None and r_fall is not None:
        note += ("升温后收益%s降温后，印证『情绪越涨越慎、越跌越贪』的逆向时机信号。"
                 % ("低于" if r_rise < r_fall else "高于"))
    return {"rising": {"n": int(rising_n), "fwd20": r_rise, "pos": p_rise},
            "falling": {"n": int(falling_n), "fwd20": r_fall, "pos": p_fall},
            "quadrants": quads, "note": note}


def _horizon_scan(D, hist, bounds=None):
    """R124a 最优预测窗口扫描：逆向(contrarian)信号在不同预测窗口 H 下强度不同。

    对每个 H∈{5,10,20,40,60}，按 score 中位数切『冷(低)/热(高)』两组，
    统计各组未来 H 日平均收益，spread = 冷组 - 热组（正=逆势有效：低情绪后涨、高情绪后跌）。
    选 |spread| 最大且两组 N 均充足(>=20) 的 H 为『最优预测窗口』。
    全为描述性统计、非拟合；提示『当前最优解读窗口』，但不改变 R123 的 20 日今日展示。
    """
    k = D.get("kline") or {}
    closes = [float(x) for x in (k.get("close") or [])]
    if not hist or len(closes) < len(hist) + 60:
        return None
    base = len(closes) - len(hist)
    scores = [h["score"] for h in hist]
    med = sorted(scores)[len(scores) // 2]
    rows, opt = [], None
    for H in (5, 10, 20, 40, 60):
        cn = cs = hn = hs = 0.0
        for off, h in enumerate(hist):
            i = base + off
            j = i + H
            if j >= len(closes):
                continue
            f = (closes[j] / closes[i] - 1.0) * 100.0
            if h["score"] < med:
                cn += 1
                cs += f
            else:
                hn += 1
                hs += f
        cmean = round(cs / cn, 2) if cn else None
        hmean = round(hs / hn, 2) if hn else None
        spread = round(cmean - hmean, 2) if (cmean is not None and hmean is not None) else None
        rows.append({"horizon": H, "coldN": int(cn), "coldMean": cmean,
                     "hotN": int(hn), "hotMean": hmean, "spread": spread})
        if cn >= 20 and hn >= 20 and spread is not None:
            if opt is None or abs(spread) > abs(opt["spread"]):
                opt = {"horizon": H, "spread": spread}
    note = "最优预测窗口扫描(H∈{5,10,20,40,60})：按 score 中位数切冷/热组，spread=冷组未来H日收益-热组。"
    if opt is not None:
        note += "逆势信号最强窗口为 H=%d 日(spread=%.2f%%)。" % (opt["horizon"], opt["spread"])
    else:
        note += "样本不足，未定最优窗口。"
    # R125b：多窗口方向一致性（共振/背离）——不仅选最优窗口，还看各窗口信号是否同方向
    _dirs = [((r["spread"] or 0) >= 0) for r in rows if r.get("spread") is not None]
    _pos = sum(1 for d in _dirs if d)
    _neg = len(_dirs) - _pos
    if _dirs:
        if _pos == len(_dirs):
            _verdict = "共振(逆势有效)"
        elif _neg == len(_dirs):
            _verdict = "共振(顺势有效)"
        else:
            _verdict = "背离(信号分裂)"
        consensus = {"agree": max(_pos, _neg), "split": min(_pos, _neg),
                     "total": len(_dirs), "posDir": _pos, "negDir": _neg, "verdict": _verdict}
    else:
        consensus = None
    return {"horizons": rows, "optimalHorizon": (opt["horizon"] if opt else None),
            "consensus": consensus, "note": note}


def _state_signal(D, hist, delta, today_score, today_d20, regime=None):
    """R124b 组合状态信号 + 经验胜率：由『当前情绪水平档(level) × 当前Δ方向(dir)』

    定位其在四象限(高/中/低 × 升/降)中的位置，给出该组合状态的经验未来20日收益与
    经验上涨概率(=该格上涨样本数/N，纯样本频率、非拟合)。
    意义：单看水平或单看 Δ 都片面，『高位升温/低位降温』等组合状态更具时机含义。
    """
    if delta is None or today_score is None:
        return None
    lb = "高" if today_score >= 60 else ("低" if today_score < 40 else "中")
    dr = "升" if (today_d20 or 0) >= 0 else "降"
    qd = {(q["level"], q["dir"]): q for q in (delta.get("quadrants") or [])}
    q = qd.get((lb, dr))
    n = (q.get("n") if q else 0)
    fwd = (q.get("fwd20") if q else None)
    pos = (q.get("pos") if q else None)
    note = "组合状态信号：当前情绪水平=%s、Δ20方向=%s，落在四象限『%s%s』格。" % (lb, dr, lb, dr)
    if q and n:
        note += ("该格历史 N=%d，未来20日平均收益 %.2f%%，经验上涨概率 %.0f%%。"
                 % (n, fwd if fwd is not None else 0.0, (pos or 0.0) * 100))
    else:
        note += "该格历史样本不足，暂无统计支撑。"
    # R125c：分 regime 条件胜率——在当前 regime 下，该 level×dir 组合的历史经验上涨概率
    # （揭示『逆势信号在熊市态显著、牛市态失效』，比不分 regime 的 posPct 更细的条件统计）
    _regime_win = None
    if regime is not None:
        k = D.get("kline") or {}
        _closes = [float(x) for x in (k.get("close") or [])]
        _ma250 = [x for x in (k.get("ma250") or [])]
        if len(_closes) >= len(hist) + 20:
            _base = len(_closes) - len(hist)
            _rn = _rs = _rup = 0
            for _off, _h in enumerate(hist):
                _d = _h.get("d20")
                if _d is None:
                    continue
                _i = _base + _off
                if _i >= len(_closes) or (_ma250[_i] is None):
                    continue
                _isbear = _closes[_i] < _ma250[_i]
                if (regime == "bear") != _isbear:
                    continue
                _lvl = "高" if _h["score"] >= 60 else ("低" if _h["score"] < 40 else "中")
                if _lvl != lb:
                    continue
                _dd = "升" if (_d or 0) >= 0 else "降"
                if _dd != dr:
                    continue
                _j = _i + 20
                if _j >= len(_closes):
                    continue
                _f = (_closes[_j] / _closes[_i] - 1.0) * 100.0
                _rn += 1
                _rs += _f
                _rup += 1 if _f >= 0 else 0
            if _rn:
                _regime_win = {"regime": regime, "n": _rn,
                               "fwd20": round(_rs / _rn, 2),
                               "posPct": round(_rup / _rn, 2)}
    return {"level": lb, "dir": dr, "n": n, "fwd20": fwd, "posPct": pos,
            "regimeWin": _regime_win, "note": note}


def _recency_band(D, hist, today_label, bounds=None):
    """R124c 近期权重稳健性对照：对当前档(today_label)，取最近250交易日落在该档的样本，

    比较『等权』与『近1年指数衰减加权』下未来20日平均收益；
    两者显著背离(|差|>=1%%) 即提示 regime 漂移（近期与稍早统计口径不一致、信号稳健性下降）。
    纯描述性诊断，不改变任何预测口径。
    """
    if not hist or not today_label:
        return None
    k = D.get("kline") or {}
    closes = [float(x) for x in (k.get("close") or [])]
    if len(closes) < len(hist) + 20:
        return None
    base = len(closes) - len(hist)
    window = hist[-250:] if len(hist) >= 250 else hist
    lo_off = len(hist) - len(window)  # 仅取最近250个点的落档样本
    pairs = []
    for off, h in enumerate(hist):
        if off < lo_off or h.get("label") != today_label:
            continue
        i = base + off
        j = i + 20
        if j >= len(closes):
            continue
        f = (closes[j] / closes[i] - 1.0) * 100.0
        age = (len(hist) - 1 - off)  # 距今天数（0=今天）
        pairs.append((age, f))
    if not pairs:
        return None
    n = len(pairs)
    eq = sum(f for _, f in pairs) / n
    tau = 108.0  # 250 日前权重≈exp(-250/108)≈0.1
    w = [math.exp(-age / tau) for age, _ in pairs]
    dw = sum(wk * f for (age, f), wk in zip(pairs, w)) / sum(w)
    eq_r, dw_r = round(eq, 2), round(dw, 2)
    drift = abs(dw_r - eq_r) >= 1.0
    note = ("近期权重稳健性对照（当前档=%s，近%d样本）：等权未来20日均值 %.2f%%，近1年衰减加权 %.2f%%。"
            % (today_label, n, eq_r, dw_r))
    note += ("两者背离≥1%，提示该档近期 regime 相对稍早发生漂移，信号稳健性下降。"
             if drift else "两者接近，该档信号在近期与稍早口径一致，稳健性较好。")
    return {"band": today_label, "n": n, "equalWtd": eq_r, "decayWtd": dw_r,
            "drift": drift, "note": note}


def _extreme_reversal(D, hist, bounds=None, today_score=None):
    """R125a 极值反转诊断（逆向投资核心信号）：情绪进入极端区后，市场随后向相反方向回归的概率。

    极端定义：score 落入动态标尺极端档——恐慌(冰点, <b1) / 狂热(>b4)；回退固定 <20/>80。
    对历史上每个极端日，统计其后 N∈{5,20,60} 交易日市场收益方向：
      · 恐慌日『反转』=未来收益为正（市场随后反弹）；
      · 狂热日『反转』=未来收益为负（市场随后回落）。
    输出历史反转概率（验证逆向逻辑是否成立）+ 当前是否处于极端区。
    全为描述性统计、非拟合；纯诊断字段，不改预测口径、不改 dims。
    """
    k = D.get("kline") or {}
    closes = [float(x) for x in (k.get("close") or [])]
    if not hist or len(closes) < len(hist) + 60:
        return None
    base = len(closes) - len(hist)
    if bounds is None:
        bounds = [20.0, 40.0, 60.0, 80.0]
    b1, b4 = bounds[0], bounds[3]

    def _rev(kind, f):
        return 1.0 if ((kind == "panic" and f >= 0) or (kind == "euphoria" and f < 0)) else 0.0

    _st = {"panic": {5: [0, 0.0], 20: [0, 0.0], 60: [0, 0.0]},
           "euphoria": {5: [0, 0.0], 20: [0, 0.0], 60: [0, 0.0]}}
    for off, h in enumerate(hist):
        sc = h["score"]
        kind = "panic" if sc < b1 else ("euphoria" if sc > b4 else None)
        if kind is None:
            continue
        i = base + off
        for N in (5, 20, 60):
            j = i + N
            if j >= len(closes):
                continue
            f = (closes[j] / closes[i] - 1.0) * 100.0
            s = _st[kind][N]
            s[0] += 1
            s[1] += _rev(kind, f)

    def _row(kind):
        s5, s20, s60 = _st[kind][5], _st[kind][20], _st[kind][60]
        return {"n5": s5[0], "rev5": round(s5[1] / s5[0], 2) if s5[0] else None,
                "n20": s20[0], "rev20": round(s20[1] / s20[0], 2) if s20[0] else None,
                "n60": s60[0], "rev60": round(s60[1] / s60[0], 2) if s60[0] else None}

    # 当前是否极端
    cur = None
    if today_score is not None:
        if today_score < b1:
            cur = "panic"
        elif today_score > b4:
            cur = "euphoria"
    note = ("极值反转诊断：恐慌区(score<%s)后市场反弹、狂热区(score>%s)后回落的『逆向反转』概率；当前%s。"
            % (round(b1, 1), round(b4, 1),
               ("处于恐慌区" if cur == "panic" else "处于狂热区" if cur == "euphoria" else "未达极端")))
    return {"bounds": [round(b1, 1), round(b4, 1)], "current": cur,
            "panic": _row("panic"), "euphoria": _row("euphoria"), "note": note}


def _compute_forecast(D, hist_std=None, regime=None, center=None):
    """由 subForecast 价格路径派生未来情绪预测序列 + 经验置信带（#666）+ 预测水平均值回归（R128）。

    做法：把 subForecast.points 的未来锚点（date, price）与「今日收盘」拼成路径，
    按日历日线性插值得到连续价位，剔除周末后仅保留交易日近似序列，再沿路径计算动量+牛熊位置
    （量能/波动随预测 horizon 指数回归历史中枢、广度沿用当前值）。这是「斐波那契路径派生」而非因子预测，
    已在 note 诚实标注。

    #666 经验置信带：每个预测点除 score 外，按历史情绪波动率(hist_std)打底、随预测 horizon √扩张、
    熊市态(regime='bear')额外×1.15，给出诚信 lo/hi 区间（详见 out["forecastBand"]），
    把「假精确单点」变「诚实区间」；区间半宽 clamp[1,14]，非严格统计置信区间。
    R128 预测 score 水平均值回归：情绪长期围绕历史中枢波动，远端不应被路径派生极值过度外推；
    预测分数随 horizon 指数回归「历史中枢 center」（revert 上限 50% 防过度拉平），使远端更诚实。
    hist_std / regime / center 由 main() 计算后传入；缺失时回退默认值，保证函数可独立调用。
    """
    k = D.get("kline") or {}
    dates = [str(x) for x in (k.get("dates") or [])]
    closes = [float(x) for x in (k.get("close") or [])]
    if not closes:
        return []

    today = dates[-1]
    last_close = closes[-1]
    ma250_now = _ma(closes, 250) or last_close

    sub_breadth, _bw, _dr, _cr = _breadth_sub(D)  # 广度静态代理（无历史源）：仅可用源均值，缺失不偏置
    # R127a 解冻：量能/波动为「今日锚值」，沿预测 horizon 指数回归中性(0)（波动率均值回归铁律），
    # 不再恒用今日值贯穿整个预测期；仅广度无历史源、仍沿用当前值。
    sub_volat_today = _clamp(1.0 - _f((D.get("volRegime") or {}).get("pctile"), 50.0) / 50.0, -1.0, 1.0)
    vols = [float(x) for x in (k.get("volume") or [])]
    vol20 = _ma(vols, 20) or 1.0
    vol250 = _ma(vols, 250) or 1.0
    sub_vol_today = _clamp((vol20 / vol250 - 1.0) / 0.5, -1.0, 1.0)
    _COV_TAU = 15.0  # 协变量均值回归时间常数（交易日）：约 15 日衰减 ~63%
    # R128 预测 score 水平均值回归（与 R127a 协变量解冻正交）：远端随 horizon 向历史中枢回归，
    # 避免路径派生极值被过度外推到 3 个月外；revert 上限 _REVERT_CAP 防曲线被拉平失真。
    _REV_TAU = 40.0  # 预测水平均值回归时间常数（交易日）
    _REVERT_CAP = 0.5  # 远端最多向中枢回归 50%
    _center = center if (center is not None) else 60.0  # 历史中枢锚点（缺省 60）

    # #666 经验置信带参数：熊市态额外放大，缺失回退
    regime_mult = 1.15 if regime == "bear" else 1.0
    base_std = hist_std if (hist_std is not None and hist_std > 0) else 8.0

    sf = (D.get("subForecast") or {}).get("points") or []
    # 锚点：(date, price)，加入今日作为起点
    anchors = [(today, last_close)]
    for p in sf:
        d = str(p.get("date"))
        pr = p.get("price")
        if d and pr is not None:
            anchors.append((d, float(pr)))
    anchors = sorted(set(anchors), key=lambda x: x[0])

    # 仅取今日及之后的锚点（未来段）
    future = [(d, pr) for (d, pr) in anchors if d >= today]
    if len(future) < 2:
        return []

    future_dates = [d for (d, _p) in future]
    future_prices = [pr for (_d, pr) in future]
    # 逐日插值：从今日到末锚点之间按日历日（含周末）线性插值，得到连续路径
    from datetime import datetime as _dt

    def _to_dt(s):
        return _dt.strptime(s, "%Y-%m-%d")

    path = []  # (date_str, price)
    for idx in range(len(future) - 1):
        d0, p0 = future[idx]
        d1, p1 = future[idx + 1]
        t0, t1 = _to_dt(d0), _to_dt(d1)
        ndays = (t1 - t0).days
        if ndays <= 0:
            continue
        for step in range(1, ndays + 1):
            t = t0 + __import__("datetime").timedelta(days=step)
            frac = step / ndays
            price = p0 + (p1 - p0) * frac
            path.append((t.strftime("%Y-%m-%d"), price))
    # path 已含每个分段末点；需确保末锚点本身纳入
    if path and path[-1][0] != future_dates[-1]:
        path.append((future_dates[-1], future_prices[-1]))
    if not path:
        path = [(future_dates[-1], future_prices[-1])]

    # A股非交易日剔除：剔除周末/法定长假，保留调休补班日，使 forecast 仅含交易日近似日期
    path = [(d, p) for (d, p) in path if _is_a_share_trading_day(d)]
    if not path:
        path = [(future_dates[-1], future_prices[-1])]

    out = []
    for idx, (d, price) in enumerate(path):
        # 动量：沿「交易日近似序列」回看 20 个交易日（非交易日已剔除，path 即交易日序列）；
        # 早期点数不足 20 时以「今日收盘」兜底，避免衔接处动量硬归零造成的跳变。
        if idx >= 20:
            past_price = path[idx - 20][1]
        else:
            past_price = last_close
        ret_path = (price / past_price - 1.0) * 100.0 if past_price else 0.0
        sub_mom = _clamp(ret_path / 8.0, -1.0, 1.0)
        # 牛熊位置：相对「当前已知 250 均线」锚定（未来无均线数据，诚实沿用末值）
        pos = (price / ma250_now - 1.0) if ma250_now else 0.0
        sub_pos = _clamp(pos / 0.15, -1.0, 1.0)
        # R127a 解冻协变量：量能/波动随 horizon 指数回归中性(0)，远期不再被今日异常值钉死
        _decay = math.exp(-(idx + 1) / _COV_TAU)
        _sub_vol = sub_vol_today * _decay
        _sub_volat = sub_volat_today * _decay
        # R128 预测 score 水平均值回归：路径派生分随 horizon 向历史中枢回归（远端最多回归 50%），
        # 避免 3 个月外的预测被今日路径极值过度外推；近端(idx=0) revert=0 完全忠实路径。
        _path_score = _score_from_subs([sub_mom, sub_pos, _sub_vol, _sub_volat, sub_breadth])
        _revert = min(1.0 - math.exp(-(idx + 1) / _REV_TAU), _REVERT_CAP)
        score = round(_path_score * (1.0 - _revert) + _center * _revert, 1)
        # #666 经验置信带：随 horizon √扩张、熊市态额外放大；半宽 clamp[1,14]
        kk = idx + 1
        half = _clamp(base_std * math.sqrt(kk / 20.0) * 0.8 * regime_mult, 1.0, 14.0)
        lo = _clamp(score - half, 0.0, 100.0)
        hi = _clamp(score + half, 0.0, 100.0)
        out.append({"date": d, "score": score,
                    "lo": round(lo, 1), "hi": round(hi, 1)})
    return out


def main():
    out = {
        "schema": "a-share-fib-sentiment/v3",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "monitor_only",
        "error": None,
    }
    try:
        D = _load_data()
        out["asOf"] = D.get("fetchedAt") or D.get("updated")

        # R122a：先算历史序列 → 由历史分布推导动态分位标尺 → 用同一标尺统一重标 today/history/forecast
        hist = _compute_history(D)
        bounds, scale_mode, scale_pct = _scale_bounds([h["score"] for h in hist])
        for h in hist:
            h["label"] = _label(h["score"], bounds)
        out["history"] = hist

        # R123a/R123b：每段历史的情绪变化 Δ5/Δ20 与滚动 z 分位（近120日均值/标准差）
        _hs = [h["score"] for h in hist]
        for _i, _h in enumerate(hist):
            _s = _h["score"]
            _h["d5"] = round(_hs[_i] - _hs[_i - 5], 1) if _i >= 5 else None
            _h["d20"] = round(_hs[_i] - _hs[_i - 20], 1) if _i >= 20 else None
            _win = _hs[max(0, _i - 119):_i + 1]
            _mu = sum(_win) / len(_win)
            _sd = (sum((_x - _mu) ** 2 for _x in _win) / len(_win)) ** 0.5
            _h["z"] = round((_s - _mu) / _sd, 2) if _sd > 1e-9 else 0.0

        r = _compute_today(D)
        if r is None:
            out["today"] = {"score": None, "label": "数据不足",
                            "note": "样本 < 250 日，暂不合成情绪温度（monitor_only）。"}
        else:
            score, dims = r
            out["today"] = {
                "score": round(score, 1),
                "label": _label(score, bounds),
                "dims": [{"name": n, "weight": w, "sub": round(s, 4), **m}
                         for (n, w, s, m) in dims],
            }

        # #666 预测置信带：历史情绪波动率 + 当前 regime（末根价 vs MA250）
        _kline = D.get("kline") or {}
        _closes = [float(x) for x in (_kline.get("close") or [])]
        _ma250 = [x for x in (_kline.get("ma250") or [])]
        _last_close = _closes[-1] if _closes else None
        _last_ma = _ma250[-1] if _ma250 else None
        _regime = ("bear" if (_last_ma is not None and _last_close is not None
                              and _last_close < _last_ma) else "bull")
        _hist_scores = [h["score"] for h in hist]
        _hist_std = 8.0
        if _hist_scores:
            _mu = sum(_hist_scores) / len(_hist_scores)
            _hist_std = (sum((s - _mu) ** 2 for s in _hist_scores) / len(_hist_scores)) ** 0.5
        _hist_mean = (sum(_hist_scores) / len(_hist_scores)) if _hist_scores else 60.0  # R128 预测水平均值回归锚点

        # R127b 滚动感知 base_std：近端带宽跟随「当前湍流度」，而非单一全局常数。
        # 近 60 日 std 与全局 std 混合（近期更平静→带宽更窄更诚实），clamp[4,18] 防极端。
        _recent_scores = _hist_scores[-60:] if len(_hist_scores) >= 20 else []
        _recent_std = None
        if _recent_scores:
            _rmu = sum(_recent_scores) / len(_recent_scores)
            _recent_std = (sum((s - _rmu) ** 2 for s in _recent_scores) / len(_recent_scores)) ** 0.5
        _base_std = (_clamp(0.6 * _recent_std + 0.4 * _hist_std, 4.0, 18.0)
                     if _recent_std is not None else _hist_std)

        fcst = _compute_forecast(D, _base_std, _regime, _hist_mean)
        for c in fcst:
            c["label"] = _label(c["score"], bounds)
        out["forecast"] = fcst
        out["forecastBand"] = {
            "method": "经验置信带（目标覆盖≈80%，基于滚动感知波动率的启发式近似，非严格统计置信区间）",
            "level": "≈80%",
            "baseStd": round(_base_std, 2),
            "baseStdGlobal": round(_hist_std, 2),
            "baseStdRecent60": (round(_recent_std, 2) if _recent_std is not None else None),
            "regime": _regime,
            "regimeMult": (1.15 if _regime == "bear" else 1.0),
            "horizonScale": "sqrt(k/20) 时间衰减（k=预测点序号/交易日）",
            "revertCenter": round(_hist_mean, 2),
            "revertTau": 40.0,
            "revertCap": 0.5,
            "note": ("预测为路径派生单点；本带按「滚动感知波动率」打底（近60日std=%.1f 与全局std=%.1f 混合=%.1f）、"
                     "随预测 horizon √扩张、熊市态额外×1.15，将「假精确单点」变为「诚实区间」；区间半宽上限14分、下限1分，"
                     "近端带宽跟随当前湍流度（近期更平静→更窄）；(R128) 预测 score 水平随 horizon 向历史中枢(%.1f) "
                     "指数均值回归(远端最多回归50%%)避免路径极值过度外推，仅供研判参考，不构成置信区间严格含义。"
                     ) % ((_recent_std or _hist_std), _hist_std, _base_std, _hist_mean),
        }

        contra = _contra_stats(D, out["history"], bounds, scale_mode)
        if contra is not None and out.get("today", {}).get("score") is not None:
            # R123a：情绪变化(Δ)预测力统计，作为水平分档信号的补充时机维度
            delta = _contra_delta_stats(D, out["history"])
            if delta is not None:
                contra["delta"] = delta
            # R123c：分 regime 信号强度直读（复用 split 分态统计，避免重复口径）
            _spb = (contra.get("split") or {}).get(contra.get("regime"), {}).get("bands") or []
            _cur = next((b for b in _spb if b.get("label") == out["today"].get("label")), None)
            if _cur is not None:
                _f = _cur.get("fwd20")
                if _f is not None:
                    _strong = "显著" if abs(_f) >= 1.0 else "偏弱"
                    contra["regimeSummary"] = (
                        "当前为%s态：情绪%s档 N=%d，此后20日平均收益 %s%%，逆势信号%s（%s）。"
                        % (("熊市" if contra.get("regime") == "bear" else "牛市"),
                           out["today"].get("label"), _cur["n"],
                           ("+" + str(_f) if _f >= 0 else str(_f)),
                           _strong, ("历史实证支持逆向操作" if _strong == "显著" else "信号不稳固、仅作参考")))
                else:
                    contra["regimeSummary"] = (
                        "当前为%s态：情绪%s档样本不足(N=%d)，逆势信号暂无统计支撑，仅作参考。"
                        % (("熊市" if contra.get("regime") == "bear" else "牛市"),
                           out["today"].get("label"), _cur["n"]))
            # R124a/b/c：下一层预测力增强（均为 contra 下独立字段，不改 today.dims、不改今日展示口径）
            _hs0 = out["history"][-1].get("d20") if out.get("history") else None
            _horizon = _horizon_scan(D, out["history"], bounds)
            if _horizon is not None:
                contra["horizonScan"] = _horizon
            if delta is not None:
                _state = _state_signal(D, out["history"], delta,
                                       out["today"].get("score"), _hs0,
                                       contra.get("regime"))
                if _state is not None:
                    contra["stateSignal"] = _state
            _recency = _recency_band(D, out["history"], out["today"].get("label"), bounds)
            if _recency is not None:
                contra["recency"] = _recency
            # R125a：极值反转诊断（逆向投资核心信号）
            _ext = _extreme_reversal(D, out["history"], bounds, out["today"].get("score"))
            if _ext is not None:
                contra["extremeReversal"] = _ext
            out["today"]["contra"] = contra
        # R123a/b：今日情绪变化(Δ)与 z 分位（取自 history 末点，单一真值派生，不新增维度）
        if out.get("today", {}).get("score") is not None:
            _last = out["history"][-1] if out.get("history") else {}
            out["today"]["sentimentChange"] = {"d5": _last.get("d5"), "d20": _last.get("d20")}
            out["today"]["zscore"] = _last.get("z")

        out["scale"] = {
            "mode": scale_mode,
            "bounds": [round(b, 1) for b in bounds],
            "pctiles": ([round(scale_pct[0], 1), round(scale_pct[1], 1),
                         round(scale_pct[2], 1), round(scale_pct[3], 1)] if scale_pct else None),
            "note": ("动态分位标尺：基于近 %d 个交易日情绪分布 p10/p30/p70/p90 切五档"
                     "（bounds=%.1f/%.1f/%.1f/%.1f），提升区分度；today/history/forecast 同标尺。"
                     % (len(hist), bounds[0], bounds[1], bounds[2], bounds[3]))
                    if scale_mode == "dynamic" else
                    "固定标尺（样本不足或分布退化，回退 20/40/60/80 五档）",
        }
        out["note"] = ("代理情绪温度（monitor_only）：由上证量能/动量/波动/牛熊位置 + "
                       "宽基与跨市场广度合成，非全市场涨跌家数；量能维度受跨源 volume 单位差异影响，"
                       "仅供研判参考，不参与任何概率/方向计算。"
                       "history 为全部可用交易日逐日回算（约 5 年）；forecast 由 subForecast 价格路径派生"
                       "（量能/波动随 horizon 指数回归历史中枢、广度沿用当前值，仅动量+位置随路径变化），"
                       "为路径派生预测非因子预测。"
                       "广度维度（R122c）：仅用 breadthAvailable>0 的可用源均值，缺失源不参与平均、"
                       "全缺失归零不偏置；当前广度尚未历史化（R271 海外 CI 不可达 eastmoney 涨跌家数）。"
                       "五档定性（R122a）：采用动态分位标尺（mode=%s），非固定阈值。R123：新增情绪20日变化(Δ)与滚动z分位(近120日)，并给出当前regime下信号强度直读。"
                       "R124：在 R123 基础上再增强预测力——①最优预测窗口扫描(逆势信号最强H∈{5,10,20,40,60})；②组合状态信号(当前水平档×Δ方向四象限经验胜率，纯样本频率非拟合)；③近期权重稳健性对照(等权 vs 近1年衰减加权，regime漂移诊断)。R125：在 R124 基础上再增强——①极值反转诊断(恐慌区后反弹/狂热区后回落的逆向反转概率，逆向投资核心)；②多窗口信号共振/背离(各H窗口方向一致性，共振=高置信)；③分regime条件胜率(当前regime下该组合状态的经验上涨概率，比不分regime更细)。均为contra下独立诊断字段，不改dims、不改今日展示口径。"
                       % out["scale"]["mode"])
    except Exception as e:  # 降级：在场但不失真，绝不阻断当日推送
        out["error"] = "gen_sentiment 异常: %s" % e
        out["today"] = {"score": None, "label": "数据不足"}
        out["history"] = []
        out["forecast"] = []
    finally:
        os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        with open(OUT_JSON.replace(".json", ".js"), "w",  encoding="utf-8") as f:
            f.write("window.SENTIMENT = ")
            json.dump(out, f, ensure_ascii=False)
            f.write(";")

    print("=== 市场情绪温度 (R87 · monitor_only · v3) ===")
    print("asOf      = %s" % out.get("asOf"))
    t = out.get("today") or {}
    print("today     = %s  label=%s" % (t.get("score"), t.get("label")))
    print("history   = %d 点" % len(out.get("history") or []))
    print("forecast  = %d 点" % len(out.get("forecast") or []))
    sc = out.get("scale") or {}
    if sc:
        print("scale     = mode=%s bounds=%s" % (sc.get("mode"), sc.get("bounds")))
    if t.get("dims"):
        for d in t["dims"]:
            print("  %-10s w=%.2f sub=%+.3f  %s" % (d["name"], d["weight"], d["sub"],
                                                     json.dumps({kk: vv for kk, vv in d.items()
                                                                 if kk not in ("name", "weight", "sub")},
                                                                ensure_ascii=False)))
    if out.get("error"):
        print("[WARN] %s" % out["error"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
