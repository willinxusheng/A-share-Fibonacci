#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R228 — 子浪价位比率「R85 诚实性 + 样本充足性 + 自洽」体检
============================================================================
前几轮(R214→R227)查了价格概率、时间维、线上自洽，但从未验证 build_data 的
「子浪价位经验校准」是否真的遵守 R85 纪律：
  line 828: 仅当 经验 MAE < 标准 MAE 才启用经验比率，否则回退斐波那契标准。

本脚本 faithful 复刻三条核心函数(_detect_five_wave_runs / _measure_subwave_ratios
/ _subwave_baseline_error, build_data line 35-128)，独立重算 R85 选择，
与 data.js 发布的 empSamples / baselineMaeFib / baselineMaeEmp / iiRet 比对：
  1) R85 诚实性：发布选择是否真满足「经验 MAE < 标准 MAE」(或样本不足回退标准)
  2) 样本充足性：本指数有效同浪级 5 浪结构数 n
  3) 子浪价位自洽：用发布比率 + w4_low + 卖① 复刻子浪ⅰ/ⅲ/ⅴ 价位 vs data.js

严守 R85：只读、不改生产。
"""
import os, re, json, math, statistics
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")

# ---- faithful 复刻 (build_data line 35-128) ----
def _detect_five_wave_runs(zigzag, min_leg_days=12, min_net=0.02):
    legs = []
    for i in range(len(zigzag) - 1):
        p0, p1 = zigzag[i], zigzag[i + 1]
        try:
            ld = max(1, len(pd.bdate_range(pd.Timestamp(p0["date"]), pd.Timestamp(p1["date"]))) - 1)
            lr = math.log(float(p1["price"]) / float(p0["price"]))
        except Exception:
            continue
        if ld < min_leg_days:
            continue
        legs.append({"dir": 1 if lr > 0 else -1,
                     "p0": float(p0["price"]), "p1": float(p1["price"]),
                     "d0": str(p0["date"]), "d1": str(p1["date"])})
    runs = []
    for s in range(0, len(legs) - 4):
        wl = legs[s:s + 5]
        if not (wl[0]["dir"] > 0 and wl[1]["dir"] < 0 and wl[2]["dir"] > 0
                and wl[3]["dir"] < 0 and wl[4]["dir"] > 0):
            continue
        p = [wl[0]["p0"], wl[0]["p1"], wl[1]["p1"], wl[2]["p1"], wl[3]["p1"], wl[4]["p1"]]
        d = [wl[0]["d0"], wl[0]["d1"], wl[1]["d1"], wl[2]["d1"], wl[3]["d1"], wl[4]["d1"]]
        if (p[5] - p[0]) / p[0] < min_net:
            continue
        runs.append({"p": p, "d": d})
    return runs

def _measure_subwave_ratios(zigzag):
    runs = _detect_five_wave_runs(zigzag)
    if len(runs) < 6:
        return None
    ii, iii, iv = [], [], []
    for r in runs:
        p = r["p"]
        w1, w2, w3, w4 = p[1] - p[0], p[1] - p[2], p[3] - p[2], p[3] - p[4]
        if not (w1 > 0 and w2 > 0 and w3 > 0 and w4 > 0):
            continue
        r2_, r3, r4 = w2 / w1, w3 / w1, w4 / w3
        if not (0.20 <= r2_ <= 0.70 and 1.0 <= r3 <= 2.20 and 0.20 <= r4 <= 0.60):
            continue
        ii.append(r2_); iii.append(r3); iv.append(r4)
    if len(ii) < 6:
        return None
    return {"ii_ret": round(statistics.median(ii), 4),
            "iii": round(statistics.median(iii), 4),
            "iv_ret": round(statistics.median(iv), 4),
            "n": len(ii)}

def _subwave_baseline_error(zigzag, ratios):
    bii, biii, biv = (0.5, 1.618, 0.2917) if ratios is None else (ratios["ii_ret"], ratios["iii"], ratios["iv_ret"])
    runs = _detect_five_wave_runs(zigzag)
    if not runs:
        return None
    perr_frac, pe_pct = [], []
    for r in runs:
        p, d = r["p"], r["d"]
        net = p[5] - p[0]
        amp = 2 - bii + biii - biv * biii
        ri = 1.0 / amp
        pred = [p[0] + ri * net, p[0] + ri * (1 - bii) * net,
                p[0] + ri * (1 - bii + biii) * net,
                p[0] + ri * (1 - bii + biii - biv * biii) * net, p[5]]
        act = [p[1], p[2], p[3], p[4], p[5]]
        for a, b in zip(pred, act):
            perr_frac.append(abs(a - b) / net)
            pe_pct.append(abs(a - b) / b * 100)
    return {"n": len(runs),
            "mae_price_frac": round(statistics.median(perr_frac), 4),
            "disp_pct": round(float(np.percentile(pe_pct, 68)), 2)}

# ---- data.js 递归搜索 ----
def _find_key(obj, keys):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and not isinstance(v, (dict, list)):
                out[k] = v
            out.update(_find_key(v, keys))
    elif isinstance(obj, list):
        for v in obj:
            out.update(_find_key(v, keys))
    return out

def _find_price_by_name(obj, name_sub):
    if isinstance(obj, dict):
        if name_sub in str(obj.get("name", "")) and "price" in obj:
            return float(obj["price"])
        for v in obj.values():
            r = _find_price_by_name(v, name_sub)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_price_by_name(v, name_sub)
            if r is not None:
                return r
    return None

def _collect_points(obj, out):
    if isinstance(obj, dict):
        _n = str(obj.get("label", obj.get("name", "")))
        if ("子浪" in _n) and "price" in obj:
            out[_n] = float(obj["price"])
        for v in obj.values():
            _collect_points(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_points(v, out)

def main():
    with open(os.path.join(DATA, "structures.json"), encoding="utf-8") as f:
        st = json.load(f)
    zig = st.get("zigzag", [])

    # 独立复刻 R85 选择
    _sw_emp = _measure_subwave_ratios(zig)
    _sw_base_err = _subwave_baseline_error(zig, None)
    _sw_emp_err = _subwave_baseline_error(zig, _sw_emp) if _sw_emp else None
    if _sw_emp and _sw_emp_err and _sw_base_err and _sw_emp_err["mae_price_frac"] < _sw_base_err["mae_price_frac"]:
        my_choice = "经验校准"
    else:
        my_choice = "斐波那契标准"

    # data.js 发布值（递归搜真实字段）
    s = open(os.path.join(DATA, "data.js"), encoding="utf-8").read()
    m = re.search(r'FIB_DATA\s*=\s*(\{.*\})\s*;', s, re.S)
    d = json.loads(m.group(1))
    fld = _find_key(d, {"iiRet", "iii", "ivRet", "empSamples", "baselineMaeFib", "baselineMaeEmp"})
    pub_emp_n = fld.get("empSamples")
    pub_mae_fib = fld.get("baselineMaeFib")
    pub_mae_emp = fld.get("baselineMaeEmp")
    pub_ii = fld.get("iiRet"); pub_iii = fld.get("iii"); pub_iv = fld.get("ivRet")
    if (pub_emp_n not in (None, 0) and pub_emp_n >= 6 and pub_mae_emp is not None
            and pub_mae_fib is not None and pub_mae_emp < pub_mae_fib):
        pub_choice = "经验校准"
    else:
        pub_choice = "斐波那契标准"

    print("=" * 72)
    print("R228 子浪价位比率 R85 诚实性 + 样本充足性 体检")
    print("=" * 72)
    print("本指数 zigzag 点数 = %d" % len(zig))
    print("\n[独立复刻] _sw_emp n = %s, 经验 MAE = %s" % (
        (_sw_emp["n"] if _sw_emp else None), (_sw_emp_err["mae_price_frac"] if _sw_emp_err else None)))
    print("[独立复刻] 标准 MAE = %s" % _sw_base_err["mae_price_frac"])
    print("[独立复刻] R85 选择 = %s" % my_choice)
    print("\n[发布值] empSamples = %s | baselineMaeFib = %s | baselineMaeEmp = %s"
          % (pub_emp_n, pub_mae_fib, pub_mae_emp))
    print("[发布值] iiRet/iii/ivRet = %s / %s / %s" % (pub_ii, pub_iii, pub_iv))
    print("[发布选择推断] %s" % pub_choice)

    # 1) R85 诚实性
    honest = (my_choice == pub_choice)
    reason = [] if honest else ["独立重算(%s) != 发布推断(%s)" % (my_choice, pub_choice)]
    print("\n[R85 诚实性] %s%s" % ("OK 发布选择=独立重算" if honest else "FAIL",
                                  ("（" + "; ".join(reason) + "）") if reason else ""))

    # 2) 样本充足性
    n = (_sw_emp["n"] if _sw_emp else 0)
    print("[样本充足性] 有效同浪级 5 浪结构 n = %d → %s" % (
        n, "充足" if n >= 6 else "不足(<6)，经验校准不可行→已回退标准(正确)"))

    # 3) 子浪价位自洽：用发布子浪ⅰ + 卖① + 发布比率 反推 w4_low，再验证子浪ⅲ/ⅴ（无外部猜测）
    pts_pub = {}
    _collect_points(d, pts_pub)
    sell1 = _find_price_by_name(d, "卖①")
    si_p = pts_pub.get("子浪ⅰ")
    if pub_ii is not None and si_p and sell1:
        bii = float(pub_ii); biii = float(pub_iii); biv = float(pub_iv)
        R_AMP = 2 - bii + biii - biv * biii
        RI = 1.0 / R_AMP
        # 反推 w4_low：子浪ⅰ = w4_low + RI*(卖① - w4_low)
        w4_low = (si_p - RI * sell1) / (1 - RI)
        sfA = sell1 - w4_low
        siii = w4_low + RI * (1 - bii + biii) * sfA
        sv = w4_low + RI * R_AMP * sfA
        def _near(val):
            best = None; bd = 1e9
            for k, v in pts_pub.items():
                dd = abs(v - val)
                if dd < bd: bd = dd; best = (k, v)
            return best, bd
        print("\n[子浪价位自洽] 反推 w4_low=%.2f 卖①=%.2f (采用%s)" % (w4_low, sell1, pub_choice))
        for lbl, val in [("子浪ⅰ", si_p), ("子浪ⅲ", siii), ("子浪ⅴ", sv)]:
            best, bd = _near(val)
            print("  %s 推导/发布=%.2f <-> 发布%s=%.2f (Δ=%.2f)" % (lbl, val, best[0] if best else "?", best[1] if best else 0, bd))
    else:
        print("\n[子浪价位自洽] 跳过：子浪ⅰ/卖①/比率 未就绪")

    print("\n" + "=" * 72)
    print("结论：R85 纪律诚实性 + 子浪价位自洽 + 样本边界 体检完毕（不替代实證）。")
    print("=" * 72)

if __name__ == "__main__":
    main()
