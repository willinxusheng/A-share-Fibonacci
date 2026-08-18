#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R227 — 子浪/卖点「真实触达时间」(expDays) 内部质量体检（时间维准确性）
============================================================================
前几轮(R214→R226)只查了「价格概率」维度的准确性，从未验证过
build_data 的「时间维」——即各目标 expDays 的派生质量。

本脚本 faithful 复刻 build_data 两段核心逻辑：
  * _hist_legs 构造 (line 176-188, structures.json zigzag 完成浪 >=10 交易日)
  * _horizon_for(price) (line 781-811, 达到目标幅度的时长 ttr 中位数, R65 修复)
并补充：
  1) 自洽检查：复刻 expDays vs data.js 发布 expDays（应完全一致）
  2) 样本质量：每个目标 ttr 中位数来自多少个历史腿、P25/P50/P75 离散度
  3) 方向性偏差：上行目标 vs 下行目标的 ttr 中位是否系统性偏置
  4) 子浪内部时间分配：timeCalib.blended 单调性 + 子浪ⅴ≡卖① 不变

注意：最终「时间预测精度」须待 ~2026-09-15 前向回测闭合才能量化，
      本脚本只做「当下内部质量/自洽」体检（不依赖未来数据）。

严守 R85：只读、不改生产；输出体检结论。
"""
import os, re, json, math, statistics
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")

def _load_last_close():
    df = pd.read_csv(os.path.join(DATA, "sh000001.csv"), parse_dates=["date"]).set_index("date").sort_index()
    return float(df["close"].iloc[-1]), df.index[-1].strftime("%Y-%m-%d")

def _load_hist_legs():
    with open(os.path.join(DATA, "structures.json"), encoding="utf-8") as f:
        st = json.load(f)
    legs = []
    for _i in range(len(st.get("zigzag", [])) - 1):
        _p0, _p1 = st["zigzag"][_i], st["zigzag"][_i + 1]
        try:
            _ld = max(1, len(pd.bdate_range(pd.Timestamp(_p0["date"]), pd.Timestamp(_p1["date"]))) - 1)
        except Exception as _e:
            print("[WARN] R227 跳过历史腿 %s→%s：%s" % (_p0.get("date"), _p1.get("date"), _e))
            continue
        if _ld < 10:
            continue
        try:
            _lr = math.log(float(_p1["price"]) / float(_p0["price"]))
        except Exception as _e:
            print("[WARN] R227 跳过历史腿 %s→%s：价格比非正或不可解析 %s" % (_p0.get("date"), _p1.get("date"), _e))
            continue
        legs.append((_lr, _ld, _p0["date"], _p1["date"]))
    return legs

def _horizon_for(price, last_close, hist_legs):
    _a = math.log(price / last_close)
    _dir = 1 if _a >= 0 else -1
    _at = abs(_a)
    _ttr = sorted(ld * (_at / abs(lr)) for lr, ld, *_ in hist_legs
                  if _dir * lr > 0 and abs(lr) >= _at and abs(lr) > 0)
    if len(_ttr) >= 4:
        _exp = _ttr[len(_ttr) // 2]
    else:
        _near = sorted([(abs(lr), ld) for lr, ld, *_ in hist_legs if _dir * lr > 0],
                       key=lambda x: abs(x[0] - _at))[:5]
        if _near:
            _exps = [ld * (_at / abs(lr)) for lr, ld in _near if abs(lr) > 0]
            _exp = sum(_exps) / len(_exps)
        else:
            _exp = 330
    return max(10, min(330, int(round(_exp)))), _ttr

def _ttr_stats(hist_legs):
    """方向性偏差：上行腿 vs 下行腿的 ttr 中位（R65 修正是否真降偏）"""
    up_ttr, dn_ttr = [], []
    for lr, ld, *_ in hist_legs:
        # 整条腿时长 vs ttr(用自身幅度)——此处只看「方向性幅度-时长」经验
        if lr > 0:
            up_ttr.append((abs(lr), ld))
        else:
            dn_ttr.append((abs(lr), ld))
    return up_ttr, dn_ttr

def main():
    last_close, last_date = _load_last_close()
    legs = _load_hist_legs()
    print("=" * 72)
    print("R227 子浪/卖点触达时间(expDays) 内部质量体检")
    print("=" * 72)
    print("last_close = %.2f (%s) | 历史完成浪(>=10 交易日) = %d 条" % (last_close, last_date, len(legs)))

    # ---- 目标价格（来自 data.js，与 R224 一致） ----
    s = open(os.path.join(DATA, "data.js"), encoding="utf-8").read()
    m = re.search(r'FIB_DATA\s*=\s*(\{.*\})\s*;', s, re.S)
    d = json.loads(m.group(1))
    targets = []
    def walk(o):
        if isinstance(o, dict):
            if "expDays" in o and "price" in o:
                targets.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d)

    sells = [t for t in targets if (t.get("name") or "").startswith("卖")]
    sf_pts = d.get("subForecast", {}).get("points", [])
    s1 = next((x for x in sells if "卖①" in (x.get("name") or "")), None)
    s1_exp = int(s1["expDays"]) if s1 else 54

    # ---- 卖点(主浪)自洽：_horizon_for(price) 复刻 vs 发布 ----
    print("\n[卖点自洽] 复刻 _horizon_for(price) vs data.js 发布 expDays")
    print("%-22s %8s %8s %10s %8s %8s %8s" % ("目标","price","发布exp","复刻exp","样本数","P25","P75"))
    print("-" * 72)
    mism_sell = 0
    for t in sells:
        price = float(t["price"]); pub = int(t.get("expDays"))
        my_exp, ttr = _horizon_for(price, last_close, legs)
        ns = len(ttr)
        p25 = ttr[len(ttr)//4] if ns >= 4 else (ttr[0] if ns else 0)
        p75 = ttr[3*len(ttr)//4] if ns >= 4 else (ttr[-1] if ns else 0)
        flag = "" if my_exp == pub else "  <-- 不一致"
        if my_exp != pub: mism_sell += 1
        print("%-22s %8.2f %8d %10d %8d %8.1f %8.1f%s" % ((t.get("name") or "")[:22], price, pub, my_exp, ns, p25, p75, flag))
    print("卖点不一致数 = %d（应为 0）" % mism_sell)

    # ---- 子浪自洽：时间占比切分复刻 vs 发布（浪⑤起=历史锚点兜底10，跳过） ----
    tc = d.get("subForecast", {}).get("timeCalib", {})
    blended = tc.get("blended", [])
    _cum = [0.0]
    for f in blended:
        _cum.append(_cum[-1] + f)
    print("\n[子浪自洽] 卖①总时长(%d) × timeCalib.blended 累计 复刻 vs 发布" % s1_exp)
    print("%-14s %8s %10s %8s" % ("子浪","发布exp","复刻exp","偏差"))
    print("-" * 44)
    mism_sub = 0
    # sf_pts 顺序: 浪⑤起, 子浪ⅰ..ⅴ ; 复刻用 _cum[1..5] × s1_exp
    for k, p in enumerate(sf_pts):
        if k == 0:  # 浪⑤起=历史锚点兜底10，非真实时间，跳过
            continue
        pub = int(p.get("expDays"))
        my_exp = int(round(_cum[k] * s1_exp))
        flag = "" if abs(my_exp - pub) <= 2 else "  <-- 偏差>2"
        if abs(my_exp - pub) > 2: mism_sub += 1
        print("%-14s %8d %10d %8s" % (p.get("label", "k%d" % k), pub, my_exp, flag))
    print("子浪偏差>2 数 = %d（日历/bdate 微差允许）" % mism_sub)

    # ---- 方向性偏差 ----
    up, dn = _ttr_stats(legs)
    def _med(x): return statistics.median([ld for _, ld in x]) if x else float("nan")
    def _med_amp(x): return statistics.median([a for a, _ in x]) if x else float("nan")
    print("\n[方向性] 上行腿 N=%d 中位幅度=%.3f 中位时长=%d 交易日" % (len(up), _med_amp(up), _med(up)))
    print("[方向性] 下行腿 N=%d 中位幅度=%.3f 中位时长=%d 交易日" % (len(dn), _med_amp(dn), _med(dn)))
    if up and dn:
        _ratio = _med(up) / _med(dn)
        print("[方向性] 上行/下行 时长比 = %.3f（≈1 表示无方向性偏置；>1 上行更慢）" % _ratio)

    # ---- timeCalib 结构 + 子浪ⅴ≡卖① ----
    tc = d.get("subForecast", {}).get("timeCalib", {})
    if tc:
        blended = tc.get("blended", [])
        # 经典5浪时间结构：浪ⅲ(下标2)应最长，非单调
        max_i = max(range(len(blended)), key=lambda i: blended[i])
        print("\n[timeCalib] blended(子浪时间占比) = %s" % blended)
        print("[timeCalib] 浪ⅲ(下标2)最长 = %s（经典5浪结构正确，非需单调）" % (max_i == 2))
        print("[timeCalib] 经验样本数 empSamplesTime = %s（R107: N>=12 才启用经验权重，当前未启用）" % tc.get("empSamplesTime"))
    # 子浪ⅴ≡卖①
    sf_pts = d.get("subForecast", {}).get("points", [])
    sell = [t for t in targets if (t.get("name") or "").startswith("卖")]
    if sf_pts and sell:
        sv = max(sf_pts, key=lambda p: p.get("expDays", 0))
        s1 = next((x for x in sell if "卖①" in (x.get("name") or "")), None)
        if s1:
            print("[不变量] 子浪ⅴ expDays=%s ≡ 卖① expDays=%s → %s" % (
                sv.get("expDays"), s1.get("expDays"),
                "OK" if sv.get("expDays") == s1.get("expDays") else "FAIL"))

    print("\n" + "=" * 72)
    print("结论边界：时间维最终精度须待 ~2026-09-15 前向回测闭合量化；")
    print("本体检仅证「内部自洽 + 样本质量 + 方向性」，不替代实證。")
    print("=" * 72)

if __name__ == "__main__":
    main()
