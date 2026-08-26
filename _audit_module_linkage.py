# -*- coding: utf-8 -*-
"""R120 模块联动性审计：验证 data.js 内部各模块 + 跨文件的派生/同源关系。

联动核对项：
  A. data.js.zigzag/signals == structures.json zigzag/signals（跨文件同源双份）
  B. subWavePoints 与 subZigzag 派生一致（每个子浪点应是 zigzag 的一个极值点）
  C. wavePoints 与 zigzag 联动（每个大浪点应落在 zigzag 极值上，价格吻合）
  D. tradePlan.sellTargets 与 targets 联动（卖档价格 ∈ targets）
  E. tradePlan 与 subForecast 联动（买①/卖① 与 subForecast.points 锚点一致）
  F. targets/supports 与 wavePoints 派生（斐波那契回撤位 = 浪2回撤浪1等）
  G. weekly 周线 close 与日线聚合一致（每周最后一根 close）
  H. spark/sparkDates 与 kline 联动（近 N 根收盘对齐）
  I. sentiment 广度维度与 resonance/crossMarket breadth 联动
  J. subZigzag 与 zigzag 派生（子浪 zigzag 是主 zigzag 的子集或延续）
"""
import json, os, re, sys

REPO = os.path.dirname(os.path.abspath(__file__))

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
    data = load_js(os.path.join(REPO, "data", "data.js"), "FIB_DATA")
    st = load_json(os.path.join(REPO, "data", "structures.json"))
    sent = load_json(os.path.join(REPO, "data", "sentiment.json"))

    kd = data["kline"]["dates"]
    kc = data["kline"]["close"]
    ko = data["kline"]["ohlc"]
    klow = [x[2] for x in ko]
    khigh = [x[3] for x in ko]
    kvol = data["kline"]["volume"]
    kset = {d: i for i, d in enumerate(kd)}

    # ---------- A. data.js vs structures.json 同源双份 ----------
    for field in ("zigzag", "signals"):
        a = data.get(field) or []
        b = st.get(field) or []
        chk(len(a) == len(b), "A: data.js.%s 长度 %d != structures.json.%s 长度 %d" % (field, len(a), field, len(b)))
        mism = []
        for i, (x, y) in enumerate(zip(a, b)):
            if json.dumps(x, ensure_ascii=False, sort_keys=True) != json.dumps(y, ensure_ascii=False, sort_keys=True):
                mism.append((i, x, y))
        chk(not mism, "A: %s 双份不一致 %d 处: %r" % (field, len(mism), mism[:2]))

    # ---------- B. subWavePoints 与 subZigzag 联动 ----------
    swp = data.get("subWavePoints") or []
    sz = data.get("subZigzag") or []
    chk(len(swp) >= 2, "B: subWavePoints 不足")
    sz_map = {p["date"]: p for p in sz}
    b_bad = []
    for p in swp:
        zp = sz_map.get(p["date"])
        if zp is None:
            b_bad.append((p["date"], p.get("label"), "subZigzag 无此日期"))
        elif abs(zp["price"] - p["price"]) > 0.011:
            b_bad.append((p["date"], p.get("label"), "价格不一致 %s vs %s" % (zp["price"], p["price"])))
    chk(not b_bad, "B: subWavePoints 与 subZigzag 联动 %d 处异常: %r" % (len(b_bad), b_bad[:3]))

    # ---------- C. wavePoints 与 zigzag 联动 ----------
    wp = data.get("wavePoints") or []
    zz = data.get("zigzag") or []
    zz_map = {p["date"]: p for p in zz}
    c_bad = []
    for p in wp:
        zp = zz_map.get(p["date"])
        if zp is None:
            c_bad.append((p["date"], p.get("label"), "zigzag 无此日期"))
        elif abs(zp["price"] - p["price"]) > 0.011:
            c_bad.append((p["date"], p.get("label"), "价格 %s vs %s" % (zp["price"], p["price"])))
    chk(not c_bad, "C: wavePoints 与 zigzag 联动 %d 处异常: %r" % (len(c_bad), c_bad[:3]))

    # ---------- D. tradePlan.sellTargets 与 targets 联动 ----------
    tp = data.get("tradePlan") or {}
    sels = tp.get("sellTargets") or []
    tgts = data.get("targets") or []
    tgt_prices = [t["price"] for t in tgts]
    d_bad = []
    for s in sels:
        if not any(abs(s["price"] - p) < 0.011 for p in tgt_prices):
            d_bad.append((s.get("name"), s["price"]))
    chk(not d_bad, "D: sellTargets 有 %d 个不在 targets 中: %r" % (len(d_bad), d_bad))

    # ---------- E. tradePlan 与 subForecast 锚点联动（卖①=子浪ⅴ 锚点） ----------
    sf = data.get("subForecast") or {}
    sf_pts = sf.get("points") or []
    e_bad = []
    if sels:
        s0 = sels[0]  # 卖①
        for p in sf_pts:
            if p.get("label") == "子浪ⅴ" and abs(p["price"] - s0["price"]) > 0.011:
                e_bad.append(("卖①", s0["price"], p["price"]))
    chk(not e_bad, "E: 卖① 与 subForecast 子浪ⅴ 不一致: %r" % e_bad[:2])

    # ---------- F. targets/supports 与 wavePoints 斐波那契派生（浪3回撤=浪③幅度） ----------
    f_bad = []
    w3_lo = next((p for p in wp if str(p.get("label", "")).startswith("浪③") or str(p.get("label", "")).startswith("浪3起")), None)
    w3_hi = next((p for p in wp if str(p.get("label", "")).startswith("浪3顶") or str(p.get("label", "")).startswith("浪③顶")), None)
    # 兜底：浪③波段 = 浪③起(浪②底) 到 浪③顶；wavePoints 里 label 形如「浪③起/浪③顶」
    if w3_lo is None or w3_hi is None:
        w3_lo = next((p for p in wp if p.get("label") == "浪③起"), None)
        w3_hi = next((p for p in wp if p.get("label") == "浪③顶"), None)
    if w3_lo and w3_hi and w3_lo["price"] and w3_hi["price"]:
        w3 = abs(w3_hi["price"] - w3_lo["price"])  # 浪③幅度
        for s in data.get("supports") or []:
            nm = s.get("name", "")
            m = re.search(r"浪3回撤\s*([\d.]+)%", nm)
            if m and w3 > 0:
                ratio = float(m.group(1)) / 100.0
                expect = w3_hi["price"] - w3 * ratio
                if abs(expect - s["price"]) > 0.011:
                    f_bad.append((nm, s["price"], round(expect, 2)))
    chk(not f_bad, "F: supports 浪3回撤 派生异常: %r" % f_bad[:3])

    # ---------- G. weekly 与 kline 联动（R286 修正：引擎剔除"当前不完整周"） ----------
    # 引擎语义(build_data L428-435)：weekly 取"上一完整周"收盘，正常 ≠ kline 末根
    # （仅当末根恰为周五时才相等）。正确断言：weekly.close == 上一个周五(或最后完整周结束日)收盘。
    wk = data.get("weekly") or {}
    wk_close = wk.get("close")
    wk_ma30 = wk.get("ma30w")
    wk_above = wk.get("above")
    if wk_close is not None and kc:
        import datetime as _dt
        _last = _dt.datetime.strptime(kd[-1], "%Y-%m-%d").date()
        _off = (_last.weekday() - 4) % 7  # 4=Friday → 距上一个周五的天数
        _fri = (_last - _dt.timedelta(days=_off)).strftime("%Y-%m-%d")
        _j = kset.get(_fri)
        if _j is not None:
            chk(abs(wk_close - kc[_j]) < 0.011,
                "G: weekly.close %s != 上一完整周五(%s)收盘 %s" % (wk_close, _fri, kc[_j]))
        else:
            # 周五为休市假日等极端情形：退化为"weekly.close 须是近15根内真实 kline 收盘"
            chk(any(abs(wk_close - c) < 0.011 for c in kc[-15:]),
                "G: weekly.close %s 不是近15根内真实 kline 收盘（数据脱节）" % wk_close)
    if wk_close is not None and wk_ma30 is not None and wk_above is not None:
        chk(wk_above == (wk_close > wk_ma30),
            "G: weekly.above %s != (close>ma30w)=%s (close=%s ma30w=%s)"
            % (wk_above, wk_close > wk_ma30, wk_close, wk_ma30))

    # ---------- H. spark 与 kline 联动 ----------
    sp = data.get("spark") or []
    spd = data.get("sparkDates") or []
    h_bad = []
    if sp and spd:
        n = min(len(sp), len(spd))
        for i in range(n):
            j = kset.get(spd[i])
            if j is not None and abs(sp[i] - kc[j]) > 0.011:
                h_bad.append((spd[i], sp[i], kc[j]))
        chk(not h_bad, "H: spark 与 kline 收盘不一致 %d 处: %r" % (len(h_bad), h_bad[:3]))
        chk(len(sp) >= 50, "H: spark 点数 %d 偏少（预期近60日）" % len(sp))

    # ---------- I. sentiment 广度与 resonance/crossMarket 联动（R122c→R141 同口径） ----------
    dims = (sent.get("today") or {}).get("dims") or []
    br = next((d for d in dims if d.get("name") == "breadth"), None)
    res_b = (data.get("resonance") or {}).get("breadth")
    res_a = (data.get("resonance") or {}).get("breadthAvailable") or 0
    cross_b = (data.get("crossMarket") or {}).get("breadth")
    cross_a = (data.get("crossMarket") or {}).get("breadthAvailable") or 0
    if br is not None and (res_a > 0 or cross_a > 0):
        # R122c：仅用 breadthAvailable>0 的源均值（缺失源不参与平均、全缺失归零）
        # R141：引擎对原始源均值做 (b-0.5)*2 映射到 [-1,1] 信号维度（_breadth_adaptive），
        #       故维度 sub 应等于该映射值，而非原始均值本身（旧校验口径在 R141 后失真）。
        parts, tags = [], []
        if res_a > 0 and res_b is not None:
            parts.append(float(res_b)); tags.append("dom")
        if cross_a > 0 and cross_b is not None:
            parts.append(float(cross_b)); tags.append("cross")
        raw_mean = (sum(parts) / len(parts)) if parts else 0.0
        # 单一真值：直接复用 gen_sentiment._breadth_adaptive（与引擎完全一致，避免双份公式漂移）
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("gen_sentiment", os.path.join(REPO, "gen_sentiment.py"))
        _gs = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_gs)
        expect_b = _gs._breadth_adaptive(raw_mean, tags)
        i_ok = abs(br.get("sub", 0) - expect_b) < 0.011
        chk(i_ok, "I: sentiment breadth sub=%.4f != 可用源映射=%.4f (raw_mean=%.4f, 源=%s，R141 _breadth_adaptive)"
            % (br.get("sub", 0), expect_b, raw_mean, "+".join(tags)))

    # ---------- J. subZigzag 起点 与 wavePoints 浪③起联动（5% 子浪 zigzag 从浪③起开始） ----------
    if sz and wp:
        # 浪③起 = 浪②底（同一根 K 线，label 形如「浪2(回撤xx.x%)」或「浪③起」）
        w3_start = next((p for p in wp if p.get("label") == "浪③起"), None)
        if w3_start is None:
            w3_start = next((p for p in wp if str(p.get("label", "")).startswith("浪2")), None)
        if w3_start:
            j_ok = abs(sz[0]["price"] - w3_start["price"]) < 0.011
            chk(j_ok, "J: subZigzag 起点 %s(%s) != wavePoints 浪③起(%s) %s(%s)"
                % (sz[0]["date"], sz[0]["price"], w3_start.get("label"),
                   w3_start.get("date"), w3_start["price"]))
        else:
            chk(any(abs(sz[0]["price"] - p["price"]) < 0.011 for p in zz[-3:]),
                "J: subZigzag 起点与 zigzag 末段脱节")

    # ---------- K. tradePlan 内部联动（buyZones/stopLine/trailingStop/subRefs） ----------
    k_bad = []
    # 铁律线单一真值：stopLine == wavePoints 浪1顶 == buyZones.lo
    stop_price = (tp.get("stopLine") or {}).get("price")
    if stop_price is not None:
        w1_top = next((p for p in wp if str(p.get("label", "")).startswith("浪1") and p.get("pos") == "top"), None)
        if w1_top and abs(stop_price - w1_top["price"]) > 0.011:
            k_bad.append(("stopLine %s != 浪1顶 %s" % (stop_price, w1_top["price"])))
        for bz in tp.get("buyZones") or []:
            if abs(bz.get("lo", 0) - stop_price) > 0.011:
                k_bad.append(("buyZones.lo %s != stopLine %s" % (bz.get("lo"), stop_price)))
    # buyZones.hi == supports 浪3回撤38.2%（0.382 回撤位）
    if tp.get("buyZones"):
        bz_hi = tp["buyZones"][0].get("hi")
        s382 = next((s for s in data.get("supports") or [] if s.get("name") == "浪3回撤 38.2%"), None)
        if bz_hi is not None and s382 and abs(bz_hi - s382["price"]) > 0.011:
            k_bad.append(("buyZones.hi %s != 浪3回撤38.2% %s" % (bz_hi, s382["price"])))
    # trailingStop.ma20/ma60 == kline 末根 MA
    ts = tp.get("trailingStop") or {}
    ma20_last = next((x for x in reversed(data["kline"].get("ma20") or []) if x is not None), None)
    ma60_last = next((x for x in reversed(data["kline"].get("ma60") or []) if x is not None), None)
    if ts.get("ma20") is not None and ma20_last is not None and abs(ts["ma20"] - ma20_last) > 0.011:
        k_bad.append(("trailingStop.ma20 %s != kline末根MA20 %s" % (ts["ma20"], ma20_last)))
    if ts.get("ma60") is not None and ma60_last is not None and abs(ts["ma60"] - ma60_last) > 0.011:
        k_bad.append(("trailingStop.ma60 %s != kline末根MA60 %s" % (ts["ma60"], ma60_last)))
    # subRefs 价格 ∈ subZigzag 极值点
    sz_map_p = {p["price"] for p in sz}
    for r in tp.get("subRefs") or []:
        if r.get("price") is not None and not any(abs(r["price"] - sp) < 0.011 for sp in sz_map_p):
            k_bad.append(("subRefs %s(%s) 不在 subZigzag 极值中" % (r.get("date"), r.get("price"))))
    chk(not k_bad, "K: tradePlan 内部联动 %d 处异常: %r" % (len(k_bad), k_bad[:4]))

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
