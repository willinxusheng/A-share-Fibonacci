# -*- coding: utf-8 -*-
"""R119 常驻审计：sentiment v3 契约深核（contra 单一真值 + forecast 插值一致性）。

覆盖（防回归）：
  A. today.contra.bands 各档 N/fwd20 与独立重算逐位一致（单一真值，防统计口径漂移）
  B. contra 各档 N 合计 = 有未来20日收益的样本数（history - 尾部20日，非全量）
  C. forecast 全部日期落在 subForecast 锚点区间 [首,末]，严格递增
  D. forecast 每个点 label 与 score 分档一致（<20冰点/<40偏冷/<60中性/<80偏热/else狂热）
  E. today.score/label 与 history 末点衔接一致（±0.011）
  F. contra.note 包含 N 合计与分数区间（诚实口径可追溯）

用法：python _audit_sentiment_contract.py  （退出码 0=全过，1=有问题）
"""
import json
import re
import sys
import os
import math

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


def main():
    data = load_js(os.path.join(REPO, "data", "data.js"), "FIB_DATA")
    sent = json.load(open(os.path.join(REPO, "data", "sentiment.json"), encoding="utf-8"))

    # 动态分位标尺（R122a）：contra.bands / forecast label 必须按 scale.bounds 一致分档
    sbounds = (sent.get("scale") or {}).get("bounds") or [20.0, 40.0, 60.0, 80.0]
    chk(len(sbounds) == 4, "sentiment.scale.bounds 格式异常: %r" % sbounds)
    b1, b2, b3, b4 = [float(x) for x in sbounds]

    kd = data["kline"]["dates"]
    kc = data["kline"]["close"]
    hist = sent.get("history") or []
    fcst = sent.get("forecast") or []
    contra = (sent.get("today") or {}).get("contra") or {}
    bands = contra.get("bands") or []

    # ---------- A. contra 单一真值 ----------
    chk(len(bands) == 5, "contra.bands 应为 5 档，实际 %d" % len(bands))
    base = len(kd) - len(hist)
    exp_bands = [("冰点", 0, b1), ("偏冷", b1, b2), ("中性", b2, b3), ("偏热", b3, b4), ("狂热", b4, 101)]
    recomputed = {}
    for lab, lo, hi in exp_bands:
        n, s = 0, 0.0
        for off, h in enumerate(hist):
            if lo <= h["score"] < hi:
                j = base + off + 20
                if j < len(kc):
                    n += 1
                    s += (kc[j] / kc[base + off] - 1.0) * 100.0
        recomputed[lab] = {"n": n, "fwd20": round(s / n, 2) if n else None}
    for b in bands:
        lab = b.get("label")
        exp = recomputed.get(lab, {})
        n_ok = b.get("n") == exp.get("n")
        f_ok = (b.get("fwd20") is None and exp.get("fwd20") is None) or \
               abs((b.get("fwd20") or 0) - (exp.get("fwd20") or 0)) < 0.011
        chk(n_ok and f_ok, "contra[%s] 不一致: 存储 N=%s/fwd20=%s vs 独立 N=%s/fwd20=%s"
            % (lab, b.get("n"), b.get("fwd20"), exp.get("n"), exp.get("fwd20")))

    # ---------- A2. contra.split 分态单一真值（R121） ----------
    split = contra.get("split") or {}
    regime = contra.get("regime")
    chk(regime in ("bear", "bull"), "contra.regime 应为 bear/bull，实际 %r" % regime)
    chk(set(split.keys()) == {"bear", "bull"}, "contra.split 应含 bear/bull 两态")
    ma250_arr = data["kline"].get("ma250") or []
    for state in ("bear", "bull"):
        sb = (split.get(state) or {}).get("bands") or []
        chk(len(sb) == 5, "contra.split.%s.bands 应为 5 档，实际 %d" % (state, len(sb)))
        for b in sb:
            lab = b.get("label")
            lo, hi = next(((lo, hi) for (l, lo, hi) in exp_bands if l == lab), (None, None))
            n, s = 0, 0.0
            for off, h in enumerate(hist):
                i = base + off
                if lo <= h["score"] < hi and i < len(ma250_arr) and ma250_arr[i] is not None:
                    pred = kc[i] < ma250_arr[i] if state == "bear" else kc[i] > ma250_arr[i]
                    if pred:
                        j = i + 20
                        if j < len(kc):
                            n += 1
                            s += (kc[j] / kc[i] - 1.0) * 100.0
            n_ok = b.get("n") == n
            exp_fwd = round(s / n, 2) if n else None
            f_ok = (b.get("fwd20") is None and exp_fwd is None) or \
                   abs((b.get("fwd20") or 0) - (exp_fwd or 0)) < 0.011
            chk(n_ok and f_ok, "contra.split.%s[%s] 不一致: 存储 N=%s/fwd20=%s vs 独立 N=%s/fwd20=%s"
                % (state, lab, b.get("n"), b.get("fwd20"), n, exp_fwd))

    # ---------- B. N 合计语义 ----------
    tot_n = sum(b.get("n", 0) for b in bands)
    expect_n = max(len(hist) - 20, 0)  # 尾部 20 日无未来收益
    chk(tot_n == expect_n, "contra 各档 N 合计 %d != 预期 %d（history %d - 尾部20）"
        % (tot_n, expect_n, len(hist)))

    # ---------- C. forecast 日期范围 ----------
    sf_pts = (data.get("subForecast") or {}).get("points") or []
    chk(len(sf_pts) >= 2, "subForecast.points 不足 2 锚点")
    if sf_pts:
        sf_first, sf_last = sf_pts[0]["date"], sf_pts[-1]["date"]
        fcst_dates = [f["date"] for f in fcst]
        chk(all(sf_first <= d <= sf_last for d in fcst_dates),
            "forecast %d 个日期超出锚点区间 [%s,%s]: %r" % (
                len([d for d in fcst_dates if not (sf_first <= d <= sf_last)]),
                sf_first, sf_last,
                [d for d in fcst_dates if not (sf_first <= d <= sf_last)][:5]))
        chk(all(b > a for a, b in zip(fcst_dates, fcst_dates[1:])), "forecast 日期非严格递增")

    # ---------- D. forecast label 分档一致性（动态标尺，R122a） ----------
    def label_of(sc):
        for hi, lab in [(b1, "冰点"), (b2, "偏冷"), (b3, "中性"), (b4, "偏热")]:
            if sc < hi:
                return lab
        return "狂热"
    bad_lbl = [f for f in fcst if f.get("label") != label_of(f.get("score"))]
    chk(not bad_lbl, "forecast %d 个点 label 与 score 分档不符: %r" % (len(bad_lbl), bad_lbl[:3]))

    # ---------- E. today 与 history 末点衔接 + R123 Δ/z 一致（单一真值） ----------
    if hist:
        last_h = hist[-1]
        t = sent.get("today") or {}
        chk(abs((t.get("score") or 0) - last_h["score"]) <= 0.011,
            "today score %s 与 history 末点 %s 不一致" % (t.get("score"), last_h["score"]))
        chk(t.get("label") == last_h["label"],
            "today label %s 与 history 末点 %s 不一致" % (t.get("label"), last_h["label"]))
        _sc = t.get("sentimentChange") or {}
        chk(_sc.get("d20") == last_h.get("d20"),
            "today.sentimentChange.d20(%r) != history 末点 d20(%r)" % (_sc.get("d20"), last_h.get("d20")))
        chk(t.get("zscore") == last_h.get("z"),
            "today.zscore(%r) != history 末点 z(%r)" % (t.get("zscore"), last_h.get("z")))
    # ---------- E3. R123：contra.delta + regimeSummary 存在且结构合理 ----------
    _delta = (contra or {}).get("delta") or {}
    chk("rising" in _delta and "falling" in _delta, "contra.delta 缺 rising/falling(R123)")
    _rs_str = contra.get("regimeSummary")
    chk(isinstance(_rs_str, str) and len(_rs_str) > 0, "contra.regimeSummary 缺失或空(R123)")

    # ---------- F. contra.note 诚实口径 ----------
    note = contra.get("note") or ""
    chk(str(tot_n) in note, "contra.note 未含 N 合计 %d（口径不可追溯）" % tot_n)
    if hist:
        sc_min = min(h["score"] for h in hist)
        sc_max = max(h["score"] for h in hist)
        chk(("%.1f~%.1f" % (sc_min, sc_max)) in note, "contra.note 未含分数区间 %.1f~%.1f" % (sc_min, sc_max))

    # ---------- G. R124 预测力增强：独立重算一致性（单一真值，防回归） ----------
    if hist:
        # G1. stateSignal：由 today.score / today.sentimentChange.d20 派生的 level/dir 与值须与 delta.quadrants 一致
        _st = contra.get("stateSignal") or {}
        sc_now = (sent.get("today") or {}).get("score")
        d20_now = (sent.get("today") or {}).get("sentimentChange", {}).get("d20")
        _lv = "高" if (sc_now or 0) >= 60 else ("低" if (sc_now or 0) < 40 else "中")
        _dr = "升" if (d20_now or 0) >= 0 else "降"
        chk(_st.get("level") == _lv and _st.get("dir") == _dr,
            "R124 stateSignal level/dir 派生不一致: 存储(%s,%s) vs 派生(%s,%s)"
            % (_st.get("level"), _st.get("dir"), _lv, _dr))
        _qd = {(q.get("level"), q.get("dir")): q for q in (_delta.get("quadrants") or [])}
        _q = _qd.get((_lv, _dr))
        if _q:
            chk(_st.get("n") == _q.get("n")
                and abs((_st.get("fwd20") or 0) - (_q.get("fwd20") or 0)) < 0.011
                and abs((_st.get("posPct") or 0) - (_q.get("pos") or 0)) < 0.011,
                "R124 stateSignal 与 delta.quadrants 不一致")
        # G2. recency：独立重算 等权/衰减加权（与生成器同口径）
        _rc = contra.get("recency") or {}
        _band = (sent.get("today") or {}).get("label")
        _win = hist[-250:] if len(hist) >= 250 else hist
        _lo = len(hist) - len(_win)
        _pairs = []
        for off, h in enumerate(hist):
            if off < _lo or h.get("label") != _band:
                continue
            i = base + off
            j = i + 20
            if j < len(kc):
                _pairs.append((len(hist) - 1 - off, (kc[j] / kc[i] - 1.0) * 100.0))
        if _pairs:
            _n = len(_pairs)
            _eq = sum(f for _, f in _pairs) / _n
            _tau = 108.0
            _w = [math.exp(-a / _tau) for a, _ in _pairs]
            _dw = sum(wk * f for (a, f), wk in zip(_pairs, _w)) / sum(_w)
            chk(abs((_rc.get("equalWtd") or 0) - round(_eq, 2)) < 0.011,
                "R124 recency.equalWtd 独立重算不一致: 存储%s vs 重算%.2f" % (_rc.get("equalWtd"), _eq))
            chk(abs((_rc.get("decayWtd") or 0) - round(_dw, 2)) < 0.011,
                "R124 recency.decayWtd 独立重算不一致: 存储%s vs 重算%.2f" % (_rc.get("decayWtd"), _dw))
            chk(_rc.get("drift") == (abs(round(_dw, 2) - round(_eq, 2)) >= 1.0),
                "R124 recency.drift 标志与背离判定不一致")
        # G3. horizonScan：独立重算最优窗口（与生成器同口径）
        _hs = contra.get("horizonScan") or {}
        _scores = [h["score"] for h in hist]
        _med = sorted(_scores)[len(_scores) // 2]
        _opt = None
        for H in (5, 10, 20, 40, 60):
            _cn = _cs = _hn = _hs2 = 0.0
            for off, h in enumerate(hist):
                i = base + off
                j = i + H
                if j < len(kc):
                    f = (kc[j] / kc[i] - 1.0) * 100.0
                    if h["score"] < _med:
                        _cn += 1
                        _cs += f
                    else:
                        _hn += 1
                        _hs2 += f
            if _cn >= 20 and _hn >= 20:
                _cmean = round(_cs / _cn, 2) if _cn else None
                _hmean = round(_hs2 / _hn, 2) if _hn else None
                _sp = round(_cmean - _hmean, 2) if (_cmean is not None and _hmean is not None) else None
                if _sp is not None and (_opt is None or abs(_sp) > abs(_opt[1])):
                    _opt = (H, _sp)
        _optH = _opt[0] if _opt else None
        chk(_hs.get("optimalHorizon") == _optH,
            "R124 horizonScan.optimalHorizon 独立重算不一致: 存储%s vs 重算%s" % (_hs.get("optimalHorizon"), _optH))

        # ---------- G4. R125 extremeReversal 单一真值（current 由 today.score 与 bounds 派生） ----------
        _er = contra.get("extremeReversal") or {}
        _erb = _er.get("bounds") or [20.0, 80.0]
        _er_sc = sc_now if sc_now is not None else None
        _exp_cur = "panic" if (_er_sc is not None and _er_sc < _erb[0]) else (
            "euphoria" if (_er_sc is not None and _er_sc > _erb[1]) else None)
        chk(_er.get("current") == _exp_cur,
            "R125 extremeReversal.current 派生不一致: 存储%s vs 派生%s (score=%s,bounds=%s)"
            % (_er.get("current"), _exp_cur, sc_now, _erb))
        chk(abs(_erb[0] - b1) < 0.011 and abs(_erb[1] - b4) < 0.011,
            "R125 extremeReversal.bounds 与 scale.bounds 不一致: %r vs %r" % (_erb, sbounds))

        # ---------- G5. R125 consensus 独立重算（各窗口 spread 方向与 verdict） ----------
        _cs = _hs.get("consensus") or {}
        _scores = [h["score"] for h in hist]
        _med = sorted(_scores)[len(_scores) // 2]
        _dirs = []
        for H in (5, 10, 20, 40, 60):
            _cc = _cs2 = _hh = _hm = 0.0
            for off, h in enumerate(hist):
                i = base + off
                j = i + H
                if j < len(kc):
                    f = (kc[j] / kc[i] - 1.0) * 100.0
                    if h["score"] < _med:
                        _cc += 1; _cs2 += f
                    else:
                        _hh += 1; _hm += f
            if _cc and _hh:
                _dirs.append((_cs2 / _cc - _hm / _hh) >= 0)
        _pos = sum(1 for d in _dirs if d); _neg = len(_dirs) - _pos
        if _dirs:
            _exp_v = "共振(逆势有效)" if _pos == len(_dirs) else (
                "共振(顺势有效)" if _neg == len(_dirs) else "背离(信号分裂)")
            chk(_cs.get("total") == len(_dirs), "R125 consensus.total 不一致: 存储%s vs 重算%s" % (_cs.get("total"), len(_dirs)))
            chk(_cs.get("verdict") == _exp_v,
                "R125 consensus.verdict 独立重算不一致: 存储%s vs 重算%s" % (_cs.get("verdict"), _exp_v))
            chk(_cs.get("agree") == max(_pos, _neg), "R125 consensus.agree 不一致")

        # ---------- G6. R125 regimeWin 独立重算（当前 regime 下 level×dir 组合经验涨概率） ----------
        _st = contra.get("stateSignal") or {}
        _rw = _st.get("regimeWin")
        if _rw is not None:
            _regime = _rw.get("regime")
            _lv = "高" if (sc_now or 0) >= 60 else ("低" if (sc_now or 0) < 40 else "中")
            _d20n = (sent.get("today") or {}).get("sentimentChange", {}).get("d20")
            _dr = "升" if (_d20n or 0) >= 0 else "降"
            _rn = _rs = _rup = 0
            for off, h in enumerate(hist):
                _d = h.get("d20")
                if _d is None:
                    continue
                i = base + off
                if i >= len(ma250_arr) or ma250_arr[i] is None:
                    continue
                _isbear = kc[i] < ma250_arr[i]
                if (_regime == "bear") != _isbear:
                    continue
                _lvl = "高" if h["score"] >= 60 else ("低" if h["score"] < 40 else "中")
                if _lvl != _lv:
                    continue
                _dd = "升" if (_d or 0) >= 0 else "降"
                if _dd != _dr:
                    continue
                j = i + 20
                if j >= len(kc):
                    continue
                _f = (kc[j] / kc[i] - 1.0) * 100.0
                _rn += 1; _rs += _f; _rup += 1 if _f >= 0 else 0
            if _rn:
                _exp_pct = round(_rup / _rn, 2)
                _exp_fwd = round(_rs / _rn, 2)
                chk(_rw.get("n") == _rn, "R125 regimeWin.n 不一致: 存储%s vs 重算%s" % (_rw.get("n"), _rn))
                chk(abs((_rw.get("posPct") or 0) - _exp_pct) < 0.011,
                    "R125 regimeWin.posPct 不一致: 存储%s vs 重算%s" % (_rw.get("posPct"), _exp_pct))
                chk(abs((_rw.get("fwd20") or 0) - _exp_fwd) < 0.011,
                    "R125 regimeWin.fwd20 不一致: 存储%s vs 重算%s" % (_rw.get("fwd20"), _exp_fwd))

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
