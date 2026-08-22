# -*- coding: utf-8 -*-
"""深度逐点对齐审计（R113）：sentiment/structures/chanlun_view 与主行情 data.js 逐点核验。

覆盖：
  A. sentiment.history 日期序列 == data.js kline.dates 末250根（逐点一致）
  B. sentiment.forecast 每个日期均为 A股交易日（周末剔除+长假剔除+补班保留，与 gen_sentiment 同源口径）
  C. structures.signals 日期 ∈ kline.dates、严格递增、无重复
  D. structures.zigzag 日期 ∈ kline.dates，且 L 点 price==当日 low、H 点 price==当日 high
  E. chanlun_view lastDate/lastClose == data.js 末根
  F. backtest 子浪 n 求和 vs 全局、hitRate == Laplace(hits+1)/(n+2) 复核

用法：python _audit_deep_align.py  （退出码 0=全过，1=有问题）
"""
import datetime
import json
import re
import sys

REPO = r"C:\Users\Administrator\WorkBuddy\2026-08-04-23-16-18\A-share-Fibonacci"

problems = []
checks = []


def chk(cond, msg):
    checks.append((cond, msg))
    if not cond:
        problems.append(msg)


def load_js(path, var):
    raw = open(path, encoding="utf-8").read()
    body = re.sub(r"^\s*window\.%s\s*=\s*" % re.escape(var), "", raw).rstrip().rstrip(";")
    return json.loads(body)


def load_json(path):
    return json.load(open(path, encoding="utf-8"))


def main():
    data = load_js(REPO + r"\data\data.js", "FIB_DATA")
    sent = load_json(REPO + r"\data\sentiment.json")
    struct = load_json(REPO + r"\data\structures.json")
    chanlun = load_js(REPO + r"\data\chanlun_view.js", "CHANLUN_VIEW")
    bt = load_json(REPO + r"\data\backtest.json")

    kd = data["kline"]["dates"]
    kc = data["kline"]["close"]
    ko = data["kline"]["ohlc"]
    klow = [x[2] for x in ko]
    khigh = [x[3] for x in ko]
    kset = set(kd)
    klast = kd[-1]

    # ---------- A. sentiment.history 与 kline 末250根逐点对齐 ----------
    hist = sent.get("history") or []
    chk(len(hist) == 250, "history 点数=%d != 250" % len(hist))
    chk(not hist or all("score" in h and "label" in h for h in hist), "history 缺 score/label 字段")
    tail250 = kd[-250:]
    hist_dates = [h["date"] for h in hist]
    mismatch = [(a, b) for a, b in zip(hist_dates, tail250) if a != b]
    chk(not mismatch, "history 与 kline 末250根日期逐点不一致 %d 处，前3: %r" % (len(mismatch), mismatch[:3]))
    chk(hist_dates[-1] == klast, "history 末点 %s != kline 末根 %s" % (hist_dates[-1], klast))
    chk(hist_dates[0] == tail250[0], "history 首点 %s != kline 倒数第250根 %s" % (hist_dates[0], tail250[0]))
    # history 严格递增、无重复
    chk(all(b > a for a, b in zip(hist_dates, hist_dates[1:])), "history 日期非严格递增")

    # ---------- B. forecast 每日期均为 A股交易日 ----------
    fcst = sent.get("forecast") or []
    chk(len(fcst) > 0, "forecast 为空")
    # 复用 gen_sentiment 同源口径：直接 import（避免双份硬编码）
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_sentiment", REPO + r"\gen_sentiment.py")
    gs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gs)
    bad_fcst = [f["date"] for f in fcst if not gs._is_a_share_trading_day(f["date"])]
    chk(not bad_fcst, "forecast 含 %d 个非交易日: %r" % (len(bad_fcst), bad_fcst[:8]))
    fcst_dates = [f["date"] for f in fcst]
    chk(all(b > a for a, b in zip(fcst_dates, fcst_dates[1:])), "forecast 日期非严格递增")
    # forecast 首点应为 history 末点（今日）之后最近的交易日
    if hist_dates:
        from datetime import timedelta

        _d = datetime.date.fromisoformat(hist_dates[-1]) + timedelta(days=1)
        nxt = None
        for _ in range(20):
            if gs._is_a_share_trading_day(_d.isoformat()):
                nxt = _d.isoformat()
                break
            _d += timedelta(days=1)
        if nxt is not None:
            chk(fcst_dates[0] == nxt, "forecast 首点 %s != 下一交易日 %s" % (fcst_dates[0], nxt))

    # ---------- C. structures.signals 日期合法性 ----------
    sigs = struct.get("signals") or []
    chk(len(sigs) > 0, "structures.signals 为空")
    sig_dates = [s["date"] for s in sigs]
    chk(all(d in kset for d in sig_dates), "signals 含非交易日: %r" % [d for d in sig_dates if d not in kset])
    chk(all(b > a for a, b in zip(sig_dates, sig_dates[1:])), "signals 日期非严格递增")
    chk(all(s["signal"] in (1, -1) for s in sigs), "signals 含非法信号值")

    # ---------- D. structures.zigzag 价格与 kline 逐点吻合 ----------
    zz = struct.get("zigzag") or []
    chk(len(zz) > 0, "structures.zigzag 为空")
    zz_dates = [z["date"] for z in zz]
    chk(all(d in kset for d in zz_dates), "zigzag 含非交易日: %r" % [d for d in zz_dates if d not in kset])
    chk(all(b > a for a, b in zip(zz_dates, zz_dates[1:])), "zigzag 日期非严格递增")
    price_bad = []
    for z in zz:
        i = kd.index(z["date"])
        if z["type"] == "L" and abs(z["price"] - klow[i]) > 0.011:
            price_bad.append((z["date"], "L", z["price"], klow[i]))
        if z["type"] == "H" and abs(z["price"] - khigh[i]) > 0.011:
            price_bad.append((z["date"], "H", z["price"], khigh[i]))
    chk(not price_bad, "zigzag 价格与 kline 当日 low/high 不吻合 %d 处: %r" % (len(price_bad), price_bad[:5]))
    # 末点语义：unconfirmed 运行极值，其日期不必等于末根；但末点之后不得出现
    #   反向突破 8% 阈值的价格（否则该极值早应确认翻转）。同时不得出现更极端价格。
    z_last = zz[-1]
    i_last = kd.index(z_last["date"])
    seg_low = min(klow[i_last + 1:]) if i_last + 1 < len(klow) else None
    seg_high = max(khigh[i_last + 1:]) if i_last + 1 < len(khigh) else None
    if z_last["type"] == "L":
        chk(seg_low is None or seg_low >= z_last["price"] - 0.011,
            "zigzag L 末点 %s 之后出现更低 low %s" % (z_last["date"], seg_low))
        chk(seg_high is None or seg_high < z_last["price"] * 1.08,
            "zigzag L 末点 %s 之后反弹超 8%% 阈值(high=%s) 未确认翻转" % (z_last["date"], seg_high))
    else:
        chk(seg_high is None or seg_high <= z_last["price"] + 0.011,
            "zigzag H 末点 %s 之后出现更高 high %s" % (z_last["date"], seg_high))
        chk(seg_low is None or seg_low > z_last["price"] * 0.92,
            "zigzag H 末点 %s 之后回落超 8%% 阈值(low=%s) 未确认翻转" % (z_last["date"], seg_low))

    # ---------- E. chanlun_view 与 data.js 末根对齐 ----------
    chk(chanlun.get("lastDate") == klast, "chanlun_view.lastDate %s != kline 末根 %s" % (chanlun.get("lastDate"), klast))
    chk(abs((chanlun.get("lastClose") or 0) - data["lastClose"]) < 0.011,
        "chanlun_view.lastClose %s != data.js lastClose %s" % (chanlun.get("lastClose"), data["lastClose"]))

    # ---------- F. backtest 子浪 n/hits 与 Laplace 口径 ----------
    subs = [s for s in (bt.get("summary") or []) if s.get("cat") == "subwave"]
    tot_n = sum(s.get("n", 0) for s in subs)
    tot_hits = sum(s.get("hits", 0) for s in subs)
    realized = bt.get("realizedHitRate")
    chk(tot_n > 0, "backtest 子浪样本合计 n=0")
    lap_all = round((tot_hits + 1.0) / (tot_n + 2.0) * 100, 1)
    if realized is not None:
        chk(abs(realized - lap_all) < 0.6,
            "realizedHitRate %s != 全局Laplace %s (n=%d,hits=%d)" % (realized, lap_all, tot_n, tot_hits))
    hr_bad = []
    for s in subs:
        n, hits = s.get("n", 0), s.get("hits", 0)
        hr = s.get("hitRate")
        if hr is not None and n > 0:
            expect = round((hits + 1.0) / (n + 2.0) * 100, 1)
            if abs(hr - expect) > 0.6:
                hr_bad.append((s.get("wave") or s.get("name"), n, hits, hr, expect))
    chk(not hr_bad, "子浪 hitRate 非 Laplace 口径 %d 处: %r" % (len(hr_bad), hr_bad[:5]))

    # ---------- G. sentiment forecast 与 subForecast 锚点同源 ----------
    sf = data.get("subForecast") or {}
    sf_pts = sf.get("points") or []
    if sf_pts:
        sf_dates = [p["date"] for p in sf_pts]
        sf_first, sf_last = sf_dates[0], sf_dates[-1]
        chk(sf_last == fcst_dates[-1] if fcst_dates else False,
            "sentiment forecast 末点 %s != subForecast 末锚点 %s" % (fcst_dates[-1] if fcst_dates else None, sf_last))
        chk(all(sf_first <= d <= sf_last for d in fcst_dates),
            "forecast 日期超出 subForecast 锚点区间 [%s,%s]: %r" % (sf_first, sf_last,
                                                                 [d for d in fcst_dates if not (sf_first <= d <= sf_last)][:5]))
        sf_prices = [p["price"] for p in sf_pts]
        lo_all = min(p.get("lo", p["price"]) for p in sf_pts)
        hi_all = max(p.get("hi", p["price"]) for p in sf_pts)
        chk(lo_all <= sf_prices[-1] <= hi_all, "subForecast 末锚点价 %s 超出自身 lo/hi [%s,%s]"
            % (sf_prices[-1], lo_all, hi_all))

    # ---------- H. chanlun_view 预测带与 subForecast 价格区间一致 ----------
    kp = chanlun.get("keyProjection") or []
    if kp and sf_pts:
        sf_prices = [p["price"] for p in sf_pts]
        p_lo, p_hi = min(sf_prices), max(sf_prices)
        span = max(p_hi - p_lo, 1e-9)
        kp_bad = []
        for row in kp:
            lo, hi = row.get("lo"), row.get("hi")
            if lo is None or hi is None:
                kp_bad.append((row.get("t"), "缺lo/hi"))
                continue
            if lo > p_hi + 0.01 * span or hi < p_lo - 0.01 * span:
                kp_bad.append((row.get("t"), lo, hi, p_lo, p_hi))
        chk(not kp_bad, "chanlun_view keyProjection 预测带与 subForecast 价格区间冲突 %d 处: %r"
            % (len(kp_bad), kp_bad[:5]))

    # ---------- 汇总 ----------
    print("检查项 %d 条，问题 %d 条" % (len(checks), len(problems)))
    for cond, msg in checks:
        print(("[PASS] " if cond else "[FAIL] ") + msg)
    if problems:
        print("\n共 %d 个问题，退出码 1" % len(problems))
        sys.exit(1)
    print("\n全部通过 ✓")
    sys.exit(0)


if __name__ == "__main__":
    main()
