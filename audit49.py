# -*- coding: utf-8 -*-
"""R48 守门员：子浪 expDays 时间锚定基准必须与卖点一致（相对今日 last_date）。

回测 evaluate() 从 B日(rec['date']=updated=今日) 往后推 expDays 天观察窗，
故所有目标 expDays 必须相对今日。R48 修复前 _sf_exp 用 _sf_sd(浪⑤起点) 基准，
导致子浪观察窗系统性偏长(今日→浪⑤起)约 11 个交易日，与卖点口径脱节。

本守门员固化：①源码级防线(_sf_exp 必须用 last_date 不可回退 _sf_sd)；
②数据级(从 FIB_DATA 读，独立复刻 _trading_days_between 验证子浪ⅴ==卖①、基准相对今日)；
③回测语义(从 B日 推窗)。全程只读不写、不调 run_backtest，杜绝生产污染。
"""
import re, json, os, sys
BASE = os.path.dirname(os.path.abspath(__file__))

def _chk(cond, msg):
    print(("  [OK] " if cond else "  [FAIL] ") + msg)
    return cond

def _load_fib():
    src = open(os.path.join(BASE, "data", "data.js"), encoding="utf-8").read()
    m = re.search(r"window\.FIB_DATA\s*=\s*(\{.*\})\s*;?\s*$", src, re.S)
    return json.loads(m.group(1))

def _tdb(d0, d1):
    import pandas as pd
    d0, d1 = pd.Timestamp(d0), pd.Timestamp(d1)
    if d1 <= d0:
        return 10          # 锚点落在过去(如浪⑤起)：兜底小正值
    return len(pd.bdate_range(d0, d1)) - 1   # 真实差(与 build_data.py _trading_days_between 同步, R60)

ok = True
print("=== R48 守门员：子浪 expDays 锚定基准 ===")

# ---- ① 源码级防线 ----
bsrc = open(os.path.join(BASE, "build_data.py"), encoding="utf-8").read()
line = [l for l in bsrc.splitlines() if "_sf_exp = [" in l]
line = line[0] if line else ""
chk = _chk("_trading_days_between(last_date, _sf_date(k))" in line,
           "_sf_exp 用 last_date 基准（与 _horizon_for 同源）")
ok = ok and chk
chk = _chk("_sf_sd" not in line.split("=")[1].split("for")[0],
           "_sf_exp 行未回退到 _sf_sd 旧基准")
ok = ok and chk
# _horizon_for 由历史摆动腿独立派生（不依赖情景图稀疏点），与子浪 _sf_exp 同源口径
_hf = bsrc.split("_horizon_for(price):", 1)[1].split("\n    def ", 1)[0]
# 注释可提及 scenarios[0]（解释为何解耦），故只检查代码不再「从情景图取点」(scenarios[0]["points"])
chk = _chk("_hist_legs" in _hf and 'scenarios[0]["points"]' not in _hf,
           "_horizon_for 由历史摆动腿(_hist_legs)独立派生、不再从情景图取点")
ok = ok and chk

# ---- ② 数据级（从 FIB_DATA 读）----
D = _load_fib()
last_date = D["updated"]
s1 = next(t for t in D["tradePlan"]["sellTargets"] if t["name"].startswith("卖①"))
sv = next(p for p in D["subForecast"]["points"] if p["label"] == "子浪ⅴ")
si = next(p for p in D["subForecast"]["points"] if p["label"] == "子浪ⅰ")
w5 = next(p for p in D["subForecast"]["points"] if p["label"] == "浪⑤起")

chk = _chk(abs(sv["expDays"] - s1["expDays"]) < 1.0,
           "子浪ⅴ expDays(%.0f) == 卖① expDays(%.0f)（同触达日应一致）" % (sv["expDays"], s1["expDays"]))
ok = ok and chk
chk = _chk(si["expDays"] < sv["expDays"],
           "子浪ⅰ expDays(%.0f) < 子浪ⅴ expDays(%.0f)（行程顺序合理）" % (si["expDays"], sv["expDays"]))
ok = ok and chk
chk = _chk(w5["expDays"] == 10,
           "浪⑤起 expDays==10(floor，已过的锚点)")
ok = ok and chk
# 独立复刻验证：子浪ⅴ expDays 必须等于 今日→子浪ⅴ date（相对今日基准）
exp_sv = _tdb(last_date, sv["date"])
chk = _chk(abs(sv["expDays"] - exp_sv) < 1.0,
           "子浪ⅴ expDays(%.0f) == 今日(%s)→子浪ⅴ(%s) 独立复刻 %.0f" % (sv["expDays"], last_date, sv["date"], exp_sv))
ok = ok and chk
# 反证：若用旧基准(浪⑤起) 则偏差约 11 日，守门员须保证不发生
exp_sv_old = _tdb(w5["date"], sv["date"])
chk = _chk(abs(exp_sv_old - sv["expDays"]) > 5.0,
           "旧基准(浪⑤起→子浪ⅴ=%.0f) 与现值(%.0f) 偏差>5日（已修复、不再使用）" % (exp_sv_old, sv["expDays"]))
ok = ok and chk

# ---- ③ 回测语义确认（evaluate 从 B日 推窗，expDays 须相对今日）----
b2 = open(os.path.join(BASE, "backtest.py"), encoding="utf-8").read()
chk = _chk('i0 = _safe_idx(dates, rec["date"])' in b2 and 'max(HORIZON, int(rec.get("expDays")' in b2,
           "evaluate 从 B日(rec['date']=updated=今日) 推 max(HORIZON,expDays) 窗 → expDays 须相对今日")
ok = ok and chk

print("\n守门员结论：" + ("全部不变量通过，0 问题。" if ok else "存在问题，见上。"))
sys.exit(0 if ok else 1)
