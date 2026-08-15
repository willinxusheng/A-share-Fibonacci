# R164: 校验已部署 data.js 实产物的数值健全性（前10轮只跑引擎源码，从没读过实际部署数值）
import json, re, sys

path = "data/data.js"
raw = open(path, encoding="utf-8").read()
# 去掉前缀 window.FIB_DATA = / const D = / var D = 等，取到第一个 { 起的 JSON
m = re.search(r"=\s*(\{.*\})\s*;?\s*$", raw, re.S)
if not m:
    # 退路：直接找最外层 {}
    m = re.search(r"(\{.*\})", raw, re.S)
obj = json.loads(m.group(1))
print("顶层键:", list(obj.keys()))

# 子浪预测
sf = obj.get("subForecast") or {}
rows = sf.get("rows") or []
pts = sf.get("points") or []
print("\n=== subForecast 实产物校验 ===")
print("rows 数:", len(rows), " points 数:", len(pts))
bad = 0
for r in rows:
    p = r.get("prob"); lo = r.get("lo"); hi = r.get("hi"); t = r.get("target")
    src = r.get("probSrc"); ed = r.get("expDays")
    issues = []
    if p is None or not (0 <= p <= 100): issues.append("prob越界")
    if lo is None or hi is None or t is None: issues.append("缺失")
    else:
        if not (lo <= t <= hi): issues.append("target不在[lo,hi]")
        if lo >= hi: issues.append("lo>=hi")
    if ed is not None and ed <= 0: issues.append("expDays<=0")
    tag = ("🌊" if not issues else "❌")
    if issues: bad += 1
    print(f"  {tag} {r.get('wave'):>6} target={t} band=[{lo},{hi}] prob={p}%({src}) expDays={ed} {' '.join(issues)}")

# 端点锁：子浪ⅴ == 卖①（sellTargets 在 tradePlan 下）
st = (obj.get("tradePlan") or {}).get("sellTargets") or []
s1 = st[0]["price"] if st else None
vrow = next((r for r in rows if r.get("wave") == "子浪ⅴ"), None)
if vrow and s1 is not None:
    diff = abs(vrow["target"] - s1)
    print(f"\n端点锁: 子浪ⅴ={vrow['target']} 卖①={s1} 差={diff:.6f} -> {'PASS' if diff < 1e-6 else 'FAIL'}")

# 回测实证
bt = obj.get("backtest") or {}
print("\n=== backtest 实产物 ===")
print("  coldStart:", bt.get("coldStart"), " empiricalActive:", bt.get("empiricalActive"),
      " empiricalAnchors:", bt.get("empiricalAnchors"))
# 注意：build_data  nowhere 输出 "empiricalRates" 键；真实实证命中率表在 backtest["summary"]
# （前端 index.html:1054 也读 bt.summary）。下方读 summary 才是正确判定，避免误报"实证率为空"。
sm = bt.get("summary") or []
print("  summary(真实实证表) 样本数:", len(sm), " （empiricalRates 键是否存在:", "empiricalRates" in bt, "）")
for s in sm:
    print("    %-14s hitRate=%.1f%%  n=%s  cold=%s" % (s.get("key"), s.get("hitRate", 0), s.get("n"), s.get("cold")))

# subForecast.calib
cb = sf.get("calib") or {}
print("\n=== calib ===")
print("  ratioSrc:", cb.get("ratioSrc"), " empSamples:", cb.get("empSamples"),
      " baselineMaeFib:", cb.get("baselineMaeFib"), " bandPct:", cb.get("bandPct"))

print("\n=== 汇总 ===")
print("  子浪行异常数:", bad, " (0=全部健全)")
print("  结论:", "已部署实物数值健全" if bad == 0 else "存在数值异常需修")
