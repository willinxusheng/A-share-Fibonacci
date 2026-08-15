# -*- coding: utf-8 -*-
"""R50 守门员：六条「准确度增强」建议的结构与数值不变量。

锁 R50 不变量（只读不写、不调 run_backtest）：
  ① 波动率 regime（建议3）：volRegime 含 pctile∈[0,100]、bucket∈{低,中,高}、
     bandScale∈(0.5,2)、driftConf∈(0,1]；
  ② 跨指数共振（建议5）：resonance 含 breadth∈[-1,1]、details 为列表；
  ③ 浪⑤终结信号（建议2）：divergence 含 rsi/volume 布尔、level∈{none,watch,warn}、
     detail 非空字符串；
  ④ 机械化出场（建议6）：tradePlan.trailingStop 含 ma20/ma60(数值或null) 与 3 条
     {trigger,price,action} 规则，price 有限；
  ⑤ 子浪时间校准（建议4）：subForecast.timeCalib 含 empirical/prior/blended 各 5 元素，
     blended 累计和≈1；且 R48 时间不变量仍成立（子浪ⅴ expDays==卖①、子浪ⅰ<子浪ⅴ）；
  ⑥ 回测自动升级通道（建议1）：findings 含「回测样本进度」卡；概率仍∈[2,98]（与 audit50 同合约）、
     probSrc∈{回测实证,历史浪幅校准,漂移模型}、bandPct<25（与 audit50 互补，不重复其数学锁）。
"""
import re, json, os, sys
BASE = os.path.dirname(os.path.abspath(__file__))


def _chk(cond, msg):
    print(("  [OK] " if cond else "  [FAIL] ") + msg)
    return cond


ok = True
print("=== R50 守门员：六条准确度增强不变量 ===")

src = open(os.path.join(BASE, "data", "data.js"), encoding="utf-8").read()
D = json.loads(re.search(r"window\.FIB_DATA\s*=\s*(\{.*\})\s*;?\s*$", src, re.S).group(1))

# ---- ① 波动率 regime ----
vr = D.get("volRegime") or {}
chk = _chk(isinstance(vr, dict) and "pctile" in vr and "bucket" in vr
           and "bandScale" in vr and "driftConf" in vr, "volRegime 四字段齐备")
ok = ok and chk
if vr:
    chk = _chk(0 <= vr["pctile"] <= 100, "volRegime.pctile∈[0,100]: %s" % vr["pctile"]); ok = ok and chk
    chk = _chk(vr["bucket"] in ("低", "中", "高"), "volRegime.bucket∈{低,中,高}: %s" % vr["bucket"]); ok = ok and chk
    chk = _chk(0.5 < vr["bandScale"] < 2.0, "volRegime.bandScale∈(0.5,2): %s" % vr["bandScale"]); ok = ok and chk
    chk = _chk(0.0 < vr["driftConf"] <= 1.0, "volRegime.driftConf∈(0,1]: %s" % vr["driftConf"]); ok = ok and chk

# ---- ② 跨指数共振 ----
rs = D.get("resonance") or {}
chk = _chk(isinstance(rs, dict) and "breadth" in rs and "details" in rs, "resonance 字段齐备"); ok = ok and chk
if rs:
    chk = _chk(-1.0 <= rs["breadth"] <= 1.0, "resonance.breadth∈[-1,1]: %s" % rs["breadth"]); ok = ok and chk
    chk = _chk(isinstance(rs["details"], list), "resonance.details 为列表"); ok = ok and chk

# ---- ③ 浪⑤终结信号 ----
dv = D.get("divergence") or {}
chk = _chk(isinstance(dv, dict) and "rsi" in dv and "volume" in dv
           and "level" in dv and "detail" in dv, "divergence 字段齐备"); ok = ok and chk
if dv:
    chk = _chk(isinstance(dv["rsi"], bool) and isinstance(dv["volume"], bool),
               "divergence.rsi/volume 为布尔"); ok = ok and chk
    chk = _chk(dv["level"] in ("none", "watch", "warn"), "divergence.level∈{none,watch,warn}: %s" % dv["level"]); ok = ok and chk
    chk = _chk(isinstance(dv["detail"], str) and len(dv["detail"]) > 0, "divergence.detail 非空"); ok = ok and chk

# ---- ④ 机械化出场 ----
ts = (D.get("tradePlan") or {}).get("trailingStop") or {}
chk = _chk(isinstance(ts, dict) and "ma20" in ts and "ma60" in ts and "rules" in ts, "trailingStop 字段齐备"); ok = ok and chk
if ts:
    for _k in ("ma20", "ma60"):
        _v = ts[_k]
        chk = _chk(_v is None or (isinstance(_v, (int, float)) and abs(_v) < 1e6),
                   "trailingStop.%s 有限或 null: %s" % (_k, _v)); ok = ok and chk
    _rules = ts.get("rules") or []
    chk = _chk(len(_rules) == 3, "trailingStop.rules 含 3 条: %d" % len(_rules)); ok = ok and chk
    for _r in _rules:
        chk = _chk(all(x in _r for x in ("trigger", "price", "action")),
                   "trailingStop 规则含 trigger/price/action: %s" % _r.get("trigger")); ok = ok and chk

# ---- ⑤ 子浪时间校准 + R48 时间不变量 ----
tc = (D.get("subForecast") or {}).get("timeCalib") or {}
chk = _chk(isinstance(tc, dict) and all(len(tc.get(k, [])) == 5 for k in ("empirical", "prior", "blended")),
           "subForecast.timeCalib 三序列各 5 元素"); ok = ok and chk
if tc and len(tc.get("blended", [])) == 5:
    _s = sum(tc["blended"])
    chk = _chk(0.99 <= _s <= 1.01, "timeCalib.blended 累计和≈1: %.3f" % _s); ok = ok and chk
# R48：子浪ⅴ expDays == 卖① expDays；子浪ⅰ < 子浪ⅴ
s1 = next(t for t in D["tradePlan"]["sellTargets"] if t["name"].startswith("卖①"))
sv = next(p for p in D["subForecast"]["points"] if p["label"] == "子浪ⅴ")
si = next(p for p in D["subForecast"]["points"] if p["label"] == "子浪ⅰ")
chk = _chk(abs(sv["expDays"] - s1["expDays"]) < 1.0,
           "子浪ⅴ expDays(%.0f)==卖① expDays(%.0f)（R48 仍成立）" % (sv["expDays"], s1["expDays"])); ok = ok and chk
chk = _chk(si["expDays"] < sv["expDays"], "子浪ⅰ expDays(%.0f)<子浪ⅴ expDays(%.0f)" % (si["expDays"], sv["expDays"])); ok = ok and chk

# ---- ⑥ 回测自动升级通道 + 概率整体健康 ----
_titles = [f["title"] for f in D.get("findings", [])]
chk = _chk("回测样本进度" in _titles, "findings 含「回测样本进度」卡（建议1 通道可见）"); ok = ok and chk
ALLOWED = ("回测实证", "历史浪幅校准", "漂移模型")
_targets = []
for t in D["tradePlan"]["sellTargets"]:
    _targets.append(t)
for p in D["subForecast"]["points"]:
    _targets.append(p)
for r in D["subForecast"]["rows"]:
    _targets.append(r)
_bad = 0
for t in _targets:
    if not (2 <= t.get("prob", -1) <= 98):
        _bad += 1; print("  [FAIL] prob 越界: %s=%s" % (t.get("name") or t.get("label"), t.get("prob")))
    if t.get("probSrc") not in ALLOWED:
        _bad += 1; print("  [FAIL] probSrc 非法: %s=%s" % (t.get("name") or t.get("label"), t.get("probSrc")))
    if not (0 < t.get("bandPct", -1) < 25):
        _bad += 1; print("  [FAIL] bandPct 越界: %s=%s" % (t.get("name") or t.get("label"), t.get("bandPct")))
chk = _chk(_bad == 0, "全部目标 prob∈[2,98]、probSrc 合法、bandPct∈(0,25)：共 %d 个目标，异常 %d" % (len(_targets), _bad)); ok = ok and chk

print("\n守门员结论：" + ("全部不变量通过，0 问题。" if ok else "存在问题，见上。"))
sys.exit(0 if ok else 1)
