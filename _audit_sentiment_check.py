# -*- coding: utf-8 -*-
"""情绪数据 v 3 契约实证审计（R110 后深度挖掘）。仅标准库。"""
import json
import os
import sys
import datetime
import re

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
from gen_sentiment import _load_data, _label, _is_a_share_trading_day

SENT = os.path.join(REPO, "data", "sentiment.json")

problems = []

def chk(cond, msg):
    if not cond:
        problems.append(msg)

with open(SENT, encoding="utf-8") as f:
    S = json.load(f)

# 动态分位标尺（R122a）：today/history/forecast 的 label 必须按 scale.bounds 一致分档
_scale = S.get("scale") or {}
_bounds = _scale.get("bounds") or [20.0, 40.0, 60.0, 80.0]
chk(isinstance(_bounds, list) and len(_bounds) == 4, "scale.bounds 缺失或格式异常: %r" % _scale.get("bounds"))

# 1. schema
chk(S.get("schema") == "a-share-fib-sentiment/v3", "schema 不是 v3: %s" % S.get("schema"))

# 2. today
today = S.get("today") or {}
ts = today.get("score")
chk(ts is not None and 0 <= ts <= 100, "today.score 越界或缺失: %r" % ts)
if ts is not None:
    chk(today.get("label") == _label(ts, _bounds), "today.label 与 score 分档不一致(动态标尺): %s vs %s" % (today.get("label"), _label(ts, _bounds)))
    chk(isinstance(today.get("dims"), list) and len(today["dims"]) == 5, "today.dims 维度数异常: %r" % today.get("dims"))
    sc = today.get("sentimentChange") or {}
    chk("d5" in sc and "d20" in sc, "today.sentimentChange 缺 d5/d20(R123)")
    chk("zscore" in today, "today 缺 zscore 字段(R123)")

# 3. history
hist = S.get("history") or []
chk(len(hist) > 0, "history 为空")
dates_h = [h["date"] for h in hist]
for h in hist:
    for k in ("date", "score", "label"):
        chk(h.get(k) is not None, "history 缺字段 %s: %r" % (k, h))
    for kk in ("d5", "d20", "z"):
        chk(kk in h, "history 缺派生字段 %s(R123): %r" % (kk, h))
    if h.get("score") is not None:
        chk(0 <= h["score"] <= 100, "history score 越界: %r" % h)
        chk(h.get("label") == _label(h["score"], _bounds), "history label 与 score 不一致(动态标尺): %r" % h)
chk(all(re.match(r"^\d{4}-\d{2}-\d{2}$", d) for d in dates_h), "history 日期格式异常")
gaps = []
for i in range(1, len(dates_h)):
    a = datetime.datetime.strptime(dates_h[i-1], "%Y-%m-%d")
    b = datetime.datetime.strptime(dates_h[i], "%Y-%m-%d")
    delta = (b - a).days
    if delta < 1:
        gaps.append((dates_h[i-1], dates_h[i], "倒序/重复"))
    elif delta > 7:
        gaps.append((dates_h[i-1], dates_h[i], "缺口%d天(疑似异常)" % delta))
if any(g[2].startswith("倒序") for g in gaps):
    problems.append("history 连续性异常(倒序/重复): %r" % [g for g in gaps if g[2].startswith("倒序")])

# 4. forecast（核心：是否含周末/非交易日；补班日虽在周末仍交易，须与生成器同源口径）
fcst = S.get("forecast") or []
chk(len(fcst) > 0, "forecast 为空")
dates_f = [c["date"] for c in fcst]
bad_fcst_day = [d for d in dates_f if not _is_a_share_trading_day(d)]
chk(len(bad_fcst_day) == 0, "forecast 含 %d 个非交易日(周末/长假,补班日除外): %r" % (len(bad_fcst_day), bad_fcst_day[:10]))
for c in fcst:
    for k in ("date", "score", "label"):
        chk(c.get(k) is not None, "forecast 缺字段 %s: %r" % (k, c))
    if c.get("score") is not None:
        chk(0 <= c["score"] <= 100, "forecast score 越界: %r" % c)
        chk(c.get("label") == _label(c["score"], _bounds), "forecast label 与 score 不一致(动态标尺): %r" % c)

# 4b. #666 预测置信带：forecast 点须含 lo/hi 且 lo<=score<=hi；顶层须有 forecastBand 元信息
chk("forecastBand" in S, "#666 forecastBand 元信息缺失")
_fb = S.get("forecastBand") or {}
if _fb:
    for _kk in ("method", "level", "baseStd", "baseStdGlobal", "baseStdRecent60", "regime", "regimeMult", "horizonScale", "revertCenter", "revertTau", "revertCap", "regimeBiasCenter", "regimeBiasW", "extremeBiasW", "extremeBiasMethod", "revertTauEff", "driftMult", "stateBiasW", "stateBiasMethod", "momWinEff", "maRevertTau", "pathVolMultMax", "pathVolMethod", "consensusConf", "confMult", "inertiaTau", "inertiaMaxPts", "inertiaMethod", "verdict", "verdictMult", "regimeWinN", "posPctUsed"):
        chk(_kk in _fb, "#666 forecastBand 缺字段 %s" % _kk)
    chk(_fb.get("regime") in ("bear", "bull"), "#666 forecastBand.regime 异常: %r" % _fb.get("regime"))
    # R129 方向修正守门：权重须∈[0,1]、分regime中枢(若有)须∈[0,100]，防过拟合/过度修正
    if _fb.get("regimeBiasW") is not None:
        chk(0.0 <= _fb["regimeBiasW"] <= 1.0, "R129 regimeBiasW 越界[0,1]: %r" % _fb.get("regimeBiasW"))
    if _fb.get("extremeBiasW") is not None:
        chk(0.0 <= _fb["extremeBiasW"] <= 1.0, "R129 extremeBiasW 越界[0,1]: %r" % _fb.get("extremeBiasW"))
    if _fb.get("regimeBiasCenter") is not None:
        chk(0.0 <= _fb["regimeBiasCenter"] <= 100.0, "R129 regimeBiasCenter 越界[0,100]: %r" % _fb.get("regimeBiasCenter"))
    # R130 预测自适应增强守门：数据驱动时标∈[10,80]、漂移带宽倍率∈[1,1.5]、漂移期回归上限∈[0.3,0.5]、
    # 状态偏置权重∈[0,1]、方法说明非空——防超参越界/过度修正
    if _fb.get("revertTauEff") is not None:
        chk(10.0 <= _fb["revertTauEff"] <= 80.0, "R130 revertTauEff 越界[10,80]: %r" % _fb.get("revertTauEff"))
    if _fb.get("driftMult") is not None:
        chk(1.0 <= _fb["driftMult"] <= 1.5, "R130 driftMult 越界[1,1.5]: %r" % _fb.get("driftMult"))
    if _fb.get("revertCap") is not None:
        chk(0.3 <= _fb["revertCap"] <= 0.5, "R130 revertCap(漂移自适应) 越界[0.3,0.5]: %r" % _fb.get("revertCap"))
    if _fb.get("stateBiasW") is not None:
        chk(0.0 <= _fb["stateBiasW"] <= 1.0, "R130 stateBiasW 越界[0,1]: %r" % _fb.get("stateBiasW"))
    chk(isinstance(_fb.get("stateBiasMethod"), str) and len(_fb.get("stateBiasMethod") or "") > 0,
        "R130 stateBiasMethod 缺失或非字符串")
    # R131 路径派生诚实化守门：动量窗口∈[5,60]、均线回归时标∈[30,120]、路径波动上限∈[1,2]、方法说明非空
    if _fb.get("momWinEff") is not None:
        chk(5 <= _fb["momWinEff"] <= 60, "R131 momWinEff 越界[5,60]: %r" % _fb.get("momWinEff"))
    if _fb.get("maRevertTau") is not None:
        chk(30.0 <= _fb["maRevertTau"] <= 120.0, "R131 maRevertTau 越界[30,120]: %r" % _fb.get("maRevertTau"))
    if _fb.get("pathVolMultMax") is not None:
        chk(1.0 <= _fb["pathVolMultMax"] <= 2.0, "R131 pathVolMultMax 越界[1,2]: %r" % _fb.get("pathVolMultMax"))
    chk(isinstance(_fb.get("pathVolMethod"), str) and len(_fb.get("pathVolMethod") or "") > 0,
        "R131 pathVolMethod 缺失或非字符串")
    # R132 经验信号调制守门：共识置信度∈[0,1]、调制倍率∈[0.5,1.5]、惯性时标∈[5,40]、
    # 惯性最大偏置∈[1,15]、方法说明非空——防超参越界/过度调制
    if _fb.get("consensusConf") is not None:
        chk(0.0 <= _fb["consensusConf"] <= 1.0, "R132 consensusConf 越界[0,1]: %r" % _fb.get("consensusConf"))
    if _fb.get("confMult") is not None:
        chk(0.5 <= _fb["confMult"] <= 1.5, "R132 confMult 越界[0.5,1.5]: %r" % _fb.get("confMult"))
    if _fb.get("inertiaTau") is not None:
        chk(5.0 <= _fb["inertiaTau"] <= 40.0, "R132 inertiaTau 越界[5,40]: %r" % _fb.get("inertiaTau"))
    if _fb.get("inertiaMaxPts") is not None:
        chk(1.0 <= _fb["inertiaMaxPts"] <= 15.0, "R132 inertiaMaxPts 越界[1,15]: %r" % _fb.get("inertiaMaxPts"))
    chk(isinstance(_fb.get("inertiaMethod"), str) and len(_fb.get("inertiaMethod") or "") > 0,
        "R132 inertiaMethod 缺失或非字符串")
    # R133 信号方向调制守门：verdict 须为合法三值之一或 None、调制倍率∈[0.5,1]、regimeWinN 非负 int 或 None、
    # posPctUsed∈[0,1]——防方向调制越界/口径漂移
    if _fb.get("verdict") is not None:
        chk(_fb["verdict"] in ("共振(逆势有效)", "共振(顺势有效)", "背离(信号分裂)"),
            "R133 verdict 异常: %r" % _fb.get("verdict"))
    if _fb.get("verdictMult") is not None:
        chk(0.5 <= _fb["verdictMult"] <= 1.0, "R133 verdictMult 越界[0.5,1]: %r" % _fb.get("verdictMult"))
    if _fb.get("regimeWinN") is not None:
        chk(isinstance(_fb["regimeWinN"], int) and _fb["regimeWinN"] >= 0,
            "R133 regimeWinN 须为非负 int: %r" % _fb.get("regimeWinN"))
    if _fb.get("posPctUsed") is not None:
        chk(0.0 <= _fb["posPctUsed"] <= 1.0, "R133 posPctUsed 越界[0,1]: %r" % _fb.get("posPctUsed"))
_missing_band = [c for c in fcst if c.get("lo") is None or c.get("hi") is None]
chk(len(_missing_band) == 0, "#666 forecast 有 %d 点缺 lo/hi 置信带" % len(_missing_band))
_bad_band = [c for c in fcst if c.get("lo") is not None and c.get("hi") is not None
             and not (c["lo"] <= c["score"] <= c["hi"])]
chk(len(_bad_band) == 0, "#666 forecast 有 %d 点 lo/score/hi 不满足 lo<=score<=hi" % len(_bad_band))

# 5. 今日点衔接：history 末点日期 == data.js kline 末根
data = _load_data()
kdates = [str(x) for x in ((data.get("kline") or {}).get("dates") or [])]
last_k = kdates[-1] if kdates else None
last_h = dates_h[-1] if dates_h else None
chk(last_h == last_k, "history 末点(%s) != data.js kline 末根(%s)" % (last_h, last_k))

# 6. forecast 首点 vs history 末点 score 跳变
jump = None
if hist and fcst:
    jump = abs(fcst[0]["score"] - hist[-1]["score"])
    chk(jump <= 8, "forecast 首点(%s,%s) 与 history 末点(%s,%s) score 跳变 %.1f" % (
        fcst[0]["date"], fcst[0]["score"], hist[-1]["date"], hist[-1]["score"], jump))

# 7. 重复日期
chk(len(set(dates_f)) == len(dates_f), "forecast 存在重复日期")
chk(len(set(dates_h)) == len(dates_h), "history 存在重复日期")

# 7b. R124 预测力增强字段结构审计（contra 下独立诊断字段，不改 dims/今日展示）
contra = today.get("contra") or {}
if contra:
    hs = contra.get("horizonScan") or {}
    chk(hs.get("optimalHorizon") in (5, 10, 20, 40, 60), "R124 horizonScan.optimalHorizon 异常: %r" % hs.get("optimalHorizon"))
    chk(isinstance(hs.get("horizons"), list) and len(hs.get("horizons") or []) == 5, "R124 horizonScan.horizons 应为5个窗口")
    st = contra.get("stateSignal") or {}
    chk(st.get("n") is not None and st.get("fwd20") is not None and st.get("posPct") is not None,
        "R124 stateSignal 缺 n/fwd20/posPct")
    if st.get("posPct") is not None:
        chk(0 <= st["posPct"] <= 1, "R124 stateSignal.posPct 超出[0,1]: %r" % st.get("posPct"))
    rc = contra.get("recency") or {}
    chk(rc.get("equalWtd") is not None and rc.get("decayWtd") is not None and isinstance(rc.get("drift"), bool),
        "R124 recency 缺 equalWtd/decayWtd/drift")

# 7c. R125 预测力再增强字段结构审计（contra 下独立诊断字段，不改 dims/今日展示）
er = contra.get("extremeReversal") or {}
if er:
    chk(er.get("current") in (None, "panic", "euphoria"), "R125 extremeReversal.current 异常: %r" % er.get("current"))
    for _kk in ("panic", "euphoria"):
        _row = er.get(_kk) or {}
        for _nn in ("rev5", "rev20", "rev60"):
            _v = _row.get(_nn)
            chk(_v is None or (0 <= _v <= 1), "R125 extremeReversal.%s.%s 越界: %r" % (_kk, _nn, _v))
    chk(isinstance(er.get("bounds"), list) and len(er.get("bounds") or []) == 2, "R125 extremeReversal.bounds 异常")
hs2 = contra.get("horizonScan") or {}
_cs = hs2.get("consensus") or {}
if _cs:
    chk(_cs.get("verdict") in ("共振(逆势有效)", "共振(顺势有效)", "背离(信号分裂)"),
        "R125 consensus.verdict 异常: %r" % _cs.get("verdict"))
    chk((_cs.get("agree") or 0) + (_cs.get("split") or 0) == (_cs.get("total") or 0),
        "R125 consensus agree+split != total")
st2 = contra.get("stateSignal") or {}
_rw = st2.get("regimeWin")
if _rw is not None:
    chk(_rw.get("regime") in ("bear", "bull"), "R125 regimeWin.regime 异常: %r" % _rw.get("regime"))
    if _rw.get("posPct") is not None:
        chk(0 <= _rw["posPct"] <= 1, "R125 regimeWin.posPct 越界: %r" % _rw.get("posPct"))

print("===== 情绪 v3 实证审计 =====")
print("schema        = %s" % S.get("schema"))
print("today         = %s (%s)" % (today.get("score"), today.get("label")))
print("history 点数  = %d  末点 %s" % (len(hist), dates_h[-1] if dates_h else None))
print("forecast点数  = %d  首点 %s 末点 %s" % (len(fcst), dates_f[0] if dates_f else None, dates_f[-1] if dates_f else None))
print("forecast非交易日= %d" % len(bad_fcst_day))
if bad_fcst_day:
    print("  bad samples: %s" % bad_fcst_day[:12])
print("history缺口    = %r" % (gaps[:5] if gaps else "无"))
print("data.js末根    = %s" % last_k)
print("forecast首末跳变= %s" % (("%.1f" % jump) if jump is not None else "N/A"))
if contra:
    _hs = contra.get("horizonScan") or {}
    _st = contra.get("stateSignal") or {}
    _rc = contra.get("recency") or {}
    print("R124最优窗口   = %s" % (_hs.get("optimalHorizon")))
    print("R124组合状态   = %s%s (N=%s, fwd20=%s, pos=%s)" % (
        _st.get("level"), _st.get("dir"), _st.get("n"), _st.get("fwd20"), _st.get("posPct")))
    print("R124稳健对照   = 等权%s vs 衰减%s drift=%s" % (_rc.get("equalWtd"), _rc.get("decayWtd"), _rc.get("drift")))
print("===== 问题清单 (%d) =====" % len(problems))
for p in problems:
    print("  X %s" % p)
if not problems:
    print("  OK 全部契约项通过")
