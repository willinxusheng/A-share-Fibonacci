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
  5. 广度确认   w=0.15  (宽基 resonance.breadth + 跨市场 crossMarket.breadth)/2

输出（schema a-share-fib-sentiment/v3）：
  - today   : 当日单点快照（含 5 维 dims），保持 R87 既有契约。
  - history : 近 250 个交易日逐日回算的历史情绪序列（date, score, label）。
  - forecast: 由 subForecast 价格路径派生（斐波那契路径映射）的未来情绪预测序列（date, score, label）。
              预测段无量能/波动/广度未来因子，故量能沿用当前比值、波动沿用当前分位、广度沿用当前均值，
              仅「动量+牛熊位置」随价格路径变化 —— 这是路径派生预测，非因子预测，已在 note 中诚实标注。

五档定性：<20 冰点 / 20-40 偏冷 / 40-60 中性 / 60-80 偏热 / ≥80 狂热。

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


def _label(score):
    if score < 20:
        return "冰点"
    if score < 40:
        return "偏冷"
    if score < 60:
        return "中性"
    if score < 80:
        return "偏热"
    return "狂热"


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

    # 5. 广度确认（宽基共振 + 跨市场共振 平均）
    res_b = _f((D.get("resonance") or {}).get("breadth"), 0.0)
    cross_b = _f((D.get("crossMarket") or {}).get("breadth"), 0.0)
    sub_breadth = _clamp((res_b + cross_b) / 2.0, -1.0, 1.0)

    dims = [
        ("momentum", 0.30, sub_mom, {"ret20_pct": round(ret20, 2)}),
        ("position", 0.20, sub_pos, {"lastClose": round(last, 2),
                                     "ma250": round(ma250, 2) if ma250 else None,
                                     "dev_pct": round(pos * 100.0, 2)}),
        ("volume", 0.20, sub_vol, {"vol20_vol250_ratio": round(vr, 3)}),
        ("volatility", 0.15, sub_volat, {"hv20_pctile": int(pctile)}),
        ("breadth", 0.15, sub_breadth, {"domestic": round(res_b, 3),
                                        "cross_market": round(cross_b, 3)}),
    ]
    score = round(_score_from_subs([s for (_n, _w, s, _m) in dims]), 1)
    return score, _label(score), dims


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

    res_b = _f((D.get("resonance") or {}).get("breadth"), 0.0)
    cross_b = _f((D.get("crossMarket") or {}).get("breadth"), 0.0)
    sub_breadth = _clamp((res_b + cross_b) / 2.0, -1.0, 1.0)  # 广度静态代理（无历史源）

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
        out.append({"date": dates[i], "score": score, "label": _label(score)})
    return out  # 全部可用点（约 5 年），不再截断到 250


def _contra_stats(D, hist):
    """逆向信号统计（单一真值，随窗口全量计算）。

    对每个 history 点按 label 分档，统计各档样本数 N 与「未来 20 交易日平均收益」。
    实证：score 与未来收益负相关（逆势信号）。返回 {bands: [{label, n, fwd20}], note}。
    """
    k = D.get("kline") or {}
    closes = [float(x) for x in (k.get("close") or [])]
    if not hist or len(closes) < len(hist) + 20:
        return None
    base = len(closes) - len(hist)
    bands = [("冰点", 0, 20), ("偏冷", 20, 40), ("中性", 40, 60), ("偏热", 60, 80), ("狂热", 80, 101)]
    out_bands = []
    for (lab, lo, hi) in bands:
        n, s = 0, 0.0
        for off, h in enumerate(hist):
            if lo <= h["score"] < hi:
                j = base + off + 20
                if j < len(closes):
                    n += 1
                    s += (closes[j] / closes[base + off] - 1.0) * 100.0
        out_bands.append({
            "label": lab,
            "n": n,
            "fwd20": round(s / n, 2) if n else None,
        })
    return {
        "bands": out_bands,
        "note": "近%d个交易日全量样本：score 与未来20日收益负相关（逆势信号）；N 为该档样本数" % len(hist),
    }


def _compute_forecast(D):
    """由 subForecast 价格路径派生未来情绪预测序列。

    做法：把 subForecast.points 的未来锚点（date, price）与「今日收盘」拼成路径，
    按日历日线性插值得到连续价位，剔除周末后仅保留交易日近似序列，再沿路径计算动量+牛熊位置
    （量能/波动/广度沿用当前值）。这是「斐波那契路径派生」而非因子预测，已在 note 诚实标注。
    """
    k = D.get("kline") or {}
    dates = [str(x) for x in (k.get("dates") or [])]
    closes = [float(x) for x in (k.get("close") or [])]
    if not closes:
        return []

    today = dates[-1]
    last_close = closes[-1]
    ma250_now = _ma(closes, 250) or last_close

    res_b = _f((D.get("resonance") or {}).get("breadth"), 0.0)
    cross_b = _f((D.get("crossMarket") or {}).get("breadth"), 0.0)
    sub_breadth = _clamp((res_b + cross_b) / 2.0, -1.0, 1.0)
    sub_volat = _clamp(1.0 - _f((D.get("volRegime") or {}).get("pctile"), 50.0) / 50.0, -1.0, 1.0)
    vols = [float(x) for x in (k.get("volume") or [])]
    vol20 = _ma(vols, 20) or 1.0
    vol250 = _ma(vols, 250) or 1.0
    sub_vol = _clamp((vol20 / vol250 - 1.0) / 0.5, -1.0, 1.0)

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
        score = round(_score_from_subs([sub_mom, sub_pos, sub_vol, sub_volat, sub_breadth]), 1)
        out.append({"date": d, "score": score, "label": _label(score)})
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

        r = _compute_today(D)
        if r is None:
            out["today"] = {"score": None, "label": "数据不足",
                            "note": "样本 < 250 日，暂不合成情绪温度（monitor_only）。"}
        else:
            score, label, dims = r
            out["today"] = {
                "score": round(score, 1),
                "label": label,
                "dims": [{"name": n, "weight": w, "sub": round(s, 4), **m}
                         for (n, w, s, m) in dims],
            }

        out["history"] = _compute_history(D)
        out["forecast"] = _compute_forecast(D)
        contra = _contra_stats(D, out["history"])
        if contra is not None:
            out["today"]["contra"] = contra
        out["note"] = ("代理情绪温度（monitor_only）：由上证量能/动量/波动/牛熊位置 + "
                       "宽基与跨市场广度合成，非全市场涨跌家数；量能维度受跨源 volume 单位差异影响，"
                       "仅供研判参考，不参与任何概率/方向计算。"
                       "history 为全部可用交易日逐日回算（约 5 年）；forecast 由 subForecast 价格路径派生"
                       "（量能/波动/广度沿用当前值，仅动量+位置随路径变化），为路径派生预测非因子预测。")
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
