# -*- coding: utf-8 -*-
"""生成前端图表数据 data/data.js（v3 专业版）。

v3 新增：艾略特通道（浪2-浪4连线+过浪3顶平行线）、三铁律+两规则校验卡、
交替规则验证、MA60/MA250、20日年化波动率及五年分位、周线多周期共振、
情景观察矩阵（三区框架）。

浪型标注（大级别，人工校订，基于 8% Zigzag + 三铁律）：
- 2021-09-14 3723.85 起大级别 ABC 调整：A 2863.65 / B 3424.84 / C 2635.09（C≈0.92×A）
- 2024-09-18 2689.70 起大级别推动浪：
  浪1 2689.70->3674.40 (+984.70) / 浪2 ->3040.69 (回撤64.4%)
  浪3 ->4258.86 (1.237x浪1, 子浪延长) / 浪4 ->3741.11 (回撤42.5%, 未确认)
"""
import json
import math
import os
import shutil
import statistics
import datetime as _dt
import time
import urllib.request

import numpy as np
import pandas as pd

from analyze import zigzag_pct, read_kline_md
from backtest import run_backtest, MIN_SAMPLE
from calibrate import run_calibration as _run_calib   # 概率模型 walk-forward 实证校准
import datafeed  # R271 多源回退取数（eastmoney 主源 -> yahoo/stooq 海外可达回退）

BASE = os.path.dirname(os.path.abspath(__file__))

# eastmoney 近期拒绝仅带简单 User-Agent 的 urllib 请求(RemoteDisconnected)，
# 需补全浏览器级请求头(Referer/Accept/identity 编码)方可正常返回。
_EASTMONEY_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "identity",
    "Referer": "https://quote.eastmoney.com/",
    "Connection": "close",
}


def r2(x):
    return round(float(x), 2)


# ---------- R101 子浪结构识别与经验比率测量（同浪级 5 浪上行推动浪）----------
def _detect_five_wave_runs(zigzag, min_leg_days=12, min_net=0.02):
    """从 zigzag 识别连续 5 腿上行推动浪（同浪级）。
    返回每组的 pivot 价 p0..p5 与对应日期 d0..d5；要求：
      - 5 腿方向严格 + - + - +（上行推动浪）；
      - 每腿交易日跨度 >= min_leg_days（同浪级过滤，剔除微小噪声腿）；
      - 净涨幅 = (p5-p0)/p0 >= min_net（确认上行大浪而非横盘）。
    防坑：严格的方向 + 净涨过滤，剔除原始 zigzag 直接取会出的 >1 荒谬回撤结构。
    """
    legs = []
    for i in range(len(zigzag) - 1):
        p0, p1 = zigzag[i], zigzag[i + 1]
        try:
            ld = max(1, len(pd.bdate_range(pd.Timestamp(p0["date"]), pd.Timestamp(p1["date"]))) - 1)
            lr = math.log(float(p1["price"]) / float(p0["price"]))
        except Exception:
            continue
        if ld < min_leg_days:
            continue
        legs.append({"dir": 1 if lr > 0 else -1,
                     "p0": float(p0["price"]), "p1": float(p1["price"]),
                     "d0": str(p0["date"]), "d1": str(p1["date"])})
    runs = []
    for s in range(0, len(legs) - 4):
        wl = legs[s:s + 5]
        if not (wl[0]["dir"] > 0 and wl[1]["dir"] < 0 and wl[2]["dir"] > 0
                and wl[3]["dir"] < 0 and wl[4]["dir"] > 0):
            continue
        p = [wl[0]["p0"], wl[0]["p1"], wl[1]["p1"], wl[2]["p1"], wl[3]["p1"], wl[4]["p1"]]
        d = [wl[0]["d0"], wl[0]["d1"], wl[1]["d1"], wl[2]["d1"], wl[3]["d1"], wl[4]["d1"]]
        if (p[5] - p[0]) / p[0] < min_net:
            continue
        runs.append({"p": p, "d": d})
    return runs


def _measure_subwave_ratios(zigzag):
    """量取同浪级 5 浪上行结构的子浪比率中位数（带防坑 sanity clamp）。
    返回 {ii_ret, iii, iv_ret}（相对子浪ⅰ幅度）或 None（样本不足）。
    ii_ret=(p1-p2)/(p1-p0) 子浪ⅱ回撤占ⅰ；iii=(p3-p2)/(p1-p0) 子浪ⅲ占ⅰ；
    iv_ret=(p3-p4)/(p3-p2) 子浪ⅳ回撤占ⅲ。
    """
    runs = _detect_five_wave_runs(zigzag)
    if len(runs) < 6:
        return None
    ii, iii, iv = [], [], []
    for r in runs:
        p = r["p"]
        w1, w2, w3, w4 = p[1] - p[0], p[1] - p[2], p[3] - p[2], p[3] - p[4]
        if not (w1 > 0 and w2 > 0 and w3 > 0 and w4 > 0):
            continue
        r2_, r3, r4 = w2 / w1, w3 / w1, w4 / w3
        if not (0.20 <= r2_ <= 0.70 and 1.0 <= r3 <= 2.20 and 0.20 <= r4 <= 0.60):
            continue
        ii.append(r2_); iii.append(r3); iv.append(r4)
    if len(ii) < 6:
        return None
    return {"ii_ret": round(statistics.median(ii), 4),
            "iii": round(statistics.median(iii), 4),
            "iv_ret": round(statistics.median(iv), 4),
            "n": len(ii)}


def _subwave_baseline_error(zigzag, ratios):
    """P0 基线 + P2 离散度：用给定比率模型在每历史 5 浪结构上量预测误差。
    ratios=None 用斐波那契标准(0.5/1.618/0.2917)。返回 {n, mae_price_frac,
    disp_pct(68分位子浪拐点价误差%)，date_cum(历史累计时间占比中位数)}。
    """
    bii, biii, biv = (0.5, 1.618, 0.2917) if ratios is None else (ratios["ii_ret"], ratios["iii"], ratios["iv_ret"])
    runs = _detect_five_wave_runs(zigzag)
    if not runs:
        return None
    perr_frac, pe_pct, derr = [], [], []
    for r in runs:
        p, d = r["p"], r["d"]
        net = p[5] - p[0]
        amp = 2 - bii + biii - biv * biii
        ri = 1.0 / amp
        pred = [p[0] + ri * net, p[0] + ri * (1 - bii) * net,
                p[0] + ri * (1 - bii + biii) * net,
                p[0] + ri * (1 - bii + biii - biv * biii) * net, p[5]]
        act = [p[1], p[2], p[3], p[4], p[5]]
        for a, b in zip(pred, act):
            perr_frac.append(abs(a - b) / net)
            pe_pct.append(abs(a - b) / b * 100)
        try:
            span = max(1, (pd.Timestamp(d[5]) - pd.Timestamp(d[0])).days)
            for k in range(1, 5):
                derr.append(max(0.0, min(1.0, (pd.Timestamp(d[k]) - pd.Timestamp(d[0])).days / span)))
        except Exception:
            pass
    return {"n": len(runs),
            "mae_price_frac": round(statistics.median(perr_frac), 4),
            "disp_pct": round(float(np.percentile(pe_pct, 68)), 2),
            "date_cum": [round(statistics.median(derr[i::4]), 3) for i in range(4)] if derr else None}


def safe_idx(idx_list, ts):
    """返回 ts 在交易日列表中的下标；若不存在（如假期）则取其后最近交易日，
    避免 idx.index() 抛 ValueError 拖垮整个看板生成（每日自动化容错）。"""
    try:
        return idx_list.index(ts)
    except ValueError:
        cand = [i for i, d in enumerate(idx_list) if d >= ts]
        return cand[0] if cand else len(idx_list) - 1


def parse_md(path):
    rows = read_kline_md(path)
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").set_index("date")[["close"]]


def rsi14(close):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)


def main():
    df = pd.read_csv(os.path.join(BASE, "data", "sh000001.csv"), parse_dates=["date"]).set_index("date")
    # 成交量单位容错(R62)：每日管线追加的当日行偶发以不同单位(股 vs 手)写入，
    # 造成单点 ~100x 突跳(实测 592M vs 滚动中位 ~5.8M)，会污染 vol_by_wave 均值 与
    # 量价背离判定(近峰量能虚高→背离被压制)。检测相对滚动中位数的 >5x(<0.2x) 异常行，
    # 回缩到本地中位单位，使量能序列单位一致；仅动异常行、不改正常数据，且对每日新注入的
    # 异常行同样生效(与本项目「每日自动化容错」一致)。
    _v = df["volume"].astype(float)
    _med = _v.rolling(60, min_periods=10).median()
    _ratio = _v / _med
    _vmask = _med.notna() & ((_ratio > 5) | (_ratio < 0.2))
    if _vmask.any():
        df.loc[_vmask, "volume"] = _med[_vmask]
        print("  [fix] 成交量单位异常 %d 行已归一化(单位容错)" % int(_vmask.sum()))
    with open(os.path.join(BASE, "data", "structures.json"), encoding="utf-8") as f:
        st = json.load(f)

    # 本指数真实摆动腿（来自 8% Zigzag 完成浪，st["zigzag"]）；仅取 >=10 交易日的完成浪，
    # 每条记录 (带符号对数收益, 交易日跨度)。上行腿 log_ret>0，下行腿<0。
    # 提前至此构建（单一真源），供 子浪时间历史校准(R50 建议4) 与 _hist_calib(R49) 共用。
    _hist_legs = []
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
        _hist_legs.append((_lr, _ld, str(_p0["date"]), str(_p1["date"])))

    ma20 = df["close"].rolling(20).mean()
    ma60 = df["close"].rolling(60).mean()
    ma120 = df["close"].rolling(120).mean()
    ma250 = df["close"].rolling(250).mean()
    rsi = rsi14(df["close"])

    kline = {
        "dates": [d.strftime("%Y-%m-%d") for d in df.index],
        "ohlc": [[r2(o), r2(c), r2(l), r2(h)] for o, c, l, h in
                 zip(df["open"], df["close"], df["low"], df["high"])],
        "close": [r2(c) for c in df["close"]],
        "volume": [int(v) for v in df["volume"]],
        "rsi": [None if pd.isna(v) else round(float(v), 1) for v in rsi],
        "ma20": [None if pd.isna(v) else r2(v) for v in ma20],
        "ma60": [None if pd.isna(v) else r2(v) for v in ma60],
        "ma120": [None if pd.isna(v) else r2(v) for v in ma120],
        "ma250": [None if pd.isna(v) else r2(v) for v in ma250],
    }
    last_date = kline["dates"][-1]
    last_close = kline["close"][-1]

    # ---------- 大级别浪型标注（人工校订）----------
    wave_points = [
        {"date": "2021-09-14", "price": 3723.85, "label": "调整起点", "pos": "top"},
        {"date": "2022-04-27", "price": 2863.65, "label": "A", "pos": "bottom"},
        {"date": "2022-07-05", "price": 3424.84, "label": "B", "pos": "top"},
        {"date": "2024-02-05", "price": 2635.09, "label": "C(5年最低)", "pos": "bottom"},
        {"date": "2024-05-20", "price": 3174.27, "label": "X", "pos": "top"},
        {"date": "2024-09-18", "price": 2689.70, "label": "双底/浪起", "pos": "bottom"},
        {"date": "2024-10-08", "price": 3674.40, "label": "浪1", "pos": "top"},
        {"date": "2025-04-07", "price": 3040.69, "label": "浪2", "pos": "bottom"},
        {"date": "2026-05-14", "price": 4258.86, "label": "浪3", "pos": "top"},
        {"date": "2026-07-20", "price": 3741.11, "label": "浪4?(未确认)", "pos": "bottom"},
    ]
    sub_wave_points = [
        {"date": "2025-04-07", "price": 3040.69, "label": "浪③起", "pos": "bottom"},
        {"date": "2025-11-14", "price": 4034.08, "label": "子浪ⅰ", "pos": "top"},
        {"date": "2025-12-16", "price": 3815.84, "label": "子浪ⅱ", "pos": "bottom"},
        {"date": "2026-03-03", "price": 4197.23, "label": "ⅲ-1", "pos": "top"},
        {"date": "2026-03-23", "price": 3794.68, "label": "ⅲ-2", "pos": "bottom"},
        {"date": "2026-05-14", "price": 4258.86, "label": "子浪ⅲ顶(延长)", "pos": "top"},
        {"date": "2026-07-20", "price": 3741.11, "label": "浪④?(未确认)", "pos": "bottom"},
    ]
    # 子浪ⅱ回撤率（子浪ⅱ/子浪ⅰ），全局单一真值；subRefs 买点注释、ratioCheck、p3Note 共用，
    # 消除"22%"硬编码字面与三处重复计算（浪型若重校订，三处自动同步，不会"注释写22%但比例已变"）
    _sw_i1 = sub_wave_points[1]["price"]; _sw_i2 = sub_wave_points[2]["price"]; _sw_b = sub_wave_points[0]["price"]
    SUB_RET = (_sw_i1 - _sw_i2) / (_sw_i1 - _sw_b) * 100
    # 子浪 zigzag 起点对齐 wave_points[7]（浪③起/浪②底，单一真值派生），避免首 pivot 落在标注日前
    # 造成图3视觉错位；从 wave_points 派生，浪型重校订改日期时自动跟随，不写死双份真值
    seg = df[df.index >= wave_points[7]["date"]]
    sub_zigzag = [{"date": p["index"].strftime("%Y-%m-%d"), "price": r2(p["price"]), "type": p["type"]}
                  for p in zigzag_pct(seg, 0.05)]

    # ---------- 斐波那契位 ----------
    # 5年区间极值从实际数据派生（非硬编码）：每日自动更新若创5年新高/新低，
    # 斐波那契回撤线、1.272/1.618扩展目标、5年区间回撤支撑会自动跟随，不会失真
    hi5, lo5 = float(df["high"].max()), float(df["low"].min())
    # 浪型波段端点从 wave_points 派生（单一真值）：消除与人工校订浪型标注的硬编码双份真值。
    # 浪型重校订（改 wave_points 的 price）时，w3_lo/w3_hi/KEY_LINE/W1_START 自动跟随，
    # 下游 supports/targets/distances/zones/state/subForecast/_enrich 整组同步，
    # 不会"改了浪型标注但铁律线/浪③顶漏改"脱节（契合本项目一贯的单一真值哲学）。
    KEY_LINE = wave_points[6]["price"]   # 浪1顶 / 铁律线（风控位）——全局唯一真值，supports/stopLine/buyZones 共用
    W1_START = wave_points[5]["price"]   # 浪1起点（双底）
    w3_lo = wave_points[7]["price"]      # 浪③波段下界(浪②底)
    w3_hi = wave_points[8]["price"]      # 浪③波段上界(浪③顶)

    def fib_set(lo, hi):
        rng = hi - lo
        return [{"ratio": r, "price": r2(hi - rng * v)} for r, v in
                [("23.6%", 0.236), ("38.2%", 0.382), ("50.0%", 0.5), ("61.8%", 0.618), ("78.6%", 0.786)]]

    fib_5y = fib_set(lo5, hi5)
    fib_w3 = fib_set(w3_lo, w3_hi)
    w1 = max(KEY_LINE - W1_START, 1e-9)  # 除零保护:浪1起点==浪1顶的非法标注兜底(每日自动化容错)
    w3 = w3_hi - w3_lo                    # 浪③幅度，由浪型波段派生（消除硬编码 4258.86-3040.69）
    w4_low = wave_points[-1]["price"]     # 浪④低(未确认)，由浪型标注末点派生（消除硬编码 3741.11）
    targets = [
        {"name": "浪5 = 0.618 x 浪3", "price": r2(w4_low + w3 * 0.618)},
        {"name": "浪5 = 1.618 x 浪1", "price": r2(w4_low + w1 * 1.618)},
        {"name": "5年区间 1.272 扩展", "price": r2(lo5 + (hi5 - lo5) * 1.272)},
        {"name": "浪5 = 浪1 (等幅)", "price": r2(w4_low + w1)},
        {"name": "5年区间 1.618 扩展", "price": r2(lo5 + (hi5 - lo5) * 1.618)},
    ]
    supports = [
        {"name": "浪3回撤 38.2%", "price": r2(w3_hi - w3 * 0.382), "key": False},
        {"name": "浪1顶 (铁律线)", "price": KEY_LINE, "key": True},
        {"name": "浪3回撤 50%", "price": r2(w3_hi - w3 * 0.500), "key": False},
        {"name": "浪3回撤 61.8%", "price": r2(w3_hi - w3 * 0.618), "key": False},
        {"name": "5年区间回撤 50%", "price": r2(hi5 - (hi5 - lo5) * 0.500), "key": False},
    ]

    # ---------- 买卖参考框架（波浪+斐波那契推导，仅供参考）----------
    buy_0382 = w3_hi - w3 * 0.382
    buy_zones = [
        {"name": "核心建仓区", "hi": r2(buy_0382), "lo": KEY_LINE,
         "condition": "浪④回踩 0.382 回撤 ~ 铁律线",
         "note": "浪③回撤 0.382=%.2f 至铁律线 %.2f，守住铁律线数浪有效；分批低吸，首仓靠近铁律线" % (buy_0382, KEY_LINE)},
    ]
    sell_targets = [
        {"name": "卖① 保守兑现", "price": r2(w4_low + w3 * 0.618), "condition": "浪⑤第一目标",
         "note": "0.618×浪③=浪⑤首目标，放量触及可兑现首批"},
        {"name": "卖② 标准兑现", "price": r2(w4_low + w1), "condition": "浪⑤标准目标",
         "note": "浪⑤=浪①等幅（浪3未延长时的标准目标），艾略特通道上轨附近，分批减仓"},
        {"name": "卖③ 激进兑现", "price": r2(w4_low + w1 * 1.618), "condition": "浪⑤上限目标",
         "note": "浪⑤=1.618×浪①（浪1未延长时的拉伸目标），需放量大阳确认，警惕延长浪终结"},
    ]
    stop_line = {"price": KEY_LINE, "name": "铁律线 / 风控位",
                 "note": "收盘有效跌破 → 浪④数法证伪，减仓或清仓转防御"}
    # 子浪ⅱ 买点由 sub_wave_points 派生（单一真值）：消除与 sub_wave_points[2] 硬编码
    # 2025-12-16/3815.84 双份真值；浪型重校订改 sub_wave_points 的 date/price 时自动跟随，
    # 不会"子浪图标注已变但买卖参考买点仍停旧值"脱节（契合本项目一贯的单一真值哲学）
    _sub_ii = next(p for p in sub_wave_points if p["label"].startswith("子浪ⅱ"))
    sub_refs = [
        {"date": _sub_ii["date"], "price": _sub_ii["price"], "type": "buy",
         "note": "浪③子浪ⅱ终止（回撤浪ⅰ仅%.0f%%，偏浅=强势），教科书买点" % SUB_RET},
        {"date": wave_points[8]["date"], "price": wave_points[8]["price"], "type": "sell",
         "note": "子浪ⅲ延长完成，防浪④深度回调，此处减仓"},
    ]
    # 机械化出场阶梯（R50 建议6）：铁律线之外，加"跌破均线减半仓"移动止盈，与铁律线构成多级风控。
    _ma20 = float(df["close"].rolling(20).mean().iloc[-1])
    _ma60_v = float(ma60.iloc[-1]) if not pd.isna(ma60.iloc[-1]) else None
    trailing_stop = {
        "ma20": r2(_ma20), "ma60": r2(_ma60_v) if _ma60_v else None,
        "rules": [
            {"trigger": "收盘跌破 MA20", "price": r2(_ma20), "action": "减半仓，锁定已有利润"},
            {"trigger": "收盘跌破 MA60", "price": r2(_ma60_v) if _ma60_v else None, "action": "再减半 / 转防御"},
            {"trigger": "收盘有效跌破铁律线 %.2f" % KEY_LINE, "price": KEY_LINE, "action": "清仓，浪型数法证伪"},
        ],
    }
    trade_plan = {
        "buyZones": buy_zones, "sellTargets": sell_targets,
        "stopLine": stop_line, "subRefs": sub_refs, "trailingStop": trailing_stop,
    }

    # ---------- 三铁律 + 两规则校验卡 ----------
    idx = list(df.index)
    _w4_low_date = wave_points[-1]["date"]   # 浪4低点(未确认)，与 findings 同源，避免重复字面
    # 浪型拐点日期全部从 wave_points 派生（单一真值）：消除与人工校订浪型的日期双份真值脱节
    # （浪1顶=wp6 / 浪2底=wp7 / 浪3顶=wp8 / 浪起=wp5）。浪型重校订改 wave_points 的 date 时，
    # w2_days/w4_days/_gap/vol_by_wave 切片/时间窗锚点自动跟随，不会"拐点日改了但计时/切片漏改"脱节。
    w2_days = safe_idx(idx, pd.Timestamp(wave_points[7]["date"])) - safe_idx(idx, pd.Timestamp(wave_points[6]["date"]))
    # 浪④时长量到浪4低(与浪②量到浪2底对称)，而非到末日——否则多算 (末日-浪4低) 交易日，
    # 使"交替规则"时序对比基准不对称（浪②用精确时长，浪④若用"至今"会虚增约11日）
    w4_days = safe_idx(idx, pd.Timestamp(_w4_low_date)) - safe_idx(idx, pd.Timestamp(wave_points[8]["date"]))
    rules = [
        {"name": "铁律一 · 浪2不破浪1起点", "ok": True,
         "detail": f"浪2低点 {w3_lo:.2f} > 浪1起点 {W1_START:.2f}"},
        {"name": "铁律二 · 浪3非最短推动浪", "ok": True,
         "detail": "浪3 +%.2f > 浪1 +%.2f" % (w3, w1)},
        {"name": "铁律三 · 浪4不入浪1价格区", "ok": True,
         "detail": "浪4低点 %.2f > 浪1顶 %.2f（缓冲仅%.1f%%，警戒）" % (w4_low, KEY_LINE, (w4_low - KEY_LINE) / KEY_LINE * 100)},
        {"name": "交替规则 · 浪2/浪4形态交替", "ok": True,
         "detail": f"浪②历时{w2_days}个交易日复杂平台型 vs 浪④{w4_days}个交易日急促锯齿型，时间与形态双交替"},
        {"name": "等量特征 · 调整浪C≈A", "ok": True,
         "detail": "大级别调整 C/A = %.2f，符合等量关系" % (wave_points[3]["price"] / wave_points[1]["price"])},
        {"name": "通道规则 · 浪⑤运行于艾略特通道", "ok": None,
         "detail": "浪2-浪4连线及其过浪3顶平行线构成通道，见推演图"},
    ]

    # ---------- 艾略特通道（浪2-浪4连线 + 过浪3顶平行线）----------
    # 锚点全部从 wave_points 派生（浪2底/浪4低/浪3顶），消除与浪型标注双份真值脱节
    _w2p = wave_points[7]   # 浪2底（调整浪结束点）
    _w3p = wave_points[8]   # 浪3顶
    _w4p = wave_points[-1]  # 浪4低（未确认）
    d0, p0 = pd.Timestamp(_w2p["date"]), _w2p["price"]
    d1, p1 = pd.Timestamp(_w4p["date"]), _w4p["price"]
    slope = (p1 - p0) / max(1, (d1 - d0).days)  # 除零保护:浪②底==浪④低日期兜底(每日自动化容错)
    chan_end = pd.Timestamp("2027-09-30")
    channel = {
        "lower": [[_w2p["date"], r2(p0)],
                  [chan_end.strftime("%Y-%m-%d"), r2(p0 + slope * (chan_end - d0).days)]],
        "upper": [[_w3p["date"], r2(_w3p["price"])],
                  [chan_end.strftime("%Y-%m-%d"),
                   r2(_w3p["price"] + slope * (chan_end - pd.Timestamp(_w3p["date"])).days)]],
    }

    # ---------- 浪间比例校验（actual 全部由浪型标注派生，消除与显示卡双份真值漂移）----------
    # 浪型重校订时自动跟随；theory/verdict/ok 为分析判断，保持静态
    _wpv = lambda kw: next(p for p in wave_points if p["label"].startswith(kw))
    _swpv = lambda kw: next(p for p in sub_wave_points if p["label"].startswith(kw))
    _w2_ret = (_wpv("浪1")["price"] - _wpv("浪2")["price"]) / w1 * 100
    _w3_ratio = w3 / w1
    _w4_ret = (_wpv("浪3")["price"] - _wpv("浪4")["price"]) / w3 * 100
    _ca = wave_points[3]["price"] / wave_points[1]["price"]  # 调整浪 C/A
    _sub_ret = SUB_RET
    # 浪型点标签的比例后缀由派生值回填（消除与 ratio_check/rules/p3Note 双份真值脱节：
    # 浪型重校订时标签自动跟随，不会"标签写64.4%但比例卡已变"；仅改 label 文本，不动 price，
    # 对 通道/规则卡(只用 date/price) 无影响。_wpv/_swpv 用 startswith 匹配，标签前缀不变仍命中。
    wave_points[7]["label"] = "浪2(回撤%.1f%%)" % _w2_ret
    wave_points[8]["label"] = "浪3(%.3fx浪1)" % _w3_ratio
    sub_wave_points[2]["label"] = "子浪ⅱ(回撤%.0f%%)" % _sub_ret
    # 浪3/浪1 达标判定由实测比例派生（单一真源）：>=1.5 视为接近经典 1.618 扩展达标，
    # 浪型重校订使比例越过阈值时 ok 自动翻转，避免"verdict 写偏弱但比例已达标"脱节。
    _ok3 = _w3_ratio >= 1.5
    ratio_check = [
        {"item": "调整浪 C/A", "actual": "%.2f" % _ca, "theory": "0.618~1.0", "verdict": "符合", "ok": True},
        {"item": "浪2 回撤浪1", "actual": "%.1f%%" % _w2_ret, "theory": "50%~61.8%", "verdict": "略深，未破起点", "ok": True},
        {"item": "浪3 / 浪1", "actual": "%.3f" % _w3_ratio, "theory": "1.618",
         "verdict": "达标（≥1.5×浪1）" if _ok3 else "偏弱，动能温和", "ok": _ok3},
        {"item": "浪4 回撤浪3", "actual": "%.1f%%" % _w4_ret, "theory": "23.6%~38.2%", "verdict": "略深，守铁律线", "ok": True},
        {"item": "子浪ⅱ 回撤子浪ⅰ", "actual": "%.1f%%" % _sub_ret, "theory": "38.2%~61.8%", "verdict": "偏浅，强势特征", "ok": True},
    ]

    # ---------- 量能验证（按交易日索引切片：无重叠/无遗漏/顶点日归前段）----------
    # 旧版字符串半开区间 [a,b) 会遗漏中间转折点（如浪2底 2025-04-07 既不在浪2也不在浪3），
    # 且把浪1顶点天量日 2024-10-08 误归浪2。改用索引切片：相邻段 _ti[k]+1 衔接，
    # 覆盖 [浪1起点(2024-09-18), 末日] 全量无遗漏（此前 ABC 调整期约730日非推动浪，不计入各浪量能）；
    # 浪1含顶点日，浪2底/浪3顶各归其终点段。
    # 浪型波段拐点从 wave_points 派生（浪起wp5 / 浪1顶wp6 / 浪2底wp7 / 浪3顶wp8），
    # 消除与人工校订浪型日期的双份真值脱节；浪型重校订改 wave_points 的 date 时切片边界自动同步
    _turn = [wave_points[5]["date"], wave_points[6]["date"], wave_points[7]["date"], wave_points[8]["date"]]
    _ti = [safe_idx(idx, pd.Timestamp(d)) for d in _turn]
    _last_i = len(idx) - 1
    _bounds = [(_ti[0], _ti[1]), (_ti[1] + 1, _ti[2]),
               (_ti[2] + 1, _ti[3]), (_ti[3] + 1, _last_i)]
    vol_by_wave = []
    for _n, (_s, _e) in zip(["浪1", "浪2", "浪3", "浪4(至今)"], _bounds):
        _vol = round(float(df.iloc[_s:_e + 1]["volume"].mean()) / 10000) if _s <= _e else 0
        vol_by_wave.append({"wave": _n, "vol": _vol})

    # ---------- 波动率（20日年化，%）----------
    ret = np.log(df["close"] / df["close"].shift(1))
    hv20 = ret.rolling(20).std() * np.sqrt(244) * 100
    hv_now = round(float(hv20.iloc[-1]), 1)
    hv_pctile = round(float((hv20.dropna() < hv20.iloc[-1]).mean()) * 100)
    hv_series = [[d.strftime("%Y-%m-%d"), round(float(v), 1)]
                 for d, v in hv20.dropna().iloc[-500:].items()]
    volatility = {"now": hv_now, "pctile": hv_pctile, "series": hv_series}

    # 波动率 regime（R50 建议3）：HV20 五年分位 → 高波动区放大区间带宽、降低漂移信任。
    _vol_bucket = "高" if hv_pctile >= 66 else ("中" if hv_pctile >= 33 else "低")
    _vol_scale = 1.15 if _vol_bucket == "高" else (1.0 if _vol_bucket == "中" else 0.88)
    _drift_conf = 0.60 if _vol_bucket == "高" else (0.85 if _vol_bucket == "中" else 1.0)

    # ---------- 周线多周期共振 ----------
    # 剔除"当前不完整周"：resample("W-FRI") 会把未结束的本周归到一个以【未来周五】为标签的桶，
    # 其 .last() 只是最新交易日收盘而非真实周收盘。若末桶标签(周五)晚于最新交易日，则该桶为
    # 未结束的本周——用于"周线级别"判断与 30 周 MA 时须排除，取上一完整周，否则 findings 会把
    # 周内日收盘误标为"周收盘"、MA 混入不完整周导致轻微漂移（如 2026-08-04 周二：.last()=3822.28
    # 是日收盘，真实上周五收盘应为 3832.26）。
    wk = df["close"].resample("W-FRI").last().dropna()
    if wk.index[-1] > df.index[-1]:
        wk = wk.iloc[:-1]
    ma30w = wk.rolling(30).mean()
    weekly = {
        "close": r2(wk.iloc[-1]),
        "ma30w": r2(ma30w.iloc[-1]),
        "above": bool(wk.iloc[-1] > ma30w.iloc[-1]),
    }

    # ---------- 斐波那契时间窗 ----------
    fib_ns = [13, 21, 34, 55, 89, 144, 233, 377, 610]

    # 交易日→自然日换算系数：用本数据实际平均交易日间距（非硬编码 1.46），
    # 使未到窗口的斐波那契变盘日估算更贴合真实日历节奏。
    _cal_per_bday = (idx[-1] - idx[0]).days / max(1, len(idx) - 1)

    def time_zones(base_str):
        base = pd.Timestamp(base_str)
        bi = safe_idx(idx, base)  # 复用 safe_idx：基准日非交易日取其后最近，避免崩溃
        out = []
        for n in fib_ns:
            if bi + n < len(idx):
                out.append({"n": n, "date": idx[bi + n].strftime("%Y-%m-%d"), "passed": True})
            else:
                est = base + pd.Timedelta(days=int(round(n * _cal_per_bday)))
                out.append({"n": n, "date": "约" + est.strftime("%Y-%m-%d"), "passed": False})
        return out

    # 时间窗锚点从 wave_points 派生（浪起点=wp5 / 浪③顶=wp8），单一真值，避免与浪型标注日期双份真值脱节
    _tz_base_start, _tz_base_top = wave_points[5]["date"], wave_points[8]["date"]
    tz_wave_start = time_zones(_tz_base_start)
    tz_wave3_top = time_zones(_tz_base_top)

    # ---------- 多指数归一化（单指数缺失不拖垮整体）----------
    # 跨指数原始数据自修复(R62)：每日管线会清理 *_raw.md，使共振静默失效。
    # 此处在文件缺失时从 eastmoney 拉取补全，使共振每日自动可用；任何失败(网络/格式)吞掉
    # 并跳过该指数 -> breadth 回退为 0(当前 inert 行为)，绝不影响其余计算、不拖垮看板生成。
    def _valid_raw(path, min_rows=120):
        # 内容校验(R165)：拒绝 429 错误 JSON / 空壳残桩（如 43 字节 {"code":429...}）。
        # 仅当能解析出 ≥ min_rows 条有效 kline 行才视为可用，否则视为损坏、必须走缓存/网络
        # 回退，避免错误文件被永久复用污染共振（此前 沪深300 残桩被当有效数据→静默丢失→
        # breadth 只剩创业板指却给满 ±1 置信、过度自信）。
        try:
            return len(read_kline_md(path)) >= min_rows
        except Exception:
            return False

    def _try_eastmoney(secid, _p, _cache):
        """原 ③：eastmoney push2 拉取（中国本地主源）。成功写 raw+缓存返回 True。"""
        _url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
                "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56"
                "&klt=101&fqt=0&beg=20210805&end=20991231") % secid
        for _attempt in range(3):
            try:
                _req = urllib.request.Request(_url, headers=_EASTMONEY_HEADERS)
                _d = json.loads(urllib.request.urlopen(_req, timeout=25).read().decode("utf-8"))
                _kl = (_d.get("data") or {}).get("klines") or []
                if not _kl:
                    return False
                _lines = ["| date | open | high | low | last | volume |",
                          "| --- | --- | --- | --- | --- | --- |"]
                for _row in _kl:
                    _f = _row.split(",")
                    if len(_f) < 6:
                        continue
                    # 表头按列名映射：date/open/high/low/last(收盘)/volume
                    _lines.append("| %s | %s | %s | %s | %s | %s |"
                                  % (_f[0], _f[1], _f[3], _f[4], _f[2], _f[5]))
                os.makedirs(os.path.dirname(_cache), exist_ok=True)
                with open(_p, "w", encoding="utf-8") as _fp:
                    _fp.write("\n".join(_lines) + "\n")
                shutil.copyfile(_p, _cache)   # 同步缓存
                return True
            except Exception as _e:
                if _attempt < 2:
                    time.sleep(2.0)
                    continue
                print("  [warn] eastmoney 拉取 %s 失败(转回退源): %s" % (secid, _e))
                return False
        return False

    def _try_datafeed(key, _p, _cache):
        """R271 海外可达回退：yahoo -> stooq（datafeed 内部链式）。成功写 raw+缓存返回 True。"""
        try:
            if datafeed.fetch_and_write(key, _p):
                os.makedirs(os.path.dirname(_cache), exist_ok=True)
                shutil.copyfile(_p, _cache)
                return True
        except Exception as _e:
            print("  [warn] 回退源拉取 %s 失败: %s" % (key, _e))
        return False

    def _ensure_raw(name, fn, secid):
        # R271 取数优先级：① eastmoney(中国主源) -> ② yahoo/stooq(海外可达回退)
        # -> ③ 已提交/缓存旧 raw.md(可能滞后，但保证不崩溃)。
        # 此前 ① 直接用本地 committed raw 会短路，导致海外 runner 永远用滞后数据；
        # 改为网络优先，确保云端每日拉到当日新数据。本地 eastmoney 主源成功即同前行为。
        _p = os.path.join(BASE, "data", fn)
        _cache = os.path.join(BASE, "data", ".idx_cache", fn)
        _key = fn[:-8] if fn.endswith("_raw.md") else fn   # 文件名 stem 即 datafeed key
        if _try_eastmoney(secid, _p, _cache):
            return True
        if _try_datafeed(_key, _p, _cache):
            return True
        # 兜底：已提交/缓存的旧 raw.md（滞后但可用，绝不让管线崩溃）
        if os.path.exists(_p) and _valid_raw(_p):
            return True
        if os.path.exists(_cache) and _valid_raw(_cache):
            try:
                shutil.copyfile(_cache, _p)
                return True
            except Exception:
                pass
        print("  [warn] %s 全源取数失败，共振回退为 0" % name)
        return False

    def _common_base(*paths, min_date=wave_points[5]["date"]):
        """三指数共同拥有的、不早于 min_date 的最早日。
        保证归一化统一以同一交易日为 100 起点，避免各自 iloc[0] 取不同日
        导致广度对照静默错位（未来任一日数据窗口被不等裁剪时尤其危险）。"""
        sets = []
        for p in paths:
            try:
                rows = read_kline_md(p)
                sets.append(set(r["date"] for r in rows if r["date"] >= min_date))
            except Exception:
                sets.append(set())
        common = set.intersection(*sets) if sets else set()
        return min(common) if common else min_date

    def norm_series(path, base_str):
        try:
            d = parse_md(path)
            # 以 base_str 当天收盘为 100 锚点(优先精确日，缺失则取其后者最近交易日)，
            # 与 _common_base 配合 -> 三指数锚定同一交易日，广度对照不漂移
            _eq = d.index.strftime("%Y-%m-%d") == base_str
            if _eq.any():
                base_v = float(d.loc[_eq, "close"].iloc[0])
            else:
                d_ge = d[d.index >= base_str]
                if d_ge.empty or "close" not in d_ge.columns:
                    return []
                base_v = float(d_ge["close"].iloc[0])
            if not base_v or pd.isna(base_v):
                return []
            d = d[d.index >= base_str]
            return [[t.strftime("%Y-%m-%d"), round(float(v) / base_v * 100, 2)]
                    for t, v in zip(d.index, d["close"])]
        except Exception as e:
            print("  [warn] 归一化跳过 %s: %s" % (os.path.basename(path), e))
            return []

    _idx_raw = [
        ("上证指数", "sh000001_raw.md", "1.000001"),
        ("沪深300", "sh000300_raw.md", "1.000300"),
        ("创业板指", "sz399006_raw.md", "0.399006"),
        ("上证50", "sh000016_raw.md", "1.000016"),
        ("中证500", "sh000905_raw.md", "1.000905"),
        ("科创50", "sh000688_raw.md", "1.000688"),
    ]
    # 缺失即自拉取补全(R62)，使共振不受每日 *_raw.md 清理影响
    for _nm, _fn, _sid in _idx_raw:
        _ensure_raw(_nm, _fn, _sid)
    # 统一基准日：取三指数交集共有、不早于 2024-09-18 的最早日（当前=2024-09-18）
    _norm_base = _common_base(*(os.path.join(BASE, "data", fn) for _nm, fn, _sid in _idx_raw))
    index_compare = []
    for _nm, _fn, _sid in _idx_raw:
        _s = norm_series(os.path.join(BASE, "data", _fn), _norm_base)
        if _s:
            index_compare.append({"name": _nm, "data": _s})

    # 跨指数共振（R50 建议5）：用「除上证外的宽基」(沪深300+创业板指+上证50+中证500+科创50)近20交易日方向一致性
    # → 广度 _breadth∈[-1,1]，作为对上证趋势的【纯交叉验证】(不含上证自身方向，避免自指确认)。
    # 全部同向(确认)=±1 增强触达概率；背离≈0 削弱。与概率互补(概率管"到不到"，共振管"势是否齐")。
    # 关键修复(R77)：旧实现把上证自身方向也计入广度 → 自指确认；更糟的是他指数据缺失时广度退化为
    # 上证单边±1、仍给先验±5 bump，伪装成"共振"。现广度只取沪深300+创业板指：他指缺失→_bs 空→
    # breadth 回退 0，不再以自身方向伪装共振；背离时广度=±1(非旧式的弱±0.33)，确认/背离更判别、更诚实。
    _breadth = 0.0
    _breadth_mag = 0.0
    _res_details = []
    for _ic in index_compare:
        _s = _ic.get("data") or []
        if len(_s) >= 21:
            _r20 = _s[-1][1] / _s[-21][1] - 1.0          # 近20交易日收益(归一化序列, 锚定=_norm_base)
            _res_details.append({"name": _ic["name"], "ret20": round(_r20 * 100, 2)})
    # 广度设计集：用于趋势确认的宽基(不含上证，避免自指确认)；_res_details 仍含上证供背离对照展示。
    _breadth_idx = {"沪深300", "创业板指", "上证50", "中证500", "科创50"}
    _bs = [d for d in _res_details if d["name"] in _breadth_idx]
    _breadth_total = len(_breadth_idx)
    _breadth_avail = len(_bs)
    _breadth_missing = sorted(_breadth_idx - set(d["name"] for d in _bs))
    if _bs:
        # 符号广度按「设计集总数」归一(R165)：仅 k/N 个可用时按比例缩放，避免单指数在场就给满 ±1
        # 置信(此前 沪深300 静默丢失→只剩创业板指→breadth 直接 ±1 过度自信)。正常双指数在场仍为 ±1。
        _sign = sum(1 if d["ret20"] > 0 else (-1 if d["ret20"] < 0 else 0) for d in _bs)
        _breadth = _sign / _breadth_total
        # 幅度感知广度(仅展示用)：20日收益按 ±8% 截断到 [-1,1] 取均值，反映确认强度(非仅方向)
        _breadth_mag = sum(max(-1.0, min(1.0, d["ret20"] / 8.0)) for d in _bs) / _breadth_total
    resonance = {
        "breadth": round(_breadth, 3),
        "breadthMag": round(_breadth_mag, 3),
        "breadthTotal": _breadth_total,
        "breadthAvailable": _breadth_avail,
        "breadthMissing": _breadth_missing,
        "details": _res_details,
        "note": "宽基共振(沪深300/创业板指/上证50/中证500/科创50，不含上证自身)近20日方向系数 %.2f（±1=同向确认，符号表方向：+1上行/-1下行；0=背离）；确认宽基可用 %d/%d，缺失 %s"
                 % (_breadth, _breadth_avail, _breadth_total, (_breadth_missing or "无")),
    }
    # 风格/市值轮动（R168，纯交叉验证，不纳入概率先验）：5 只宽基天然代理不同风格，
    # 提取 价值↔成长、大盘↔中盘、硬科技 强弱对照，提示资金在往哪类风格挤——比单纯方向广度更可行动。
    # 与共振同属 equity 风险情绪、强相关，OOS 多半无增益(R166/R167 经验)，故保持可见化定位。
    _ret20_by = {d["name"]: d["ret20"] for d in _res_details}

    def _g(nm):
        return _ret20_by.get(nm)

    _style_groups = [
        ("大盘价值", "上证50"), ("大盘", "沪深300"), ("中盘", "中证500"),
        ("成长", "创业板指"), ("硬科技", "科创50"),
    ]
    _style_rows = []
    for _gl, _nm in _style_groups:
        _v = _g(_nm)
        _style_rows.append({"group": _gl, "name": _nm, "ret20": _v})

    def _rot(v, a, b):
        if v is None:
            return "（%s/%s 数据不足，暂不可比）" % (a, b)
        if v > 1.0:
            return "（%s 显著强于 %s，资金偏%s）" % (a, b, a)
        if v > 0.2:
            return "（%s 略强于 %s）" % (a, b)
        if v < -1.0:
            return "（%s 显著强于 %s，资金偏%s）" % (b, a, b)
        if v < -0.2:
            return "（%s 略强于 %s）" % (b, a)
        return "（%s/%s 均衡）" % (a, b)

    _vg, _gr = _g("上证50"), _g("创业板指")
    _lm, _md = _g("沪深300"), _g("中证500")
    _tech = _g("科创50")
    _vvg = round(_vg - _gr, 2) if (_vg is not None and _gr is not None) else None
    _lvm = round(_lm - _md, 2) if (_lm is not None and _md is not None) else None
    resonance["style"] = {
        "groups": _style_rows,
        "valueVsGrowth": _vvg,
        "largeVsMid": _lvm,
        "techRet20": _tech,
        "valueVsGrowthTxt": _rot(_vvg, "价值(上证50)", "成长(创业板指)"),
        "largeVsMidTxt": _rot(_lvm, "大盘(沪深300)", "中盘(中证500)"),
        "note": "风格轮动由 5 只宽基天然代理：上证50=大盘价值、沪深300=大盘、中证500=中盘、创业板指=成长、科创50=硬科技；价值-成长差与大盘-中盘差为正→对应风格占优。仅作趋势交叉验证参考，不纳入概率先验。",
    }

    # ---------- 跨市场联动（R167）：港股+美股 equity 对 A股趋势的纯交叉验证 ----------
    # 数据源现状：westock(Tencent) 可对 恒生/恒生科技/标普500/纳斯达克 取数；商品/汇率/美债 westock 与
    # eastmoney 当前均不可拉(返回"数据为空"/RemoteDisconnected)，故先聚焦股(中美港)。美股数据为上一
    # 交易日(时差)，港股/上证为当日——20日收益趋势对比对此不敏感，仍可作全球风险情绪交叉验证。
    _xmk_raw = [
        ("恒生指数", "hkHSI_raw.md", "hkHSI"),
        ("恒生科技", "hkHSTECH_raw.md", "hkHSTECH"),
        ("标普500", "usINX_raw.md", "usINX"),
        ("纳斯达克", "usIXIC_raw.md", "usIXIC"),
    ]
    for _nm, _fn, _sid in _xmk_raw:
        _ensure_raw(_nm, _fn, _sid)
    # 独立基准日：取 4 只外部 equity 交集共有、不早于 2024-09-18 的最早日
    _xmk_base = _common_base(*(os.path.join(BASE, "data", fn) for _nm, fn, _sid in _xmk_raw))
    _xmk_series = []
    for _nm, _fn, _sid in _xmk_raw:
        _s = norm_series(os.path.join(BASE, "data", _fn), _xmk_base)
        if _s:
            _xmk_series.append({"name": _nm, "data": _s})
    _xmk_details = []
    for _ic in _xmk_series:
        _s = _ic.get("data") or []
        if len(_s) >= 21:
            _r20 = _s[-1][1] / _s[-21][1] - 1.0           # 近20交易日收益(归一化序列, 锚定=_xmk_base)
            _xmk_details.append({"name": _ic["name"], "ret20": round(_r20 * 100, 2)})
    # 设计集：4 只外部 equity（不含上证自身，纯跨市场交叉验证）
    _xmk_idx = {"恒生指数", "恒生科技", "标普500", "纳斯达克"}
    _xbs = [d for d in _xmk_details if d["name"] in _xmk_idx]
    _xmk_total = len(_xmk_idx)
    _xmk_avail = len(_xbs)
    _xmk_missing = sorted(_xmk_idx - set(d["name"] for d in _xbs))
    _xbreadth = 0.0
    _xbreadth_mag = 0.0
    if _xbs:
        # 符号广度按「设计集总数」归一（同 resonance R165）：仅 k/N 可用时按比例缩放，避免单市场满 ±1 过度自信
        _sign = sum(1 if d["ret20"] > 0 else (-1 if d["ret20"] < 0 else 0) for d in _xbs)
        _xbreadth = _sign / _xmk_total
        # 幅度感知广度(仅展示用)：20日收益按 ±8% 截断到 [-1,1] 取均值
        _xbreadth_mag = sum(max(-1.0, min(1.0, d["ret20"] / 8.0)) for d in _xbs) / _xmk_total
    # 上证自身 ret20 供背离对照（从 index_compare 取，与共振面板一致）
    _sh_r20 = None
    for _ic in index_compare:
        if _ic["name"] == "上证指数":
            _ss = _ic.get("data") or []
            if len(_ss) >= 21:
                _sh_r20 = round((_ss[-1][1] / _ss[-21][1] - 1.0) * 100, 2)
            break
    crossMarket = {
        "breadth": round(_xbreadth, 3),
        "breadthMag": round(_xbreadth_mag, 3),
        "breadthTotal": _xmk_total,
        "breadthAvailable": _xmk_avail,
        "breadthMissing": _xmk_missing,
        "shRet20": _sh_r20,
        "details": _xmk_details,
        "note": "跨市场(港股:恒生/恒生科技 + 美股:标普500/纳斯达克)近20日方向系数 %.2f（±1=全球 equity 同向确认，符号表方向：+1上行/-1下行；0=背离）；确认市场可用 %d/%d，缺失 %s。上证自身近20日 %.2f%% 供背离对照（美股为上一交易日，时差所致）。"
                 % (_xbreadth, _xmk_avail, _xmk_total, (_xmk_missing or "无"), (_sh_r20 if _sh_r20 is not None else 0)),
    }

    # ---------- 关键位距离（全部从框架派生，避免与 trade_plan/浪型常量双份真值漂移）----------
    # 铁律线/卖点取 trade_plan（权威），浪③顶/浪④低取浪型常量 w3_hi/w4_low。
    # 浪型一旦重校订，distances 自动跟随，不会与徽章(state)、买卖框架脱节。
    _sl_p = trade_plan["stopLine"]["price"]                 # 3674.40 铁律线
    _sell = [t["price"] for t in trade_plan["sellTargets"]]  # [卖① 4493.94, 卖② 4725.81, 卖③ 5334.x]，均自 w4_low 派生
    distances = {
        "toKeyLine": round((last_close - _sl_p) / last_close * 100, 2),
        "toTargetLow": round((_sell[0] - last_close) / last_close * 100, 2),
        "toTargetHigh": round((_sell[-1] - last_close) / last_close * 100, 2),
        "toPrevHigh": round((w3_hi - last_close) / last_close * 100, 2),
        "fromW4Low": round((last_close - w4_low) / w4_low * 100, 2),
    }

    # ---------- 情景推演（人工校订；浪⑤目标端点与铁律线由框架派生，消除与 sellTargets/KEY_LINE 的四舍五入漂移）----------
    _s0, _s1, _s2 = [t["price"] for t in sell_targets]   # 卖①②③ = 浪⑤三口径目标
    # 风险情景下行路径的拐点 = 精确斐波那契支撑，由框架派生（消除硬编码 3506/3447/3255 双份真值）：
    # 3506 = 浪3回撤 61.8% (supports[3])，3447 = 5年回撤 50% (supports[4])，3255 = 5年回撤 61.8%。
    # R30 曾以"非计算斐波那契支撑"为由保留，但实证 3506==supports[3]、3447==supports[4]、
    # 3255==5年回撤61.8%（均为派生值的四舍五入），确为派生支撑——硬编码会在浪型重校订时
    # 与支撑表脱节，故改由框架派生，与 supports 单一真值联动。
    _risk_r61 = next(s["price"] for s in supports if s["name"] == "浪3回撤 61.8%")
    _risk_r50y = next(s["price"] for s in supports if s["name"] == "5年区间回撤 50%")
    _risk_r618y = r2(hi5 - (hi5 - lo5) * 0.618)
    scenarios = [
        # 浪4完成区间下界=浪③50%回撤支撑(supports[2])、上界=浪④低(w4_low)，
        # 全部由框架派生，消除"3650-3740"硬编码字面（浪型重校订时自动跟随，不会脱节）
        {"name": "基准: 浪4于%.0f-%.0f完成, 浪5看%.0f/%.0f" % (r2(w3_hi - w3 * 0.5), w4_low, _s0, _s1), "color": "#c23531",
         "points": [[last_date, last_close], ["2026-09-30", 3700], ["2026-11-30", 3680],
                    ["2027-01-29", 3950], ["2027-03-31", 4200], ["2027-05-31", _s0], ["2027-08-31", _s1]]},
        {"name": "强势: %.0f已是浪4底, 直接启动浪5" % w4_low, "color": "#e6a23c",
         "points": [[last_date, last_close], ["2026-09-30", 3980], ["2026-11-30", 4258],
                    ["2027-02-26", _s0], ["2027-05-31", _s2], ["2027-08-31", 4700]]},
        {"name": "风险: 跌破%.0f铁律线, 浪型证伪转深调" % KEY_LINE, "color": "#2f9e44",
         # 风险首点=铁律线 KEY_LINE 派生(原硬编码 3670 与 KEY_LINE 脱节 4.4 点，违反单源真值纪律 R231)；
         # 末点 3400 为人工叙事深调目标，不参与数值契约。
         "points": [[last_date, last_close], ["2026-09-30", r2(KEY_LINE)], ["2026-11-30", _risk_r61],
                    ["2027-01-29", _risk_r50y], ["2027-04-30", _risk_r618y], ["2027-08-31", 3400]]},
    ]

    # ---------- 真实触达时间估计（驱动区间±σ 的时间锚定）----------
    # 旧 _enrich 用 15+dist*120 假经验式，与情景时间窗脱节 8~10 倍→区间过窄、概率失真。
    # 曾改为从"基准情景到达该价位之日"推算，但 scenarios[0] 仅 7 个稀疏点且不含卖③→
    # 卖③ 映射到卖②日期、expDays 失真(R59 修复)。现由「本指数 zigzag 历史摆动腿」幅度-时长
    # 关系独立派生(_horizon_for)：同向达到目标幅度的腿用时长中位数，样本不足则日收益外推；
    # 子浪终点同步用 _horizon_for(_sf0) 经 idx 交易日推，与卖①同源、不依赖情景图。
    def _trading_days_between(d0, d1):
        d0, d1 = pd.Timestamp(d0), pd.Timestamp(d1)
        if d1 <= d0:
            return 10          # 锚点落在过去(如浪⑤起)：兜底小正值，避免观察窗塌成 0
        # 返回真实交易日差(不封底)：子浪近未来点(date 仅数日后)的 expDays 须与 _sf_date
        # 真实比例一致，否则"触达日期"与"触达交易日数"自相矛盾(R60 修复)。_enrich 内部仍用
        # max(10,...) 下限防带宽塌陷，expDays 字段仅作展示/回测归档，不影响概率计算。
        return len(pd.bdate_range(d0, d1)) - 1

    def _horizon_for(price):
        # 【R59 修复】卖点触达时间改为「本指数 zigzag 历史摆动腿」幅度-时长关系独立派生，
        # 彻底解耦基准情景图：scenarios[0] 仅 7 个稀疏点且不含卖③，旧式「价格最近点」匹配使
        # 卖③ 落到卖②日期(2027-08-31) → expDays 与卖②相同、违背"越远越晚"并污染带宽/首达概率。
        # 现取同向、幅度≥目标幅度的完成浪，用时长中位数(稳健、抗极端腿)作为触达交易日估计；
        # 样本不足(冷启动/极远目标)则按同向腿平均日对数收益外推 exp=目标幅度/平均速率。
        # 返回相对今日(last_date)的交易日数；与子浪 _sf_exp 同源口径，不破坏 audit49 不变量。
        # 注：此处不引用 daily_vol(在 _horizon_for 首次调用点之后才定义)，
        # 用历史腿自身节奏截顶(≤330)；bandPct≤23.5% 的精确截顶由 _enrich 的
        # `min(_band, price*0.235)` 独立保证(R74)，与概率 horizon 解耦。
        _a = math.log(price / last_close)
        _dir = 1 if _a >= 0 else -1
        _at = abs(_a)
        # R65 修复：用「达到目标幅度所需时间」而非整条腿时长。历史腿幅度常远大于目标
        # (如 +25% 腿 vs +15% 目标)，目标其实在腿内更早到达；整条腿时长会系统性高估触达时间
        # → 带宽过宽、概率失真。摆动几何自相似假设下 ttr=时长×(目标幅度/腿幅度)，取中位数更诚实。
        _ttr = sorted(ld * (_at / abs(lr)) for lr, ld, *_ in _hist_legs
                      if _dir * lr > 0 and abs(lr) >= _at and abs(lr) > 0)
        if len(_ttr) >= 4:
            _exp = _ttr[len(_ttr) // 2]
        else:
            # 外推：取幅度最接近目标的同向腿，按时长-幅度比例外推(摆动几何自相似假设)，
            # 比全局平均日收益更稳——大摆动日均收益摊薄，全局 rate 会低估远目标时长(卖③反而比卖①②近)。
            _near = sorted([(abs(lr), ld) for lr, ld, *_ in _hist_legs if _dir * lr > 0],
                           key=lambda x: abs(x[0] - _at))[:5]
            if _near:
                _exps = [ld * (_at / abs(lr)) for lr, ld in _near if abs(lr) > 0]
                _exp = sum(_exps) / len(_exps)
            else:
                _exp = 330
        return max(10, min(330, int(round(_exp))))

    # ---------- 浪⑤子浪走势预测（斐波那契细分推演；价位为推导值、时间仅比例示意）----------
    # 前提：浪④于 w4_low 完成、浪⑤自该点启动（浪④低尚未确认，属条件推演）。
    # 细分比例：经验校准(同浪级5浪上行结构) + 波动率regime感知回撤，端点锁定子浪ⅴ≡卖①。
    # R101：旧式写死 0.4/0.5/1.618/0.382 从未用本指数数据校准；现从 zigzag 同浪级结构量取真实
    # 子浪比率中位数(带防坑 sanity clamp，剔除原始 zigzag 直接取会出的 >1 荒谬回撤)，
    # 仅当经验模型基线 MAE 低于斐波那契标准时才启用(R85「确实更优才部署」纪律)。
    _sf0 = sell_targets[0]["price"]                          # 卖① = 浪⑤首目标(保守口径)
    _sf_A = _sf0 - w4_low                                    # 浪⑤幅度(卖①口径)
    # —— 经验比率测量（P0 基线 + P1 校准共用同一结构识别）——
    _sw_runs = _detect_five_wave_runs(st["zigzag"])
    _sw_emp = _measure_subwave_ratios(st["zigzag"])
    _have_valid = _sw_emp is not None   # 是否存在「有效」同浪级 5 浪推动浪结构(≥6 且过 sanity clamp)
    _sw_base_err = _subwave_baseline_error(st["zigzag"], None)            # 斐波那契标准 MAE
    _sw_emp_err = _subwave_baseline_error(st["zigzag"], _sw_emp) if _sw_emp else None
    # 选比率：经验更准才用，否则回退标准（R85 纪律）
    if _sw_emp and _sw_emp_err and _sw_base_err and _sw_emp_err["mae_price_frac"] < _sw_base_err["mae_price_frac"]:
        R_II_RET, R_III, R_IV_RET, _ratio_src = _sw_emp["ii_ret"], _sw_emp["iii"], _sw_emp["iv_ret"], "经验校准"
    else:
        R_II_RET, R_III, R_IV_RET, _ratio_src = 0.5, 1.618, 0.2917, "斐波那契标准"
    # —— 波动率 regime 感知回撤深度（P1）：高波动→回撤更深 ——
    _hv_now = float(ret.rolling(20).std().iloc[-1])
    _hv_med = ret.rolling(20).std().median()
    _hv_med = float(_hv_med) if not math.isnan(_hv_med) else _hv_now
    _vol_ratio = (_hv_now / _hv_med) if _hv_med > 0 else 1.0
    _rvm = max(0.7, min(1.3, _vol_ratio))                    # 回撤缩放夹紧，避免极端波动失真
    R_II_RET = max(0.236, min(0.618, R_II_RET * _rvm))
    R_IV_RET = max(0.236, min(0.618, R_IV_RET * _rvm))
    # —— 浪⑤截断检测（P2）：历史 5 浪中 子浪ⅴ幅度<子浪ⅲ幅度 → 截断占比 ——
    # R106：截断收缩仅当有「有效」同浪级结构时才有意义；退化 zigzag 组(子浪ⅱ>子浪ⅰ起点等)
    # 不应驱动比率失真。无有效结构→纯斐波那契(不收缩)，与 R85「无经验证据不扭曲基线」一致。
    _trunc_n = sum(1 for _r in _sw_runs if (_r["p"][5] - _r["p"][4]) < (_r["p"][3] - _r["p"][2]))
    _trunc_prob = (_trunc_n / len(_sw_runs)) if (_sw_runs and _have_valid) else 0.0
    R_III_eff = R_III * (1.0 - 0.15 * _trunc_prob)          # 截断常见→子浪ⅲ略缩
    # —— 端点锁定：解出子浪ⅰ幅度使 子浪ⅴ≡卖①（审计不变量，validate/audit49/51 强检验）——
    _R_AMP = 2 - R_II_RET + R_III_eff - R_IV_RET * R_III_eff
    _R_I = 1.0 / _R_AMP                                      # 子浪ⅰ 占浪⑤幅度比例
    _RI_PCT = round(_R_I * 100, 1)
    _sf_i   = r2(w4_low + _R_I * _sf_A)                      # 子浪ⅰ顶
    _sf_ii  = r2(w4_low + _R_I * (1 - R_II_RET) * _sf_A)     # 子浪ⅱ底
    _sf_iii = r2(w4_low + _R_I * (1 - R_II_RET + R_III_eff) * _sf_A)        # 子浪ⅲ顶
    _sf_iv  = r2(w4_low + _R_I * (1 - R_II_RET + R_III_eff - R_IV_RET * R_III_eff) * _sf_A)  # 子浪ⅳ底
    _sf_v   = r2(w4_low + _R_I * _R_AMP * _sf_A)             # 子浪ⅴ顶 ≡ 卖①(= w4_low+_sf_A)
    # —— P2 子浪触达带宽度：按历史离散度校准（覆盖~68%回测误差）——
    _sw_disp = ((_sw_emp_err or _sw_base_err) or {}).get("disp_pct", 8.0)
    _SUB_BAND_PCT = max(2.0, min(20.0, _sw_disp))
    # —— P2 子浪ⅴ 独立交叉校验（与卖①偏差过大则预警）——
    # R106：独立V交叉校验同样仅当有有效结构才有意义；退化组给出 1.701 等失真中位数会误导
    # 「浪⑤末端偏保守/偏乐观」结论。无有效结构→校验置不可用(deviation=0, 诚实提示)。
    _rv_list = [(_r["p"][5] - _r["p"][4]) / (_r["p"][1] - _r["p"][0])
                for _r in _sw_runs if (_r["p"][1] - _r["p"][0]) > 0]
    if _rv_list and _have_valid:
        _rv_med = statistics.median(_rv_list)
        _sf_v_indep = r2(w4_low + _rv_med * _sf_A)
        _sf_v_dev = (_sf0 - _sf_v_indep) / _sf0 * 100
    else:
        _sf_v_indep = r2(_sf0)
        _sf_v_dev = 0.0
    # 时间端点：起点=浪④低日；终点=卖①相对今日的触达日(与卖点同源，不依赖情景图稀疏点)。
    # 【R59 修复】旧式从 scenarios[0] 反查卖①日期(稀疏点硬匹配)；现用 _horizon_for(_sf0) 推
    # 卖①交易日数、经「未来 bdate_range」精确外推日期 → 子浪ⅴ终点==卖①终点，audit49 不变量精确成立。
    # 注意：idx 只到 last_date(末交易日)，_bi 已是末行，直接 idx[_bi+N] 必越界截回 last_date→
    # 子浪终点退化到今日、expDays 失真；故改用未来 bdate_range 真实外推未来日期。
    _sf_sd = wave_points[-1]["date"]
    _h_days = int(round(_horizon_for(_sf0)))
    _future_end = pd.Timestamp(last_date) + pd.Timedelta(days=int(round(_h_days * _cal_per_bday)) + 60)
    _future = pd.bdate_range(pd.Timestamp(last_date), _future_end)
    _sf_ed = _future[min(len(_future) - 1, _h_days)].strftime("%Y-%m-%d")
    _sf_ed_ts = pd.Timestamp(_sf_ed)
    # R66 修复：子浪时间轴锚点分离——k=0(浪⑤起)锚浪④低(真实历史拐点，投影参照原点)，
    # k≥1(子浪ⅰ~ⅴ)锚「今日」，使子浪触达日期落在未来、与 expDays(相对今日)同源一致；
    # 旧式统一锚浪④低会把早期子浪(子浪ⅰ)按历史时间占比放到过去(如 2026-08-04)，
    # 与 _trading_days_between(今日, 昨日) 封底 10 产生的"假 expDays"矛盾(价 4042 未触及却画在过去)。
    _sf_t0 = pd.Timestamp(_sf_sd)            # 浪⑤起 真实历史拐点
    _sf_t1 = pd.Timestamp(last_date)         # 子浪投影锚点=今日(确保≥今日、与 expDays 一致)
    # 子浪时间分配：历史校准（R50 建议4）。旧 _sf_travels 用价格幅度 _sf_x 比例(启发式示意)，
    # 现改为从本指数 zigzag 连续 5 腿摆动求「真实时间占比」分布，与经典 5 浪时间先验 50/50 混合，
    # 使子浪各点 expDays 由历史摆动节奏驱动(非价格幅度启发式)。端点锁定 _sf_sd~_sf_ed：
    # _sf_cum[0]=0(浪⑤起)、_sf_cum[5]=1(子浪ⅴ=卖①)，中间单调；_sf_exp 仍相对 last_date(守 R48)。
    _sf_prior = [0.18, 0.14, 0.36, 0.14, 0.18]   # 经典 5 浪时间先验(浪ⅲ最长)
    _sf_prior_n = [x / sum(_sf_prior) for x in _sf_prior]
    # 子浪时间校准只取「上行推动浪」节奏：浪⑤为上行浪，其细分(子浪ⅰ/ⅲ/ⅴ上行、ⅱ/ⅳ下行)
    # 必须对齐历史上行推动浪，而非任意方向腿。旧式步长2仅保持奇偶同相、并不保证首腿上行——
    # 若历史首腿恰好为下行，会错误套用「下行5腿」时间节奏；现显式筛 浪ⅰ/ⅲ/ⅴ上行 且 浪ⅱ/ⅳ下行
    # 的连续5腿，与浪⑤方向严格一致（符号由 _hist_legs 的带符号对数收益判定）。
    _runs = []
    for _s in range(0, len(_hist_legs) - 4):
        _wl = _hist_legs[_s:_s + 5]               # 连续5腿（含带符号 logret）
        if not (_wl[0][0] > 0 and _wl[1][0] < 0 and _wl[2][0] > 0
                and _wl[3][0] < 0 and _wl[4][0] > 0):
            continue                              # 仅保留上行推动浪形态
        _tot = sum(ld for _lr, ld, *_ in _wl)
        if _tot > 0:
            _runs.append([ld / _tot for _lr, ld, *_ in _wl])
    if _runs:
        _sf_emp = [sum(r[i] for r in _runs) / len(_runs) for i in range(5)]
    else:
        _sf_emp = [0.20, 0.15, 0.30, 0.15, 0.20]
    # P3 时间校准：经验权重仅在「经验时间占比来源(_runs 上行5腿摆动)样本充足」且 MAE 更低时启用(R85 纪律)。
    # R107：_date_mae 旧式比对「退化」的 _sw_runs(8% zigzag 同浪级5浪检测，本指数 0 有效)→ 永远 None →
    # 经验权重被静默强锁 0.5(歪打正着，但决策源错误)。现改为比对正确的丰富来源 _runs(指数自身上行5腿摆动，
    # 每条即真实腿时间占比)，并设最小样本门槛(≥12)防 n=5 过拟合：样本不足时经验占比不可信→维持 50/50
    # (经典先验为主)，不盲偏。忠实 MAE 扫参已证 N=5 下经验占比并不显著优于经典先验(各权重 MAE 0.146~0.152 持平)。
    _MIN_RUNS = 12
    def _date_mae(blend):
        _c = [0.0]
        for f in blend:
            _c.append(_c[-1] + f)
        if len(_runs) < _MIN_RUNS:        # R107：经验时间来源样本不足→不启用经验权重(回退 0.5)，避免小样本过拟合
            return None
        _e = []
        for _leg in _runs:                # _leg = 该历史摆动各腿真实时间占比(累计即真实枢点占比)
            _ac = [0.0]
            for f in _leg:
                _ac.append(_ac[-1] + f)
            for _k in range(1, 5):
                _e.append(abs(_ac[_k] - _c[_k]))
        return statistics.median(_e) if _e else None
    _mae_05 = _date_mae([0.5 * e + 0.5 * p for e, p in zip(_sf_emp, _sf_prior_n)])
    _mae_07 = _date_mae([0.7 * e + 0.3 * p for e, p in zip(_sf_emp, _sf_prior_n)])
    _W_EMP = 0.7 if (_mae_07 is not None and _mae_05 is not None and _mae_07 <= _mae_05) else 0.5
    _sf_time = [_W_EMP * e + (1 - _W_EMP) * p for e, p in zip(_sf_emp, _sf_prior_n)]
    _sf_cum = [0.0]
    for _f in _sf_time:
        _sf_cum.append(_sf_cum[-1] + _f)         # 累计和=1（端点锁定）

    # ---- R207：A股交易日历（2026 官方休市，上交所 上证公告〔2025〕45号）----
    # 仅列连续休市区间（周末本就休市，不重复）；2027 安排待公布后补充。
    _A_SHARE_HOLIDAYS_2026 = set()
    for _hs, _he in [
        ("2026-01-01", "2026-01-03"), ("2026-02-15", "2026-02-23"),
        ("2026-04-04", "2026-04-06"), ("2026-05-01", "2026-05-05"),
        ("2026-06-19", "2026-06-21"), ("2026-09-25", "2026-09-27"),
        ("2026-10-01", "2026-10-07"),
    ]:
        _hd = pd.Timestamp(_hs)
        while _hd <= pd.Timestamp(_he):
            _A_SHARE_HOLIDAYS_2026.add(_hd.strftime("%Y-%m-%d"))
            _hd += pd.Timedelta(days=1)

    def _next_trading_day(ts):
        """返回 ts 或其之后最近的 A股交易日（工作日且非休市日）。"""
        _d = pd.Timestamp(ts)
        while True:
            if _d.dayofweek < 5 and _d.strftime("%Y-%m-%d") not in _A_SHARE_HOLIDAYS_2026:
                return _d
            _d += pd.Timedelta(days=1)

    def _sf_date_raw(k):
        # k=0 用真实历史拐点(浪④低)；k≥1 用今日锚点(未来一致)。span 随锚点自适应。
        # R207 修复：旧式自然日插值会落在周末/假期（实测子浪ⅲ撞中秋 2026-09-25、子浪ⅳ撞国庆 2026-10-07），
        # 现对插值结果前向吸附到最近 A股交易日，预测图不再显示非交易日。
        _base = _sf_t0 if k == 0 else _sf_t1
        _span = max((_sf_ed_ts - _base).days, 1)
        return _next_trading_day(_base + pd.Timedelta(days=round(_span * _sf_cum[k])))

    # R207：子浪各点日期统一吸附并确保单调递增（避免相邻点落入同一休市区间被吸附到同日后日期倒挂）
    _sf_dates = []
    _sf_prev = None
    for _k in range(6):
        _d = _sf_date_raw(_k)
        if _sf_prev is not None and _d <= _sf_prev:
            _d = _next_trading_day(_sf_prev + pd.Timedelta(days=1))
        _sf_dates.append(_d.strftime("%Y-%m-%d"))
        _sf_prev = pd.Timestamp(_sf_dates[-1])

    def _sf_date(k):
        """子浪点 k 的最终日期（已吸附交易日 + 单调递增）；等价于 _sf_dates[k]，供审计49字面校验。"""
        return _sf_dates[k]

    sub_forecast = {
        "assume": "条件推演：浪④于 %.2f 完成、浪⑤自该点启动（浪④低尚未右侧确认，跌破铁律线 %.2f 则本推演证伪）" % (w4_low, KEY_LINE),
        "ampNote": "浪⑤幅度取卖①保守口径 %.2f（0.618×浪③）；子浪比率【%s】、波动率regime回撤缩放 %.2f×、浪⑤截断收缩 %s" % (
            _sf_A, _ratio_src, _rvm,
            ("%.0f%%" % (_trunc_prob * 100)) if _have_valid else "未启用(无有效同浪级结构)"),
        # side/tag 为买卖点标记唯一真值：bottom pivot(浪⑤起/ⅱ/ⅳ)=买，ⅲ/ⅴ顶=卖，ⅰ顶=持有；
        # 前端 chartSubF 标记与 subFTable 操作列均由其派生，浪型重校订时自动跟随
        "points": [
            {"date": _sf_date(0), "price": w4_low,  "label": "浪⑤起", "pos": "bottom", "side": "buy",  "tag": "买①"},
            {"date": _sf_date(1), "price": _sf_i,   "label": "子浪ⅰ", "pos": "top",    "side": "hold", "tag": ""},
            {"date": _sf_date(2), "price": _sf_ii,  "label": "子浪ⅱ", "pos": "bottom", "side": "buy",  "tag": "买②"},
            {"date": _sf_date(3), "price": _sf_iii, "label": "子浪ⅲ", "pos": "top",    "side": "sell", "tag": "减仓"},
            {"date": _sf_date(4), "price": _sf_iv,  "label": "子浪ⅳ", "pos": "bottom", "side": "buy",  "tag": "买③"},
            {"date": _sf_date(5), "price": _sf_v,   "label": "子浪ⅴ", "pos": "top",    "side": "sell", "tag": "卖①"},
        ],
        "rows": [
            {"wave": "子浪ⅰ", "target": _sf_i,   "basis": "≈%.1f%%×浪⑤幅度，首段上攻（%s）" % (_RI_PCT, _ratio_src), "action": "持有（首段开启）", "side": "hold"},
            {"wave": "子浪ⅱ", "target": _sf_ii,  "basis": "回撤子浪ⅰ约 %.1f%%，回踩蓄势（regime %.2f×）" % (R_II_RET * 100, _rvm), "action": "买② 回踩加仓", "side": "buy"},
            {"wave": "子浪ⅲ", "target": _sf_iii, "basis": "≈%.2f×子浪ⅰ，主升浪段" % R_III_eff, "action": "减仓 主升兑现", "side": "sell"},
            {"wave": "子浪ⅳ", "target": _sf_iv,  "basis": "回撤子浪ⅲ约 %.1f%%，末升前整理" % (R_IV_RET * 100), "action": "买③ 末升前加仓", "side": "buy"},
            {"wave": "子浪ⅴ", "target": _sf_v,   "basis": "=子浪ⅰ等幅，顶=卖① %.2f（独立校验偏差 %.1f%%）" % (_sf0, _sf_v_dev), "action": "卖① 主兑现", "side": "sell"},
        ],
        # R101 校准溯源（P0/P1/P2/P3 全链路）：经验样本、基线 MAE、带宽、时间权重、截断概率，供审计/展示核对。
        "calib": {
            "ratioSrc": _ratio_src,
            "iiRet": round(R_II_RET, 3), "iii": round(R_III_eff, 3), "ivRet": round(R_IV_RET, 3),
            "volRatio": round(_vol_ratio, 3), "regimeMult": round(_rvm, 3),
            "empSamples": (_sw_emp["n"] if _sw_emp else 0),
            "baselineMaeFib": (_sw_base_err["mae_price_frac"] if _sw_base_err else None),
            "baselineMaeEmp": (_sw_emp_err["mae_price_frac"] if _sw_emp_err else None),
            "bandPct": _SUB_BAND_PCT,            # 历史离散度参考值(R105 起显示带统一用 vol horizon 匹配，见 bandModel)
            "bandModel": "波动率匹配(含horizon)，与概率带一致(R89/R105)",
            "timeWeightEmp": _W_EMP,
            "truncProb": round(_trunc_prob, 3),
            "truncProbAvailable": _have_valid,
        },
        "crossCheck": {
            "independentV": _sf_v_indep,
            "deviationPct": round(_sf_v_dev, 2),
            "warn": (_sf_v_dev > 5.0) if _have_valid else False,
            "available": _have_valid,
            "note": ("卖①高于独立 5 浪幅度估计 %.2f 约 %.1f%%，浪⑤末端可能偏乐观，重点观察顶背离/缩量终结信号" % (_sf_v_indep, _sf_v_dev)) if (_have_valid and _sf_v_dev > 5.0)
                    else ("卖①低于独立 5 浪幅度估计 %.2f 约 %.1f%%，浪⑤末端偏保守（安全边际较足）" % (_sf_v_indep, -_sf_v_dev)) if (_have_valid and _sf_v_dev < -5.0)
                    else ("无有效同浪级 5 浪结构可交叉校验（经验比率校准休眠，沿用斐波那契+端点锁定）" if not _have_valid
                          else "子浪ⅴ与独立 5 浪幅度估计基本一致"),
        },
        # R105 透明度信号：真实浪③同浪级结构（唯一完成的真实同浪级样本）子浪ⅱ回撤仅 ~22%，
        # 远低于当前浪⑤模型假设 ~61.8% 回撤深度——提示模型对子浪ⅱ回撤可能系统性偏深、回踩买②位或偏高；
        # 仅 1 个真实样本，仅供参考、不覆盖模型（经验比率校准因样本不足仍休眠，R104 已确认）。
        "realRef": {
            "wave3Sub2Ret": round(SUB_RET, 1),
            "modelSub2Ret": round(R_II_RET * 100, 1),
            "note": "真实浪③同浪级结构中子浪ⅱ回撤仅约 %.0f%%（远低于模型假设的 %.1f%%）——模型对子浪ⅱ回撤深度可能系统性偏深，回踩买②位或偏高；仅 1 个真实样本，仅供参考、不覆盖模型" % (SUB_RET, R_II_RET * 100),
        },
    }
    # 子浪各点真实触达时间(交易日)：与卖点 _horizon_for 同源，相对今日(last_date)推算，
    # 供回测观察窗(从 B日=今日往后推 expDays)与区间±σ 时间锚定。必须用 last_date 而非 _sf_sd，
    # 否则子浪窗口从「今日」推起却被加了「今日→浪⑤起」这段已在过去的天数(约11日)，
    # 与卖点口径脱节、破坏回测窗口单一真值(R48 修复)。_sf_sd 仅用于 _sf_date 绝对日期派生。
    _sf_exp = [_trading_days_between(last_date, _sf_date(k)) for k in range(6)]

    # 子浪时间校准溯源（R50 建议4 / R107）：经典5浪时间先验为主(50%)；经验占比来源=本指数上行5腿摆动，
    # 忠实 MAE 校验显示 N=5 样本下经验占比并不显著优于经典先验(R107 设≥12门槛未启用经验权重)，故维持 50/50。
    sub_forecast["timeCalib"] = {
        "empirical": [round(x, 3) for x in _sf_emp],
        "prior": [round(x, 3) for x in _sf_prior_n],
        "blended": [round(x, 3) for x in _sf_time],
        "empSamplesTime": len(_runs),
        "note": "子浪时间占比：经典5浪先验为主(50%%)；经验占比来源=本指数上行5腿摆动 N=%d(样本不足、未显著优于先验，R107 未启用经验权重)" % len(_runs),
    }

    # ---------- 强势情景与子浪推演同前提(浪④底=w4_low、浪⑤直接启动)，近端锚点须从 subForecast 派生 ----------
    # 旧式硬编码 3980/4258 与 subForecast 的 4372/4493 自相矛盾("强势"反而比详细子浪推演更慢更弱)：
    # 同一前提(浪④底已成、浪⑤直接启动)下，子浪推演 9/28 已到 4372、10/26 即到卖①，而强势情景 9/30 仅 3980、
    # 卖①要到 2027-02 才到——近端推进反而更弱，构成看板内自相矛盾(R147 修复)。
    # 现把强势情景的近端点位(9/30、11/30)改为在 subForecast 路径上线性插值，保证两图讲同一个浪⑤故事；
    # 卖①②③与回撤沿用框架派生(_s0/_s2/4700)，不破坏"浪⑤目标端点由框架派生"纪律(旧注释 R605)。
    _sf_sorted = sorted(sub_forecast["points"], key=lambda p: p["date"])
    def _sf_price_at(ts):
        _td = pd.Timestamp(ts)
        if _td <= pd.Timestamp(_sf_sorted[0]["date"]):
            return _sf_sorted[0]["price"]
        if _td >= pd.Timestamp(_sf_sorted[-1]["date"]):
            return _sf_sorted[-1]["price"]
        for i in range(len(_sf_sorted) - 1):
            _a, _b = _sf_sorted[i], _sf_sorted[i + 1]
            _da, _db = pd.Timestamp(_a["date"]), pd.Timestamp(_b["date"])
            if _da <= _td <= _db:
                _frac = (_td - _da).days / max(1, (_db - _da).days)
                return _a["price"] + _frac * (_b["price"] - _a["price"])
        return _sf_sorted[-1]["price"]
    scenarios[1]["points"] = [
        [last_date, last_close],
        ["2026-09-30", r2(_sf_price_at("2026-09-30"))],
        ["2026-11-30", r2(_sf_price_at("2026-11-30"))],
        ["2027-02-26", _s0],
        ["2027-05-31", _s2],
        ["2027-08-31", 4700],
    ]

    # ---------- 修复 R44：归档前预先注入真实触达时间 expDays ----------
    # run_backtest 内部 archive→extract_targets 读取 expDays 做观察窗自适应(max(HORIZON,expDays)，R42 修复)。
    # 但 _enrich(写真实 expDays) 在第507~515行、run_backtest 之后才执行，故归档时读不到真实 expDays、
    # 回退 HORIZON=30 → R42 观察窗自适应对中长期卖点/子浪预测失效，命中率被系统性压低（每日自动化推进后必触发）。
    # 此处用已定义的 _horizon_for/_sf_exp 预先写入，使归档落库真实长周期；_enrich 后续重算同一 expDays
    # 并补 prob/lo/hi，与展示完全一致，二者不冲突。expDays 依赖 _horizon_for/_sf_exp（不依赖 bt_stats），
    # 而 prob 依赖 bt_stats —— 故 expDays 先于 run_backtest、prob 后于 run_backtest，化解循环依赖。
    for _s in sell_targets:
        _s["expDays"] = _horizon_for(_s["price"])
    for k, _p in enumerate(sub_forecast["points"]):
        _p["expDays"] = _sf_exp[k]
    for k, _r in enumerate(sub_forecast["rows"]):
        _r["expDays"] = _sf_exp[k + 1]

    # ---------- 预测回测闭环（提前至此，供区间+概率派生回溯命中率；每日自动化无需改）----------
    # 存档当日目标 + 重评全部历史记录 + 聚合命中率，结果供下方 _enrich 取实证命中率。
    # R69：激活「回测实证」级 —— walk-forward 历史类比(条件于当前波动率 regime)
    # 设计痛点：run_backtest 依赖 predictions_log 逐日归档并在未来窗口评估；冻结数据沙箱里
    # 归档记录恒落末日、观察窗恒超数据范围 → totalEvaluated 恒 0，「最准一级」从未生效，概率
    # 只能靠盲走随机游走(漂移模型)，且漂移模型对远目标系统性高估(实测 卖① 实证 2.6% vs 漂移 10%)。
    # 现对当前波动率 regime 相似的锚点(其后 horizon 日数据真实存在)统计「首达触达目标」的历史
    # 频率，作为该目标的实证命中率喂入 binder。经临时测试(_test_empirical.py)：同口径下实证估计
    # Brier≈0.14 < 漂移模型 Brier≈0.17，更诚实、更准确，故激活后概率由实证级主导(漂移退居先验)。
    daily_vol = float(ret.rolling(20).std().iloc[-1])    # 前置定义：本 R69 块即需用于实证锚点 vol 匹配
    _dvol_series = ret.rolling(20).std()                 # 复用文件头 ret（每日对数收益 std）
    _band = (0.75, 1.25)                               # 波动率匹配带(同波动环境类比)；R81 无趋势态下验证更宽带=更多锚点=估计更稳(0.75,1.25 最优)。
    # R82 在 vol 匹配上叠加固定60日MA趋势态匹配后，R83b 曾用一套 OOS 测试台得出"窄带(0.85,1.15)
    # 在趋势态子集内更同质→更准"并据此收窄；但 R85 用【完全忠实】walk-forward 台(从已部署 data.js 取
    # 生产目标价/触达天数、逐(测试日,目标)复刻生产 _empirical_rates 锚点构造=全局[vol带+趋势态]池+
    # 逐目标 i+exp<t 无前视跳过、等权实证命中率直接作预测)重扫带宽，推翻 R83b 结论：
    #   带(0.75,1.25) Brier=0.02430(池中位138) < (0.85,1.15) 0.02572(池中位92) < (0.90,1.10) 0.02840 < (0.95,1.05) 0.03475
    # 即更宽带=更多同质锚点=实证命中率估计方差更低=概率校准更好(Brier 更低)。R83b 窄带结论系其测试台
    # 口径偏差(全局用最大 horizon 截断锚点池，与生产逐目标跳过不一致)造成的假象。故 R85 撤销 R83b 收窄，
    # 还原 R81 的(0.75,1.25)；今日锚点池≈105(>>20 激活阈值)，远目标不脱锚、估计更稳更准。
    # R82：在「波动率匹配」之上叠加「固定MA趋势态匹配」——锚点须与今日同为上行态/下行态
    # (close vs 固定窗MA)。理由：实证首达命中率本就按方向(上行/下行触达)分层，但同波动环境下、不同
    # 趋势态(牛/熊腿)的历史触达行为显著不同(熊腿中上行目标更难触、牛腿中更易)，仅按 vol 匹配会
    # 把两类混合→命中率估计被稀释。固定MA(非 horizon-matched)彻底规避 R73 发现的符号翻转脆断
    # (长目标跨120/250日漂移符号边界来回跳变、锚点池在上下行间抖)，趋势态判定稳定逐日连续。
    # 窗口选取：R86 忠实 walk-forward OOS 扫描 15/20/30/40/60/90/120 日，Brier 单调(15≈20<30<40<60<90<120)，
    # MA20=0.03360 最优(比 MA60 0.03418 低 −1.7%、池中位 131≈132 不缩)，故采用固定20日MA。
    # 忠实 walk-forward OOS Brier 复验(R81 同款无前视口径，强制 j<=i-exp)：纯实证级共同激活集
    # vol-only=0.01621 → vol+趋势态=0.01230(更准 −24%)；晚期窗口 0.0327→0.0248(−24%)；全样本
    # 0.01342→0.01238(−7.8%)，早期窗口两者均≈0(早期固定目标远高于当时价、触达概率与实测同≈0，易判)。
    # 故 R82 重新启用趋势态匹配(固定60日MA变体)，与波动率匹配互补、提升实证命中率准确度。
    _ma_series = df["close"].rolling(20).mean()   # 趋势态固定MA窗口；R86 忠实 walk-forward OOS 扫描 15/20/30/40/60/90/120 日：Brier 单调(15≈20<30<40<60<90<120)，MA20=0.03360 最优、比 MA60 0.03418 低 −1.7%，且池中位 131≈MA60 的 132(锚点池不缩)；故 R86 将趋势态固定窗由 60 日收窄至 20 日。仍属「固定MA(非 horizon-matched)」，R73 符号翻转脆断规避不变。
    _today_trend_up = bool(df["close"].iloc[-1] > _ma_series.iloc[-1])  # 今日趋势态(牛/熊腿)
    _anchors = [i for i in range(len(df))
                if not math.isnan(_dvol_series.iloc[i])
                and _band[0] * daily_vol <= _dvol_series.iloc[i] <= _band[1] * daily_vol
                and not math.isnan(_ma_series.iloc[i])                 # 需有20日MA(>=20交易日前)
                and (df["close"].iloc[i] > _ma_series.iloc[i]) == _today_trend_up]  # 趋势态匹配

    # 趋势方向筛选：R73 当年用有缺陷测试台(同 R79/R80 的「距末日加权+未复刻 i+_exp>=_nn 跳过」
    # 假象)得出「趋势筛选中性(horizon-matched 0.02665 vs 纯vol 0.02624)」并移除；且 horizon-matched
    # 写法对长周期目标(卖③ _exp≈144 跨 120/250日漂移符号边界)确有符号翻转脆断。R82 用忠实
    # walk-forward 台(同 R81 无前视口径)重测「固定MA趋势态」变体：纯实证级共同激活集
    # vol-only=0.01621 → vol+趋势态=0.01230(−24%)、晚期窗口 0.0327→0.0248(−24%)、全样本
    # 0.01342→0.01238(−7.8%)——趋势态匹配确能提升实证命中率(牛/熊腿历史触达行为不同，仅vol匹配会
    # 稀释)，且固定MA规避了 horizon-matched 的符号翻转(趋势态判定稳定、逐日连续)。R86 进一步忠实 OOS
    # 扫描固定窗 15/20/30/40/60/90/120 日，Brier 单调、MA20 最优(比 MA60 低 −1.7%)，故 R82 重新
    # 启用、R86 将窗口由60日收窄至20日的趋势态匹配(见上方 _anchors 的 MA20 趋势态条件)，与波动率匹配互补、准确度更优。
    # 【R81 校正】R79/R80 的「时间指数衰减加权(RECENCY_TAU)」经更忠实的 walk-forward OOS Brier 复验
    # 被证为缺陷测试台造出的假象：旧台用「距实时末日」做锚点权重、且未复刻部署代码
    # `if i+_exp>=_nn: continue` 的「锚点不得依赖未来数据」跳过规则 → 对历史测试日把权重压到
    # 距末日近、却与待测日无关的锚点 → 实证估计退化 → 时间衰减看似更优实为噪声。
    # 忠实复刻(权重按距模拟当日 i、且强制 j<=i-exp 与部署一致)后结论反转：
    #   等权(tau→∞) Brier=0.0832 << 任一时间衰减(tau=30 当前=0.1054，约差21%)；
    #   衰减把权重压到少数近期锚点→有效样本锐减→估计方差变大→Brier 升高。
    # 故 R81 撤销时间衰减、改回等权，并适度放宽匹配带至(0.75,1.25)(更宽带=更多样本=估计更稳更准，
    # (0.75,1.25)=0.0832 最优、早期0.060/晚期0.109 均显著优于原值、稳健可信)。
    # 时间衰减机制已从 _empirical_rates 移除(经验证有害)；_band 常量保留便于未来以忠实测试台重标。

    def _empirical_rates(anchors, vol_for, vol_scale):
        """对当前各目标(方向/幅度/horizon)统计 walk-forward 实证首达命中率（条件于当前 vol regime，等权）。

        返回 {(cat,key): (hitRate%, eff_n)}；eff_n=命中锚点数(有效样本量，作贝叶斯融合权重)。
        原始锚点数<10 的目标不返回(留待漂移模型冷启动分支)。浪⑤起(已实现的浪④低)排除。
        【R81 校正】R79/R80 的「时间指数衰减加权」经忠实 walk-forward OOS Brier 复验被证为缺陷测试台假象
        （旧台锚点权重按距实时末日、且未复刻 `i+_exp>=_nn` 跳过规则），已撤销：等权(tau→∞) Brier=0.0832
        显著优于任一时间衰减(tau=30=0.1054，约差21%)；衰减压低有效样本→估计方差变大→Brier 升高。"""
        _close = df["close"].values
        _high = df["high"].values
        _low = df["low"].values
        _nn = len(df)
        _items = []
        for s in sell_targets:
            _items.append(("sellTarget", s["name"], s["price"], s["expDays"]))
        for k, _p in enumerate(sub_forecast["points"]):
            if _p["label"] == "浪⑤起":
                continue
            _items.append(("subwave", _p["label"], _p["price"], _sf_exp[k]))
        for k, _r in enumerate(sub_forecast["rows"]):
            _items.append(("subwave", _r["wave"], _r["target"], _sf_exp[k + 1]))
        _out = {}
        for cat, key, price, exp in _items:
            _ratio = price / last_close
            _up = price >= last_close
            _exp = max(1, int(round(exp)))
            # R89：触达容差对齐预测带宽(与 _enrich band 同定义)。原固定 0.1% 容差算的是"触达精确价"，
            # 与模型展示的宽预测带[lo,hi]语义脱节→远目标命中率被系统性低估、概率失真。对齐后 prob 语义
            # = "触达预测带"，与展示区间自洽；忠实 walk-forward OOS Brier 0.03220→0.01951(−39%%，近期regime −55%%)。
            _frac = min(vol_for(_exp) * math.sqrt(_exp) * vol_scale, 0.235)
            _wsum = 0.0
            _whit = 0.0
            _raw = 0
            # 等权：所有 vol 匹配锚点一视同仁(有效样本最大、估计方差最小)。
            # 锚点须 i+_exp < _nn(其触达窗口完全落在已知历史内，不依赖未来数据，与部署一致)。
            for i in anchors:
                if i + _exp >= _nn:
                    continue
                _base = _close[i]
                _pref = _base * _ratio
                _fh = _high[i + 1:i + 1 + _exp]
                _fl = _low[i + 1:i + 1 + _exp]
                if _up:
                    _hit = _fh.max() >= _pref * (1.0 - _frac)   # 上行目标进入 band 下缘 lo=price*(1-bandPct%)
                else:
                    _hit = _fl.min() <= _pref * (1.0 + _frac)   # 下行目标进入 band 上缘 hi=price*(1+bandPct%)
                _w = 1.0
                _wsum += _w
                if _hit:
                    _whit += _w
                _raw += 1
            if _raw >= 10 and _wsum > 0:
                _hr = _whit / _wsum * 100.0
                _eff_n = _wsum
                _out[(cat, key)] = (round(_hr, 1), round(_eff_n, 1))
        return _out

    _bt_partial = {"updated": last_date, "lastClose": last_close,
                   "tradePlan": trade_plan, "subForecast": sub_forecast}
    bt_stats = run_backtest(_bt_partial, df)
    bt_lookup = {(s["cat"], s["key"]): (s["hitRate"], s["n"]) for s in bt_stats["summary"]}
    _vol_windows = [20, 60, 120, 250]
    _vol_by_w = {w: float(ret.rolling(w).std().iloc[-1]) for w in _vol_windows}
    def _vol_for(exp):
        _w = min(_vol_windows[-1], max(_vol_windows[0], float(exp)))
        if _w <= _vol_windows[0]:
            return _vol_by_w[_vol_windows[0]]
        if _w >= _vol_windows[-1]:
            return _vol_by_w[_vol_windows[-1]]
        for i in range(len(_vol_windows) - 1):
            w0, w1 = _vol_windows[i], _vol_windows[i + 1]
            if w0 <= _w <= w1:
                _t = (_w - w0) / (w1 - w0)
                return _vol_by_w[w0] * (1 - _t) + _vol_by_w[w1] * _t
        return _vol_by_w[_vol_windows[-1]]

    # R69：把 walk-forward 实证命中率喂入 binder 的「回测实证」级（最准一级）
    # R72 防御性守卫：若波动率 regime 剧变导致匹配锚点池为空(或过小)，实证级无法成立，
    # 主动降级并留痕，避免对空池静默跑出无意义概率。
    if len(_anchors) < 20:
        bt_stats["empiricalActive"] = False
        bt_stats["empiricalAnchors"] = len(_anchors)
        bt_stats["empiricalWarn"] = "波动率匹配锚点池仅 %d 个(<20)，实证级暂不可用，概率回退漂移模型" % len(_anchors)
        _emp = {}
    else:
        _emp = _empirical_rates(_anchors, _vol_for, _vol_scale)
    for (cat, key), (hr, ec_n) in _emp.items():
        bt_lookup[(cat, key)] = (hr, ec_n)
        _found = False
        for _s in bt_stats["summary"]:
            if (_s["cat"], _s["key"]) == (cat, key):
                _s["hitRate"] = hr
                _s["n"] = ec_n
                _s["cold"] = ec_n < MIN_SAMPLE
                _s["hits"] = round(ec_n * hr / 100.0)
                _found = True
                break
        if not _found:
            bt_stats["summary"].append({"cat": cat, "key": key, "n": ec_n,
                                         "hits": round(ec_n * hr / 100.0),
                                         "hitRate": hr, "avgDays": None,
                                         "cold": ec_n < MIN_SAMPLE})
    bt_stats["coldStart"] = (len(_anchors) < 20)
    bt_stats["empiricalActive"] = (len(_anchors) >= 20)
    bt_stats["empiricalAnchors"] = len(_anchors)

    # ---------- 预测目标「区间 + 概率」派生（提升预测表达诚实度，消除伪精度点位）----------
    # 区间宽度：由近期已实现波动率(日对数收益标准差) × 期望触达天数开方 推导，即 ~1σ 触达带；
    #   浪型重校订时目标价变，区间自动跟随（与框架同源，无新硬编码）。
    # 概率（R49 三级降级，单一真值由 bt_lookup 派生，不写死）：
    #   ① 回测实证命中率(样本>=3)最高优先；
    #   ② 漂移模型(主估计)：带漂移随机游走「首达概率」(反射原理)，μ 取 horizon
    #      匹配窗口(20/60/120/250 日)日漂移，随目标距离与漂移方向自适应、鉴别性强；
    #      旧式终点分布 Φ((a-μ·exp)/(σ·√exp)) 对远目标概率反而升高(符号错)，现改为首达模型：
    #      远目标概率单调下降，更贴合「价格能否在区间内触及目标位」语义；
    #   ③ 历史浪幅校准(可信上限)：本指数 Zigzag 真实摆动经验触达率，当漂移模型超过历史可达
    #      上限时封顶防过度乐观——当前指数高波动下可达性充裕，故多绑定漂移模型；低波动或远目标
    #      时历史封顶生效。三者均 clamp[10,90]、标注 probSrc，9 月初回测样本到位自动升级回测实证。
    daily_vol = float(ret.rolling(20).std().iloc[-1])   # 日对数收益标准差(比率)

    # 注：bandPct≤23.5% 的硬上限由 _enrich 内 `min(_band, price*0.235)` 直接保证(R74)，
    # 不再依赖 _exp_cap 对概率 horizon 的截断；概率用真实 horizon 更准确（见 _enrich 注释）。

    # ---------- 概率模型 walk-forward 实证校准（R68：可真实运行的「回测实证」体检）----------
    # 设计痛点：build_data 的概率 binder 三级降级中「回测实证」一级依赖 predictions_log 逐日
    # 归档并在未来窗口评估；但冻结数据沙箱里归档记录恒落末日、观察窗恒超数据范围 → totalEvaluated
    # 恒为 0，「最准一级」从未生效。此处用一套【在冻结数据上可真实运行】的 walk-forward 校准：
    # 对历史足够早的锚点 T（其后的 horizon 日数据真实存在），仅用 T 之前信息估 vol/drift regime，
    # 用与 _enrich 逐字一致的首达概率公式预测 P(触达 ±r | 自 T 起 horizon 日)，再与 T 之后真实
    # 触达比对，输出 Brier/校准斜率/校准曲线。这是概率模型真正的实证体检，量化其可信度，
    # 并驱动「是否需温度修正」的决策（本数据校准良好 → 不修正，避免无谓改动破坏准确性）。
    _calib = _run_calib(df, vol_conf=_drift_conf, daily_vol=daily_vol)

    # 波动率 term structure（R63 增强）：长周期目标不该用最短窗口(20d，当前处90分位高波动)
    # 已实现波动外推 √T，否则高估远期离散度、压低远期首达概率。改由 horizon 匹配的多窗口波动率
    # 平滑插值（与 _drift_for 同源口径）：短周期用20d、长周期向更低波动的长期均值回落，
    # 贴合波动率均值回归特性，提升远期首达概率/带宽估计准确度。bandPct≤23.5% 上限由
    # _enrich 的 `min(_band, price*0.235)` 独立兜底，与 _exp_cap 解耦(R74)。

    # ---------- R49 概率三级降级（回测实证 → 历史浪幅校准 → 漂移模型）----------
    # 旧 _enrich 冷启动分支用固定系数 92 - z*26 拍脑袋映射，与指数真实摆动无关、且
    # 对近期动量过拟合。R49 改为数据驱动三级：
    #   ① 回测实证命中率（n>=3，最高优先，由 bt_lookup 提供）
    #   ② 历史浪幅校准：从本指数真实 Zigzag 完成浪提取「标准化摆动幅度」经验分布，
    #      目标标准化距离 z_t = |log(P/last_close)|/(daily_vol·√exp)，取同向、时间尺度
    #      相近(horizon∈[0.5,2]×exp)的历史浪段，经验触达率=标准化幅度>=z_t 的占比。
    #      是「本指数实际怎么摆」的数据驱动估计，比固定系数诚实。
    #   ③ 漂移模型：带漂移随机游走终端穿越概率 Φ((a-μ·exp)/(σ·√exp))，μ 取与 horizon
    #      匹配窗口(20/60/120/250 日)的日漂移，避免用 20 日恐慌漂移外推 10 个月目标。
    def _norm_cdf(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    # 多尺度日漂移（与 horizon 匹配，避免短期动量过拟合长周期目标）
    _drift_windows = [20, 60, 120, 250]
    _drift_by_w = {w: float(ret.rolling(w).mean().iloc[-1]) for w in _drift_windows}
    def _drift_for(exp):
        # 多尺度日漂移随 horizon 平滑插值（消除离散窗口跳变）：边界外取端点值，
        # 使不同触达周期的目标概率连续、不再在 20/60/120/250 档位间硬跳变（提升概率估计平滑度）。
        _w = min(_drift_windows[-1], max(_drift_windows[0], float(exp)))
        if _w <= _drift_windows[0]:
            return _drift_by_w[_drift_windows[0]]
        if _w >= _drift_windows[-1]:
            return _drift_by_w[_drift_windows[-1]]
        for i in range(len(_drift_windows) - 1):
            w0, w1 = _drift_windows[i], _drift_windows[i + 1]
            if w0 <= _w <= w1:
                _t = (_w - w0) / (w1 - w0)
                return _drift_by_w[w0] * (1 - _t) + _drift_by_w[w1] * _t
        return _drift_by_w[_drift_windows[-1]]

    # ---------- R217：裸首达概率单一真源（供 _enrich 与分段校准拟合共用，防漂移）----------
    def _drift_prior_prob(base, price, exp):
        """裸首达概率(反射原理)，返回 (p 0-1, barrier)。base=锚点价(当前=last_close)，price=目标位。
        与 _enrich 内联公式逐字一致，抽离为避免校准拟合与线上各自重写导致公式漂移。"""
        _exp = max(10, int(round(exp)))
        _frac = min(_vol_for(_exp) * math.sqrt(_exp) * _vol_scale, 0.235)
        _mu_eff = _drift_for(_exp) * _drift_conf
        _sv = _vol_for(_exp) * math.sqrt(_exp) if _vol_for(_exp) > 0 else 1e-9
        _dir = 1 if price >= base else -1
        if _dir > 0:
            _barrier = price * (1.0 - _frac)
            if base >= _barrier:
                return 1.0, _barrier
            _a = math.log(_barrier / base)
            _d1 = (_a - _mu_eff * _exp) / _sv
            _d2 = (-_a - _mu_eff * _exp) / _sv
            _exo = max(-50.0, min(50.0, 2.0 * _mu_eff * _a / (_vol_for(_exp) ** 2 + 1e-12)))
            return 1.0 - _norm_cdf(_d1) + math.exp(_exo) * _norm_cdf(_d2), _barrier
        else:
            _barrier = price * (1.0 + _frac)
            if base <= _barrier:
                return 1.0, _barrier
            _a = math.log(_barrier / base)
            _b = -_a
            _d1 = (_b + _mu_eff * _exp) / _sv
            _d2 = (_mu_eff * _exp - _b) / _sv
            _exo = max(-50.0, min(50.0, -2.0 * _mu_eff * _b / (_vol_for(_exp) ** 2 + 1e-12)))
            return 1.0 - _norm_cdf(_d1) + math.exp(_exo) * _norm_cdf(_d2), _barrier

    # ---------- R217：分段校准（OOS 验证通过，部署修正低概率系统性低估）----------
    # 在冻结数据上 walk-forward 拟合「裸首达概率→经验命中率」的分桶校准映射：对历史上足够早的
    # 锚点 i（其 horizon 日数据真实存在），用与 _enrich 逐字一致的 _drift_prior_prob（当前 regime
    # 阻尼）预测 P(触达 ±r | 自 i 起 H 日)，与 i 之后真实是否触达比对，按 10 等宽桶取经验命中率
    # （伪计数收缩防噪声；空/少样本桶回退近似恒等）。部署时对所有目标的首达先验做单调校准，
    # 不改三级降级结构。验证（R217_segcal_check.py，前60%训练/后40% OOS）：分桶经验校准验证
    # Brier 0.2008→0.1442(−28.2%)，PAVA 0.1465(−27.0%)，低概率桶(model<0.2) gap 0.27→0.14；
    # 满足 R85 纪律（OOS 确降 Brier 才部署）。映射写入 data.js.probCalib，audit50 同表复算。
    _K_BUCKET = 10
    _recal_edges = list(np.linspace(0.0, 1.0, _K_BUCKET + 1))

    def _fit_prior_recal():
        _close = df["close"].values
        _high = df["high"].values
        _low = df["low"].values
        _n = len(_close)
        _r_grid = [0.03, 0.05, 0.08, 0.12, 0.15, 0.20]
        _h_grid = [20, 40, 60, 90, 120, 180, 250]
        _max_h = 250
        _p_list, _y_list = [], []
        for _i in range(20, _n - _max_h - 1):
            _base = _close[_i]
            for _H in _h_grid:
                if _i + _H >= _n:
                    continue
                for _r in _r_grid:
                    _pu, _bar_up = _drift_prior_prob(_base, _base * (1.0 + _r), _H)
                    _hit_up = (_high[_i + 1:_i + 1 + _H].max() >= _bar_up)
                    _p_list.append(_pu); _y_list.append(1.0 if _hit_up else 0.0)
                    _pd, _bar_dn = _drift_prior_prob(_base, _base * (1.0 - _r), _H)
                    _hit_dn = (_low[_i + 1:_i + 1 + _H].min() <= _bar_dn)
                    _p_list.append(_pd); _y_list.append(1.0 if _hit_dn else 0.0)
        _p = np.array(_p_list, dtype=float)
        _y = np.array(_y_list, dtype=float)
        _vals = []
        for _b in range(_K_BUCKET):
            _lo, _hi = _recal_edges[_b], _recal_edges[_b + 1]
            _m = (_p >= _lo) & (_p < _hi) if _b < _K_BUCKET - 1 else (_p >= _recal_edges[_K_BUCKET - 1])
            _cnt = int(_m.sum())
            if _cnt >= 40:
                _emp = _y[_m].mean()
                _vals.append(float((_emp * _cnt + 0.5 * 5.0) / (_cnt + 5.0)))  # 伪计数收缩
            else:
                _vals.append(0.5 * (_lo + _hi))   # 空/少样本桶：近似恒等（回退原始）
        return _vals

    _recal_vals = _fit_prior_recal()

    def _recal_g(p):
        """分段校准映射：裸首达概率(0-1)→经验校准概率(0-1)，单调分桶，空桶恒等。
        近确定(≥0.98，多因已在 band 内)恒等，避免已触达目标被校准降权。"""
        _p = max(0.0, min(1.0, float(p)))
        if _p >= 0.98:
            return _p
        _idx = min(_K_BUCKET - 1, max(0, int(np.digitize(_p, _recal_edges) - 1)))
        return _recal_vals[_idx]

    # _hist_legs 已在文件头 st 加载后提前构建（单一真源），此处不再重复。

    def _hist_calib(price, exp):
        """返回与目标同向、时间尺度相近的历史浪段「经验触达率」(%)；样本过薄返回 None。

        经验可达性 = 本指数 Zigzag 真实摆动中，「同方向、时长与目标窗口相近(0.5~2 倍)的腿」
        里，幅度 ≥ 目标对数幅度 的占比。即「历史上相似时间内，指数有多大比例做出过不亚于
        目标所需的上涨/下跌」。纯数据驱动的可信上限。

        【R57 修复】旧版用「腿自身本地波动率」做 z 标准化( regime-aware )：强趋势腿日波动极小
        → z 被人为放大 → 经验率恒≈100% → clamp 90 永远不绑定、该增强名存实亡，过度乐观的漂移
        估计从不被历史封顶。新版直接比「幅度」(时间尺度已匹配)，使历史校准真正约束漂移估计。
        【R58 增强】加「最小有效样本 MIN_LEGS」：时间尺度匹配窗口常只含 2~5 条腿，会把经验率
        钉在 90(封顶)或 50/100(噪声)→校准形同虚设。现渐进放宽时间窗([0.5,2]→[0.25,4]→[0,∞))
        直到样本量达标再计算；全部窗口仍不足 MIN_LEGS 则返回 None(历史太薄→不伪造封顶，让漂移
        模型作主估计，比钉 90 更诚实)。使历史校准在样本充足时真正约束过度乐观的漂移估计。"""
        _a = math.log(price / last_close)                  # 带符号对数距离(目标-今)
        _dir = 1 if _a >= 0 else -1
        _at = abs(_a)                                      # 目标所需对数幅度
        def _rate(lo_b, hi_b):
            _n = _d = 0
            for _lr, _ld, _d0, _d1 in _hist_legs:
                if _dir * _lr <= 0:            # 仅取同向历史浪段
                    continue
                # R65 修复：用「达到目标幅度所需时间」ttr=时长×(目标幅度/腿幅度) 做时间窗匹配，
                # 而非整条腿时长——腿幅度常远大于目标，整条腿时长会高估触达时间、把本应落入目标
                # 窗口的腿错排到更宽窗口，低估历史可达率(封顶过松/漏封)。ttr 更贴近真实触达节奏。
                _ttr = (_ld * _at / abs(_lr)) if abs(_lr) > 0 else _ld
                if not (lo_b <= _ttr <= hi_b):  # 时间尺度相近(与目标窗口匹配)
                    continue
                _d += 1
                if abs(_lr) >= _at:            # 历史腿幅度 ≥ 目标幅度 → 该窗口内可达
                    _n += 1
            return _n, _d
        # 渐进放宽时间窗直到样本量达标，避免小样本把经验率钉在 90/50/100(噪声)。
        # 优先紧匹配([0.5,2]×exp)，不足则放宽([0.25,4]×exp、[0,∞))；首个达 MIN_LEGS 的窗口
        # 用于计算经验率；全部不足则返回 None(历史太薄→不伪造封顶)。
        MIN_LEGS = 4
        for _lo, _hi in ((0.5 * exp, 2.0 * exp), (0.25 * exp, 4.0 * exp), (0.0, 1e9)):
            _n, _d = _rate(_lo, _hi)
            if _d >= MIN_LEGS:
                return max(2, min(98, _n / _d * 100))
        return None

    # R90 融合伪计数（经验贝叶斯收缩）：实证命中率与「漂移先验」按样本量收缩融合。
    # 忠实 walk-forward OOS 复验发现：实证率相关性高(corr 0.67 vs 先验 0.65，排序信号更优)，
    # 但系统性低估命中率(均值 0.13 vs 实测 0.25)——因其对历史相似日「等权平均」，把当前上行漂移
    # 也平均掉了；而漂移先验用当前 drift(_drift_for*_drift_conf)捕捉当下趋势→校准良好(0.23≈0.25)。
    # 旧 K=MIN_SAMPLE=3 使融合被「低估的实证率」主导→Brier 偏高。增大 K 把融合拉向校准良好的
    # 先验，修正系统性低估：OOS Brier 0.1146(K=3)→0.1053(K=20)(−8%)；同时保留实证率的排序信号
    # (K=20 时实证仍占 67~80% 权重)。K=20 为兼顾偏差修正与实证信号、且不过拟合本 455 日样本的稳健取值
    # (K→∞ 纯先验虽再降 Brier 但丢弃实证排序信号、且对本数据集过拟合风险高，故不取)。
    _FUSE_K = 20.0

    def _enrich(cat, key, price, exp):
        # exp = 真实触达时间估计(交易日)，由历史摆动腿幅度-时长独立派生（见 _horizon_for），
        # 不再用距离假经验式 15+dist*120（旧式与情景时间窗脱节 8~10 倍，导致区间过窄、概率失真）。
        # 区间宽度 = 价格 × 日波动 × √期望触达天数，即真实时间尺度的 ~1σ 触达带。
        # 概率 horizon 用「真实触达时间」(不被 _exp_cap 截断)：_exp_cap 最初用于把 bandPct 压在
        # 23.5% 内，但带宽已有硬上限 min(_band, price*0.235) 兜底(任意 horizon 经此截断后必 ≤23.5%，
        # 守审计 (0,25))；若概率也随 _exp_cap 截断，远目标(horizon>_exp_cap)会被迫用偏短 horizon 估算，
        # 首达概率被系统性低估、失真。故概率用真实 horizon，带宽由硬上限独立保证(与 audit50 同步)。
        _exp = max(10, int(round(exp)))
        # R89/R105：显示带≡概率带。卖点与子浪统一用 vol term structure 派生 _frac（horizon 匹配波动率），
        # 概率引擎本就用 _frac 定义"触达"（R89 原则）；旧式子浪单独用历史离散度 _SUB_BAND_PCT 单一值，
        # 与概率指的 4.46~10.08% 窄带割裂（短 horizon 子浪尤其失真），现纠正为与概率一致（N3）。
        _frac = min(_vol_for(_exp) * math.sqrt(_exp) * _vol_scale, 0.235)
        _band = price * _frac
        _band = min(_band, price * 0.235)                          # 硬上限：bandPct≤23.5%，守审计(0,25)
        _lo = r2(price - _band)
        _hi = r2(price + _band)
        _band_pct = round(_band / price * 100, 1)
        _bt = bt_lookup.get((cat, key))               # (hitRate, n) | (None, n) | 缺失
        # 漂移先验（含 波动率regime阻尼 + 跨指数共振加权），实证/冷启动两路共用，避免重复计算
        _dir = 1 if price >= last_close else -1           # 目标方向(上行卖点 / 下行买点)
        # _frac 已在上方定义（显示带≡概率带，R89/R105），此处不再重复计算
        _mu_eff = _drift_for(_exp) * _drift_conf          # 波动率 regime 阻尼趋势信任
        # 首达概率(反射原理)：价格在区间内「触及」目标位的概率，比旧式终点分布模型
        # Φ((a-μT)/σ√T) 更贴合「能否到达目标」语义，且修正了其对远目标概率反而升高的
        # 符号错误——现远目标概率单调下降(越远越难达，符合常识)。上行 a>0 / 下行 a<0 对称处理。
        # 经蒙特卡洛(8万路径×1000步)校验：上行/下行公式与模拟误差均<1.6%，数学正确(R55验证)。
        _hit_raw, _ = _drift_prior_prob(last_close, price, exp)   # 裸首达概率(0-1)，单一真源(见 R217)
        _hit = _recal_g(_hit_raw)                                # R217：分段校准，修正低概率系统性低估(OOS Brier −27%)
        _p_drift = max(2, min(98, _hit * 100))
        _hcap = _hist_calib(price, _exp)                  # 历史可达上限(%)，None=无样本
        _prior = _p_drift + _breadth * _dir * 5.0         # 漂移先验 = 漂移模型 + 跨指数共振加权(R50 建议5)
        if _bt is not None and _bt[0] is not None:
            # 贝叶斯融合：实证命中率 _hr 是 _empirical_rates 返回的【原始 walk-forward 频率】
            # (hits/tot，未预收缩；Laplace 收缩只在已废弃的 backtest.aggregate 归档路径，本路径不用)。
            # 平滑/收缩由本融合的「先验伪计数 K=MIN_SAMPLE」提供：n=MIN_SAMPLE 时 先验/实证 各半，
            # n 越大越偏实证——天然避免 n 刚越阈值时的硬跳变(如 0/3→10% 砸落、3/3→70% 拉高)，更准更稳。
            # (R72 修正：原注释误称"实证率已含 (hits+0.5)/(n+2) 收缩"，与代码事实不符，已更正。)
            _hr, _bn = _bt
            _K = _FUSE_K   # R90：增大伪计数→融合更偏向校准良好的漂移先验(实证率系统性低估命中率)，OOS Brier −8%
            _fused = (_bn * _hr + _K * _prior) / (_bn + _K)
            _prob, _src = round(max(2, min(98, _fused)), 1), "回测实证"
        else:
            # 三级降级（R49）+ R50 增强 冷启动：漂移先验 作主估计 → 历史浪幅校准(可信上限)约束 → 漂移兜底。
            # 三者皆 clamp[10,90]、数据驱动，彻底替换旧式 92-z*26 拍脑袋。
            if _hcap is not None and _hcap < _prior:
                # 历史摆动能力不足以支撑(漂移+共振)的乐观估计 → 以历史封顶，标注历史浪幅校准
                _prob, _src = round(_hcap, 1), "历史浪幅校准"
            else:
                _prob, _src = round(max(2, min(98, _prior)), 1), "漂移模型"
        # expDays 返回真实传入值(相对今日交易日数)，与 _sf_date/_trading_days_between 同源口径，
        # 使"触达日期"与"触达交易日数"一致(R60 修复)；带宽/概率计算用 _exp(≥10 防塌陷)已在上方完成。
        return {"lo": _lo, "hi": _hi, "bandPct": _band_pct,
                "prob": _prob, "probSrc": _src, "expDays": exp}

    for _s in sell_targets:
        _s.update(_enrich("sellTarget", _s["name"], _s["price"], _horizon_for(_s["price"])))
    for k, _p in enumerate(sub_forecast["points"]):
        _p.update(_enrich("subwave", _p["label"], _p["price"], _sf_exp[k]))
    # 注意：points 含 [0]浪⑤起，而 rows 的首项是子浪ⅰ(对应 points[1])，故 rows[k]
    # 必须用 _sf_exp[k + 1]，否则子浪预测表会套用"早一个子浪"的触达时间→区间偏窄、
    # 与目标位表(points)对同一子浪不一致（R40 时间锚定引入的索引错位回归 bug）。
    for k, _r in enumerate(sub_forecast["rows"]):
        _r.update(_enrich("subwave", _r["wave"], _r["target"], _sf_exp[k + 1]))

    # ---------- 情景观察矩阵（三区框架）----------
    # 区间边界从精确位派生（铁律线 / 浪⑤首目标），而非硬编码整数(4494/3674)，
    # 消除与 markArea、买卖框架的精确值(3674.40 / 4493.94)出现 0.06~0.40 整数化漂移；
    # 浪型重校订时自动同步，避免"矩阵写4494而实际卖点已变"的脱节
    _zl = trade_plan["stopLine"]["price"]        # 3674.40 铁律线
    _zs = trade_plan["sellTargets"][0]["price"]  # 4493.94 浪⑤首目标(卖①)
    zones = [
        {"name": "狂热观察区", "range": "≥ " + format(_zs, ".2f"), "color": "#c23531", "bg": "#fdf1f0",
         "action": "动作：分批减仓 / 兑现",
         "stance": "浪⑤目标兑现区。若放量急涨至此区域，重点观察浪⑤终结信号（RSI顶背离、长上影、天量滞涨）"},
        {"name": "趋势运行区", "range": format(_zl, ".2f") + " ~ " + format(_zs, ".2f"), "color": "#b8872a", "bg": "#fdf8ef",
         "action": "动作：持有 / 回踩建仓",
         "stance": f"浪④→浪⑤运行区。铁律线上方数浪有效；收复 {w3_hi:.2f} 前高 = 浪④结束右侧确认"},
        {"name": "恐慌关注区", "range": "< " + format(_zl, ".2f"), "color": "#2f9e44", "bg": "#eff8f0",
         "action": "动作：减仓 / 清仓防御",
         # 恐慌区分级支撑与 R34 风险情景路径同源（_risk_r61/_risk_r50y/_risk_r618y），
         # 避免硬编码 3506/3447/3255 双份真值（R34 已把 scenarios 风险路径改派生，此处漏改）；
         # 浪型重校订时自动跟随，不会"情景图已变但矩阵文案仍停旧值"脱节
         "stance": "铁律线证伪区。浪④数法失效转ABC深调，%.0f / %.0f / %.0f 分级观察斐波那契支撑" % (_risk_r61, _risk_r50y, _risk_r618y)},
    ]

    # 动态生成时间窗文案（避免硬编码过期：每日自动更新同步真实窗口日与真实距窗）
    _tz55 = next((t for t in tz_wave3_top if t["n"] == 55), None)
    _tz89 = next((t["date"] for t in tz_wave3_top if t["n"] == 89), "?")
    # 浪3顶->浪4低点 真实交易日差，动态判断其临近的斐波那契变盘窗，
    # 避免写死"+47~55"与真实 +46（差1个交易日，落在窗外）不符、误导"时间窗共振"结论
    # 浪3顶->浪4低 真实交易日差：浪3顶日期从 wave_points[8] 派生（单一真值），
    # 避免写死"2026-05-14"与浪型标注日期双份真值脱节（浪型重校订改 date 时本差自动跟随）
    _gap = safe_idx(idx, pd.Timestamp(_w4_low_date)) - safe_idx(idx, pd.Timestamp(wave_points[8]["date"]))
    # 文案随时间自适应：T+55 已过后"临近"措辞失真（每日更新会让'临近'变假），
    # 改为点明浪4低与该窗的交易日距离，消除随时间漂移的双份真值
    if _tz55 and _tz55["passed"]:
        _off = abs(55 - _gap)
        _rel = "前" if _gap < 55 else "后"
        _tz55_txt = "T+55变盘窗(%s)已过后，浪4低落在T+%d（该窗%s%d交易日）" % (_tz55["date"], _gap, _rel, _off)
    else:
        _tz55_txt = "临近T+55变盘窗(%s)" % (_tz55["date"] if _tz55 else "?")
    # 浪3回撤50%支撑（与 supports[2] 单一真值联动；周线转弱文案引用，消除"3650"硬编码漂移：
    # 浪型重校订时此处自动跟随，不会"文案写3650但支撑表已变"脱节）
    _r50 = next(s["price"] for s in supports if s["name"] == "浪3回撤 50%")
    # 浪⑤终结信号监控（R50 建议2）：RSI/量价顶背离，与触达概率互补(概率管"到不到"，背离管"到了是否终结")。
    # 在最近 90 交易日找局部价格峰，比较最高两峰：后期峰价≥前期峰价 但 RSI/量 走弱 → 顶背离(衰竭预警)。
    # 浪⑤终结信号监控（R50 建议2，R52 精度强化）：仅在「近期双顶」结构内判定顶背离，
    # 避免把相隔数十交易日的远峰误判为顶背离（假阳性）。取最近一个局部峰为基准，
    # 参考峰限定为「前峰、且与近峰相距 8~60 交易日、区间内价格最高」，价格≈前峰或更高
    # （双顶/新高）且 RSI 走弱 ≥3、量能萎缩 <85% → 顶背离（衰竭预警）。
    _seg = df.iloc[-90:]
    _cl = _seg["close"].values; _rs = rsi.reindex(_seg.index).values; _vo = _seg["volume"].values
    _W = 5
    _peaks = []
    for _i in range(_W, len(_cl) - _W):
        if all(_cl[_i] >= _cl[_j] for _j in range(_i - _W, _i + _W + 1) if _j != _i):
            _peaks.append((_i, float(_cl[_i]), (float(_rs[_i]) if not pd.isna(_rs[_i]) else None), float(_vo[_i])))
    _rsi_div = _vol_div = False
    if len(_peaks) >= 2:
        _peaks_t = sorted(_peaks, key=lambda p: p[0])     # 按时间排序，取最近峰
        _recent = _peaks_t[-1]
        # 参考峰：近峰之前、时间距 8~60 交易日的、价格最高局部峰（真正的近期双顶）
        _cand = [p for p in _peaks_t[:-1] if 8 <= (_recent[0] - p[0]) <= 60]
        if _cand:
            _ref = max(_cand, key=lambda p: p[1])
            if _recent[1] >= _ref[1] * 0.99:              # 近峰价≈前峰或更高（双顶/创新高）
                if _recent[2] is not None and _ref[2] is not None and _recent[2] < _ref[2] - 3:
                    _rsi_div = True
                if _recent[3] < _ref[3] * 0.85:
                    _vol_div = True
    _near_s1 = last_close >= trade_plan["sellTargets"][0]["price"] * 0.97
    _div_level = "warn" if (_rsi_div or _vol_div) and _near_s1 else ("watch" if (_rsi_div or _vol_div) else "none")
    divergence = {
        "rsi": _rsi_div, "volume": _vol_div, "level": _div_level,
        "detail": (("RSI顶背离" if _rsi_div else "") + ("、量价顶背离" if _vol_div else "")).strip("、") or "无",
    }


    # R69：预取实证命中率用于「回测样本进度」卡文案（0.0 在 f-string 里 % 不会丢，但 .0 浮点需保真）
    _e1 = bt_lookup.get(("sellTarget", "卖① 保守兑现"))
    _e3 = bt_lookup.get(("sellTarget", "卖③ 激进兑现"))
    _emp_s1v = (_e1[0] if _e1 and _e1[0] is not None else 0.0)
    _emp_s3v = (_e3[0] if _e3 and _e3[0] is not None else 0.0)

    # R174：均线密集压力带共振（轻量集成均线层，纯展示，不动概率引擎）
    _ma250_v = float(ma250.iloc[-1]) if not pd.isna(ma250.iloc[-1]) else None
    _ma120_v = float(ma120.iloc[-1]) if not pd.isna(ma120.iloc[-1]) else None
    _ma60_v3 = float(ma60.iloc[-1]) if not pd.isna(ma60.iloc[-1]) else None
    _sub1 = next((p["price"] for p in sub_forecast["points"] if p["label"] == "子浪ⅰ"), None)
    _sub3 = next((p["price"] for p in sub_forecast["points"] if p["label"] == "子浪ⅲ"), None)
    _pb_lo = _ma250_v if _ma250_v is not None else (_ma120_v or _ma60_v3)
    _pb_hi = _sub1
    _below_year = (_ma250_v is not None and last_close < _ma250_v)
    findings = [
        {"title": "周线级别转强" if weekly['above'] else "周线级别转弱",
         "level": "good" if weekly['above'] else "warn",
         "text": f"周收盘 {weekly['close']} {'高于' if weekly['above'] else '已跌破'} 30周均线 {weekly['ma30w']}" +
                 ("，周线上升趋势完好" if weekly['above'] else
                  f"，是浪④数法的最大隐忧：若周收盘持续位于30周线下方，浪④下探50%回撤位{_r50:.2f}的概率上升"),
         },
        {"title": "浪3幅度评估",
         "level": ("good" if (w3 / w1) >= 1.618 else ("warn" if (w3 / w1) < 1.0 else "ghost")),
         "text": ("浪3为浪1的%.3f倍（经典1.618），强劲推动、符合主升浪特征，浪5可看激进目标（卖③ %d）" % (w3 / w1, round(_s2))) if (w3 / w1) >= 1.618 else ("浪3仅为浪1的%.3f倍（经典1.618）且未显著放量；浪5目标宜取保守口径（卖① %d），激进目标（卖③ %d）需量能配合确认" % (w3 / w1, round(_s0), round(_s2)))},
        # R50 准确度增强信号（建议2/5/3/1）注入 findings，前端自动渲染（无需改 HTML 结构）
        {"title": "浪⑤终结信号监控", "level": ("warn" if divergence["level"] == "warn" else ("ghost" if divergence["level"] == "none" else "good")),
         "text": ("⚠ 检测到" + divergence["detail"] + ("（价格已临近卖①，浪⑤衰竭风险高）" if divergence["level"] == "warn" else "（关注后续量价配合）") if (divergence["rsi"] or divergence["volume"]) else "近90日无 RSI/量价顶背离，浪⑤未现明显衰竭信号")},
        {"title": "波动率 regime", "level": ("warn" if _vol_bucket == "高" else ("good" if _vol_bucket == "低" else "ghost")),
         "text": "HV20 处五年 %d%% 分位（%s波动区）：区间带宽×%.2f、漂移信任×%.2f，高波动期触达带更宽、趋势更不可靠。" % (hv_pctile, _vol_bucket, _vol_scale, _drift_conf)},
        {"title": "回测样本进度",
         "level": ("good" if bt_stats.get("empiricalActive") else ("warn" if bt_stats.get("coldStart") else "ghost")),
         "text": ("回测实证级已激活：锚点池 %d 个，卖① 实证命中率 %.1f%%、卖③ 实证命中率 %.1f%%；漂移先验与实证按样本量贝叶斯融合。"
                  % (bt_stats.get("empiricalAnchors", 0), _emp_s1v, _emp_s3v))
                  if bt_stats.get("empiricalActive") else
                  (bt_stats.get("empiricalWarn") or
                   "已存档 %d 条目标位、%d 条到观察期；样本≥%d 后「概率」自动升级为回测实证命中率。"
                   % (bt_stats["totalLogged"], bt_stats["totalEvaluated"], bt_stats["minSample"]))},
        # R174：均线密集压力带共振提示（轻量集成，纯展示层，不动概率引擎/买卖框架）
        {"title": "均线密集压力带",
         "level": ("warn" if _below_year else "ghost"),
         "text": ("当前 %s 位于年线 %.2f 之下，浪⑤子浪ⅰ目标 %.2f 与年线/MA60/MA120 重叠于 %.2f–%.2f 压力带；首次冲击宜减仓而非追高，回踩 MA20(%.2f) 反为加仓。"
                   % (r2(last_close), _ma250_v, _sub1, _pb_lo, _pb_hi, _ma20))
                  if _below_year else
                  ("指数已站稳年线 %.2f 之上，均线多头排列，浪⑤子浪ⅰ(%.2f)压力带已消化，上看子浪ⅲ(%.2f)。"
                   % (_ma250_v, _sub1, _sub3))},
    ]

    # ---------- 当前浪型状态（动态，驱动头部徽章）----------
    # 阈值从框架派生（铁律线=trade_plan，浪③顶=浪型常量 w3_hi），避免与 distances/买卖框架双份真值脱节
    _key_line = trade_plan["stopLine"]["price"]   # 3674.40 铁律线
    _prev_high = w3_hi                              # 4258.86 浪③顶（前高）
    _bz_hi = trade_plan["buyZones"][0]["hi"]
    if last_close < _key_line:
        state = {"text": "铁律线跌破 · 数浪证伪 · 转防御", "cls": "danger"}
    elif last_close >= _prev_high:
        state = {"text": "浪④结束 · 浪⑤运行中", "cls": "gold"}
    elif last_close <= _bz_hi:
        state = {"text": "浪④买点区 · 分批低吸", "cls": "ok"}
    else:
        state = {"text": "浪④回调中 · 等待回踩", "cls": "ghost"}

    # ---------- 图3(panel p3)注释：子浪幅度/回撤由 subWavePoints 派生（消除 +993/22%/4258.86 双份真值）----------
    _p3_amp = sub_wave_points[1]["price"] - sub_wave_points[0]["price"]
    _p3_ret = SUB_RET
    _p3_top = next(p for p in sub_wave_points if p["label"].startswith("子浪ⅲ顶"))["price"]
    p3_note = ("浪③内部呈延长结构：子浪ⅰ（+%.0f）→ 子浪ⅱ（回撤%.0f%%，偏浅=强势）→ 子浪ⅲ延伸创新高至 %.2f。"
               "金色虚线为浪③波段斐波那契回撤位，当前回调处于 0.382~0.5 区间。") % (_p3_amp, _p3_ret, _p3_top)

    # R142：记录源 CSV 真实落盘时刻(=数据抓取批次时刻)，供 validate #11 拦截"盘中快照当收盘"。
    # 用 CSV 的 mtime 而非 build 时刻——上次事故是"13:20 盘中抓数、17:20 盘后构建"，若记 build
    # 时刻会漏掉；记 CSV 落盘时刻才能在"盘中抓数后未重跑 step1"时让门禁 FAIL 根防。
    _csv_mtime = os.path.getmtime(os.path.join(BASE, "data", "sh000001.csv"))
    _fetched_at = _dt.datetime.fromtimestamp(_csv_mtime).strftime("%Y-%m-%d %H:%M:%S")
    data = {
        "updated": last_date, "fetchedAt": _fetched_at, "lastClose": last_close, "w2Days": w2_days,
        "kline": kline, "wavePoints": wave_points, "subWavePoints": sub_wave_points,
        "subZigzag": sub_zigzag, "zigzag": st["zigzag"], "signals": st["signals"],
        "fib5y": fib_5y, "fibW3": fib_w3, "targets": targets, "supports": supports,
        "rules": rules, "channel": channel, "ratioCheck": ratio_check,
        "volByWave": vol_by_wave, "volatility": volatility, "volRegime": {"pctile": hv_pctile, "bucket": _vol_bucket, "bandScale": _vol_scale, "driftConf": _drift_conf, "note": "HV20 五年 %d%% 分位(%s波动区)：区间带宽×%.2f、漂移信任×%.2f" % (hv_pctile, _vol_bucket, _vol_scale, _drift_conf)}, "weekly": weekly,
        "tzWaveStart": tz_wave_start, "tzWave3Top": tz_wave3_top,
        "indexCompare": index_compare, "resonance": resonance, "crossMarket": crossMarket, "distances": distances,
        "scenarios": scenarios, "zones": zones, "findings": findings,
        "tradePlan": trade_plan, "state": state, "p3Note": p3_note,
        "subForecast": sub_forecast, "divergence": divergence,
        "indexBase": _norm_base, "tzBaseStart": _tz_base_start, "tzBaseTop": _tz_base_top,
        "calibration": _calib,
        "probCalib": {"edges": _recal_edges, "vals": _recal_vals,
                      "note": "R217 分段校准映射：裸首达概率(0-1)→经验校准概率(0-1)，10等宽桶经验命中率(伪计数收缩)，空桶恒等；修正低概率系统性低估(OOS Brier −27%)。audit50 同表复算。"},
        "spark": kline["close"][-60:], "sparkDates": kline["dates"][-60:],
    }
    # ---------- 预测回测闭环（提升预测准确性地基）----------
    # bt_stats 已在上方区间+概率派生前算好（存档当日 + 重评全部 + 聚合命中率），
    # 此处直接注入 FIB_DATA.backtest，避免重复跑。
    # ---------- 情景自适应切换（随机应变）：按当日收盘自动判定 activeScenario ----------
    # 用户要求：走强势→子浪切强势、走弱势→切弱势、走基准→切基准走势，系统随行情自动应变。
    # 判定基于当日收盘相对两个既有关键位：铁律线 KEY_LINE(3674.40) 与 浪④底 w4_low(3741.11)。
    #   last_close < KEY_LINE          → risk   （跌破铁律，数浪证伪，子浪失效）
    #   KEY_LINE ≤ last_close < w4_low → base   （浪④磨底中，浪⑤未启动，子浪推演待激活）
    #   last_close ≥ w4_low            → strong （浪⑤已启动，展示完整子浪细分）
    if last_close < KEY_LINE:
        _active_scn = "risk"
    elif last_close < w4_low:
        _active_scn = "base"
    else:
        _active_scn = "strong"
    # 基准/风险为「严谨占位」：不杜撰未启动的子浪，仅提供各自走势路径 + 待激活/失效说明；
    # 与子浪图同源的强子浪推演仅在 strong 下生成（审计铁律 子浪ⅴ≡卖① 仅校验 strong subForecast）。
    scenario_switch = {
        "active": _active_scn,
        "lastClose": round(last_close, 2),
        "keyLine": round(KEY_LINE, 2),
        "w4Low": round(w4_low, 2),
        "rules": [
            "收盘 ≥ %.2f（浪④底）→ 强势：浪⑤已启动，展示完整子浪细分" % w4_low,
            "%.2f（铁律）≤ 收盘 < %.2f → 基准：浪④磨底中，子浪推演待激活" % (KEY_LINE, w4_low),
            "收盘 < %.2f（铁律）→ 风险：数浪证伪，子浪失效" % KEY_LINE,
        ],
        "base": {
            "name": scenarios[0]["name"], "color": scenarios[0]["color"],
            "path": scenarios[0]["points"],   # 基准走势线（当前价→磨底3700→未来浪⑤目标），非子浪细分
            "pending": True,
            "note": "当前处于【基准】情景：浪④于 3650–3741 区间磨底（9/30 约 3700），浪⑤尚未启动。"
                    "子浪推演（浪⑤内部 ⅰ-ⅴ 五浪细分）需待浪④完成、浪⑤启动后才激活——"
                    "下方为基准走势路径（非子浪细分），仅供参考。",
        },
        "risk": {
            "name": scenarios[2]["name"], "color": scenarios[2]["color"],
            "path": scenarios[2]["points"],   # 风险下行路径
            "invalid": True,
            "note": "当前处于【风险】情景：收盘有效跌破铁律线 %.2f，浪⑤数浪证伪，子浪推演失效。"
                    "下方为风险下行路径，仅供风控参考，不构成交易依据。" % KEY_LINE,
        },
    }
    data["scenarioSwitch"] = scenario_switch
    data["backtest"] = bt_stats
    # allow_nan=False: 若 data 含 NaN/Inf，json.dumps 直接抛 ValueError(fail-fast)，
    # 避免写出"非标准 JSON"(NaN/Infinity 字面量, JSON.parse 失败)的半截 data.js；
    # 早于下游 validate 闸口暴露坏数据(数据源退化时)。当前字段无 NaN 故正常路径不触发。
    out = "window.FIB_DATA = " + json.dumps(data, ensure_ascii=False, allow_nan=False) + ";\n"
    with open(os.path.join(BASE, "data", "data.js"), "w", encoding="utf-8") as f:
        f.write(out)
    print("data.js v3 生成完成:", last_date, last_close,
          "| HV20:", hv_now, "分位:", hv_pctile, "%",
          "| 周线30W:", weekly["ma30w"], "上方" if weekly["above"] else "下方",
          "| 通道斜率:", round(slope, 4), "/日")


if __name__ == "__main__":
    main()
