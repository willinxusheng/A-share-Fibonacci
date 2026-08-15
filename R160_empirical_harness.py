# -*- coding: utf-8 -*-
"""R160: 用更细粒度 zigzag 全历史 harvest 同浪级 5 浪结构，
测试能否激活 _measure_subwave_ratios 经验校准，并用引擎自带
_subwave_baseline_error 对比经验 MAE vs 斐波那契基线（R85 守纪律）。
不修改引擎，仅出数字。"""
import os, sys, json
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from analyze import zigzag_pct
import build_data as BD

df = pd.read_csv(os.path.join(BASE, "data", "sh000001.csv"), parse_dates=["date"]).set_index("date")
print("CSV rows:", len(df), "range:", df.index[0].date(), "~", df.index[-1].date())

# 既有 8% zigzag（structures.json 来源）
with open(os.path.join(BASE, "data", "structures.json"), encoding="utf-8") as f:
    st = json.load(f)
zz8 = st.get("zigzag", [])
print("\n=== 既有 8% zigzag 检测 ===")
runs8 = BD._detect_five_wave_runs(zz8)
print("  5浪结构数:", len(runs8))
m8 = BD._measure_subwave_ratios(zz8)
print("  经验比率(需>=6):", m8)
b8 = BD._subwave_baseline_error(zz8, None)
print("  斐波那契基线 MAE(price_frac):", b8["mae_price_frac"], "disp%%:", b8["disp_pct"], "n=", b8["n"])

# 子浪尺度检测：放宽腿长下限(12->5/7/9) 在细 zigzag 上 harvest 子浪级 5 浪
import statistics as _st
for pct in [0.03, 0.04, 0.045, 0.05]:
    for mld in [5, 7, 9]:
        zz = zigzag_pct(df, pct)
        runs = BD._detect_five_wave_runs(zz, min_leg_days=mld, min_net=0.01)
        m = BD._measure_subwave_ratios(zz) if len(runs) >= 6 else None
        # 注意 _measure_subwave_ratios 内部用默认 min_leg_days=12，这里直接手动复算以尊重放宽后的 runs
        ii, iii, iv = [], [], []
        for r in runs:
            p = r["p"]
            w1, w2, w3, w4 = p[1]-p[0], p[1]-p[2], p[3]-p[2], p[3]-p[4]
            if not (w1>0 and w2>0 and w3>0 and w4>0): continue
            r2_, r3, r4 = w2/w1, w3/w1, w4/w3
            if not (0.20 <= r2_ <= 0.70 and 1.0 <= r3 <= 2.20 and 0.20 <= r4 <= 0.60): continue
            ii.append(r2_); iii.append(r3); iv.append(r4)
        emp = {"ii_ret": _st.median(ii), "iii": _st.median(iii), "iv_ret": _st.median(iv), "n": len(ii)} if len(ii)>=6 else None
        b_fib = BD._subwave_baseline_error(zz, None)
        b_emp = BD._subwave_baseline_error(zz, emp) if emp else None
        mae_fib = ("%.4f" % b_fib["mae_price_frac"]) if b_fib else "n/a(runs=0)"
        win = (emp is not None and b_emp is not None and b_fib is not None and b_emp["mae_price_frac"] < b_fib["mae_price_frac"])
        print("pct=%.0f%% mld=%d -> runs=%d emp_n=%s MAE_emp=%s MAE_fib=%s %s"
              % (pct*100, mld, len(runs), (emp["n"] if emp else "-"),
                 ("%.4f" % b_emp["mae_price_frac"] if b_emp else "-"),
                 mae_fib, ("<< 经验更优!" if win else "")))

# 展示最细有效阈值下的原始比率样本分布
print("\n=== 原始比率样本（0.045 zigzag 阈值，不卡 sanity）===")
zz = zigzag_pct(df, 0.045)
runs = BD._detect_five_wave_runs(zz)
ii_l, iii_l, iv_l = [], [], []
for r in runs:
    p = r["p"]
    w1, w2, w3, w4 = p[1]-p[0], p[1]-p[2], p[3]-p[2], p[3]-p[4]
    if not (w1>0 and w2>0 and w3>0 and w4>0): continue
    ii_l.append(w2/w1); iii_l.append(w3/w1); iv_l.append(w4/w3)
import statistics
if ii_l:
    print("  n=%d  ii_ret median=%.3f [min%.3f,max%.3f]" % (len(ii_l), statistics.median(ii_l), min(ii_l), max(ii_l)))
    print("  iii   median=%.3f [min%.3f,max%.3f]" % (statistics.median(iii_l), min(iii_l), max(iii_l)))
    print("  iv_ret median=%.3f [min%.3f,max%.3f]" % (statistics.median(iv_l), min(iv_l), max(iv_l)))
