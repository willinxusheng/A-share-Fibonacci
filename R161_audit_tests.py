# -*- coding: utf-8 -*-
"""R161 实证测试（补足前几轮仅"肉眼说成立"的缺口）：
  ① 子浪端点锁代数：data.js 子浪ⅴ.price 是否精确 == 卖①.price（端点锁代数非近似）
  ② 5 浪检测器召回：构造已知 5 浪，验证 _detect_five_wave_runs 能正确识别（非 bug）
  ③ 当前 regime 下真实 R_II_RET、模型买② vs 真实锚定买② 的精确差距（纠正前几轮误用 0.618 的数字）
  ④ _subwave_baseline_error 自洽：构造 pred=act 的 5 浪，MAE 应≈0
只读不写，不污染看板。
"""
import json, re, os, math
import pandas as pd
import numpy as np
import build_data as BD

BASE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(BASE, "data", "data.js"), encoding="utf-8").read()
D = json.loads(re.search(r"window\.FIB_DATA\s*=\s*(\{.*\})\s*;", src, re.S).group(1))

print("=== ① 子浪端点锁代数（validate/audit49/51 强检验，此处精确数值复核）===")
s1 = D["tradePlan"]["sellTargets"][0]
sv = next(p for p in D["subForecast"]["points"] if p["label"] == "子浪ⅴ")
d = abs(sv["price"] - s1["price"])
print("  子浪ⅴ=%.4f  卖①=%.4f  差=%.6f  %s" % (sv["price"], s1["price"], d, "PASS(<1e-6)" if d < 1e-6 else "FAIL"))

print("\n=== ② 5 浪检测器召回（构造已知清洁 5 浪，腿长>12交易日）===")
zz = [{"date": "2020-01-01", "price": 1000.0},
      {"date": "2020-02-01", "price": 1200.0},
      {"date": "2020-03-01", "price": 1100.0},
      {"date": "2020-04-01", "price": 1500.0},
      {"date": "2020-05-01", "price": 1400.0},
      {"date": "2020-06-01", "price": 2000.0}]
runs = BD._detect_five_wave_runs(zz)
print("  构造 5 浪 -> 检测器识别 runs=%d" % len(runs))
if runs:
    print("  p=", [round(x, 1) for x in runs[0]["p"]], "（期望 [1000,1200,1100,1500,1400,2000]）")
# 构造非 5 浪（仅 3 腿方向 +++）不应误报为 5 浪
zz3 = [{"date": "2020-01-01", "price": 1000.0},
       {"date": "2020-02-01", "price": 1100.0},
       {"date": "2020-03-01", "price": 1200.0},
       {"date": "2020-04-01", "price": 1300.0},
       {"date": "2020-05-01", "price": 1400.0},
       {"date": "2020-06-01", "price": 1500.0}]
runs3 = BD._detect_five_wave_runs(zz3)
print("  构造单调上行(非5浪) -> 检测器识别 runs=%d（期望 0，不误报）" % len(runs3))

print("\n=== ③ 当前 regime 真实 R_II_RET 与子浪ⅱ偏差精确量化（纠正前几轮误用 0.618）===")
calib = D["subForecast"]["calib"]
ii_ret = calib.get("iiRet")
print("  子浪ⅱ回撤 R_II_RET = %.3f（斐波那契标准基线 0.5 ×regime，clamp[0.236,0.618]）" % (ii_ret or 0))
rr = D["subForecast"].get("realRef", {})
real_ret = rr.get("wave3Sub2Ret")
print("  真实浪③同浪级子浪ⅱ回撤 = %.1f%%（单样本）" % (real_ret if real_ret is not None else float('nan')))
# 模型买② vs 真实锚定买②
pts = {p["label"]: p["price"] for p in D["subForecast"]["points"]}
i0 = pts.get("浪⑤起"); i1 = pts.get("子浪ⅰ"); model_ii = pts.get("子浪ⅱ")
if None not in (i0, i1, model_ii) and real_ret is not None:
    _real_ii = i1 - (i1 - i0) * (real_ret / 100.0)
    gap = _real_ii - model_ii
    print("  模型买②(子浪ⅱ底)=%.2f  真实锚定买②≈%.2f  差距=%.2f 点(%.2f%%)"
          % (model_ii, _real_ii, gap, gap / model_ii * 100))
    print("  （前几轮误用 0.618 报'差87点'，实际按当前 R_II_RET=%.3f 应为约%.0f点）" % (ii_ret, gap))

print("\n=== ④ _subwave_baseline_error 自洽（构造 pred=act 的 5 浪，MAE 应≈0）===")
# 用斐波那契标准生成 5 浪拐点（act=pred），看 _subwave_baseline_error 自身误差度量
bii, biii, biv = 0.5, 1.618, 0.2917
amp = 2 - bii + biii - biv * biii
ri = 1.0 / amp
net = 1000.0
p = [1000.0,
     1000.0 + ri * net,
     1000.0 + ri * (1 - bii) * net,
     1000.0 + ri * (1 - bii + biii) * net,
     1000.0 + ri * (1 - bii + biii - biv * biii) * net,
     2000.0]
# 构造使 _detect_five_wave_runs 返回这 6 点的 zigzag（正好 6 拐点）
zz_self = [{"date": "2020-01-01", "price": p[0]},
           {"date": "2020-02-01", "price": p[1]},
           {"date": "2020-03-01", "price": p[2]},
           {"date": "2020-04-01", "price": p[3]},
           {"date": "2020-05-01", "price": p[4]},
           {"date": "2020-06-01", "price": p[5]}]
err = BD._subwave_baseline_error(zz_self, {"ii_ret": bii, "iii": biii, "iv_ret": biv})
print("  自洽 5 浪 -> mae_price_frac=%.4f disp_pct=%.2f  %s"
      % (err["mae_price_frac"], err["disp_pct"],
         "PASS(≈0)" if err["mae_price_frac"] < 1e-6 else "FAIL"))
