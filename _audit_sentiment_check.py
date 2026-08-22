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
print("===== 问题清单 (%d) =====" % len(problems))
for p in problems:
    print("  X %s" % p)
if not problems:
    print("  OK 全部契约项通过")
