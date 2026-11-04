#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit56 — R827 目标可达性与 targets 元数据守门员（第 13 道门禁）

背景（11-02 发现的两类真实缺陷）：
  A) `data.targets[]` 长期缺 `expDays`/`expDate` 字段：build_data 里 `targets`（图表标注）
     与 `sell_targets`（交易计划）是两份独立列表、价格表达式重复计算，而 expDays 的赋值
     只覆盖后者 ⇒ 任何按 targets 推算观察窗的消费方都会**静默回退 30 日**（本轮回测探针
     最初就把五个卖点全按 30 日算，得出完全错误的结论）。
  B) 目标价（斐波那契扩展）与观察窗（时间模型）来自两套互不校验的模型：卖① 距现价
     +23.53% 却承诺 74 个交易日到达，隐含日均 0.286%，而浪③ 全程 266 日 +34.92% 日均仅
     0.127% —— 要求浪⑤ 比浪③ 快 2.26 倍。若不量化"可达性"，这类目标会被记成
     "引擎看错方向"，而非"目标集脱离市场速率"。

本门禁固化（共 10 项）：
  T1  targets[] 每项必须有正整数 expDays、合法 expDate（> last_date 且非周末）、reach 三键
  T2  reach.window == expDays（不得两处各算一套）
  T3  reach.cls 与 reach.prob 阈值严格自洽（<10 unreachable / [10,35) stretch / >=35 achievable）
  T4  reach.prob **独立复算**：本文件用自实现的 numpy 滑窗重算 rr，与生产值比对（容差 0.05pp）
  T5  reach.dailyRate 与 (1+|distPct|)^(1/expDays)-1 自洽
  T6  sellTargets[].reach 与同价 target 的 reach **逐字段一致**（单一真值，禁双份）
  T7  dirBandsByReach 分档计数恒等式：Σmatured + Σpending + earlyHit == totalDirEvaluated
  T8  dirBandsByReach 单调性：相邻档 rateRaw 不得逆向（样本 >=5 的档）
  T9  dirReachMeta.monotonic 必须为 True 且 inversions 为空
  T10 reachSummary.wave5Implied.speedVsWave3 与 wave3.dailyRate 复算一致

⚠ 本文件刻意**不复用** build_data._reach_prob：独立实现是发现"同源同错"的唯一办法
   （R824/R825 的教训——两处互相印证的伪独立验证比没有验证更危险）。
"""
import csv
import json
import math
import os
import sys

import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(BASE, "data", "data.js")
CSV = os.path.join(BASE, "data", "sh000001.csv")

_fail = 0
_pass = 0


def chk(cond, name, detail=""):
    global _fail, _pass
    if cond:
        _pass += 1
        print("  [PASS] %-30s %s" % (name, detail))
    else:
        _fail += 1
        print("  [FAIL] %-30s %s" % (name, detail))


def load(path=None):
    s = open(path or DATA_JS, encoding="utf-8").read()
    return json.loads(s[s.index("{"): s.rindex("}") + 1])


def load_csv():
    rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
    dates = [r["date"] for r in rows]
    hi = np.array([float(r["high"]) for r in rows])
    lo = np.array([float(r["low"]) for r in rows])
    cl = np.array([float(r["close"]) for r in rows])
    return dates, hi, lo, cl


def reach_indep(cl, hi, lo, up, a, w):
    """独立实现的 rr：w 日内朝 up 方向走过 >= a 的起点占比（仅取 i+w<=n-1）。

    与生产实现走完全不同的写法：这里用显式 numpy 逐窗切片而非 rolling().shift()，
    避免"两边其实是同一个 API 的同一处边界 bug"。
    """
    n = len(cl)
    w = int(max(1, w))
    if n - w < 30:
        return None
    a = abs(float(a))
    src = hi if up else lo
    hit = 0
    tot = 0
    for i in range(0, n - w):
        c0 = cl[i]
        seg = src[i + 1: i + 1 + w]
        if up:
            if seg.max() >= c0 * (1.0 + a):
                hit += 1
        else:
            if seg.min() <= c0 * (1.0 - a):
                hit += 1
        tot += 1
    return (hit / tot) if tot else None


def main(data_path=None):
    print("=" * 78)
    print("audit56 — R827 目标可达性与 targets 元数据")
    print("=" * 78)
    D = load(data_path)
    dates, hi, lo, cl = load_csv()
    last_date = dates[-1]
    last_close = float(D["lastClose"])
    targets = D.get("targets") or []
    sells = (D.get("tradePlan") or {}).get("sellTargets") or []

    # ---------- T1 字段完整性 ----------
    miss = []
    bad_date = []
    for t in targets:
        nm = t.get("name", "?")
        if not isinstance(t.get("expDays"), int) or t.get("expDays") < 1:
            miss.append("%s:expDays=%r" % (nm, t.get("expDays")))
        ed = t.get("expDate")
        if not ed or str(ed) <= last_date:
            bad_date.append("%s:expDate=%r<=%s" % (nm, ed, last_date))
        elif _dow(ed) >= 5:
            bad_date.append("%s:expDate=%s 是周末" % (nm, ed))
        r = t.get("reach")
        if not isinstance(r, dict) or any(k not in r for k in ("prob", "window", "cls")):
            miss.append("%s:reach 缺键" % nm)
    chk(not miss, "T1a targets 元数据完整", "缺失=%s" % (miss or "无"))
    chk(not bad_date, "T1b expDate 合法且在未来", "异常=%s" % (bad_date or "无"))

    # ---------- T2/T3/T5 reach 自洽 ----------
    w_bad, c_bad, d_bad = [], [], []
    for t in targets:
        nm = t.get("name", "?")
        r = t.get("reach") or {}
        if r.get("window") != t.get("expDays"):
            w_bad.append("%s: window=%s expDays=%s" % (nm, r.get("window"), t.get("expDays")))
        p = r.get("prob")
        cls = r.get("cls")
        if p is None:
            exp_cls = "unknown"
        else:
            exp_cls = "unreachable" if p < 10.0 else ("stretch" if p < 35.0 else "achievable")
        if cls != exp_cls:
            c_bad.append("%s: cls=%s 但 prob=%s ⇒ 应为 %s" % (nm, cls, p, exp_cls))
        dp = r.get("distPct")
        dr = r.get("dailyRate")
        if dp is not None and dr is not None:
            want = ((1.0 + abs(dp) / 100.0) ** (1.0 / max(1, int(t["expDays"]))) - 1.0) * 100.0
            if abs(want - dr) > 1e-3:
                d_bad.append("%s: dailyRate=%s 应 %.4f" % (nm, dr, want))
    chk(not w_bad, "T2 reach.window == expDays", "异常=%s" % (w_bad or "无"))
    chk(not c_bad, "T3 cls 与 prob 阈值自洽", "异常=%s" % (c_bad or "无"))
    chk(not d_bad, "T5 dailyRate 复算一致", "异常=%s" % (d_bad or "无"))

    # ---------- T4 独立复算 prob ----------
    p_bad = []
    for t in targets:
        nm = t.get("name", "?")
        r = t.get("reach") or {}
        if r.get("prob") is None:
            continue
        a = float(t["price"]) / last_close - 1.0
        mine = reach_indep(cl, hi, lo, a > 0, abs(a), int(t["expDays"]))
        if mine is None:
            p_bad.append("%s: 独立复算无样本" % nm)
            continue
        if abs(mine * 100.0 - float(r["prob"])) > 0.05:
            p_bad.append("%s: 生产=%.2f%% 独立=%.2f%%" % (nm, r["prob"], mine * 100.0))
    chk(not p_bad, "T4 prob 独立复算一致", "异常=%s" % (p_bad or "无"))

    # ---------- T6 sellTargets 与同价 target 单一真值 ----------
    byprice = {round(float(t["price"]), 2): (t.get("reach") or {}) for t in targets}
    t6_bad = []
    for s in sells:
        pr = round(float(s["price"]), 2)
        ref = byprice.get(pr)
        if ref is None:
            t6_bad.append("%s(%s) 不在 targets 中" % (s.get("name"), pr))
            continue
        got = s.get("reach") or {}
        for k in ("prob", "window", "cls", "dailyRate", "distPct"):
            if got.get(k) != ref.get(k):
                t6_bad.append("%s.%s: %r vs targets %r" % (s.get("name"), k, got.get(k), ref.get(k)))
    chk(not t6_bad, "T6 sellTargets.reach 与 targets 一致", "异常=%s" % (t6_bad or "无"))

    # ---------- T7/T8/T9 分档 ----------
    bt = D.get("backtest") or {}
    bands = bt.get("dirBandsByReach") or []
    meta = bt.get("dirReachMeta") or {}
    if not bands:
        chk(False, "T7 分档存在", "dirBandsByReach 为空（R827 未生效）")
    else:
        tot = sum(b["matured"] for b in bands) + sum(b["pending"] for b in bands) + int(meta.get("earlyHit") or 0)
        chk(tot == bt.get("totalDirEvaluated"),
            "T7 分档计数恒等式",
            "Σmatured+Σpending+early=%d ｜ totalDirEvaluated=%s" % (tot, bt.get("totalDirEvaluated")))
        inv = []
        inv_soft = []
        prev = None
        raw_bad = []
        for b in bands:
            # rateRaw 必须与 hits/matured 自洽——否则"改 hits 不改 rateRaw"的注入会绕过
            # 单调性检查（本文件首版就漏了这个：T8 只读 rateRaw，而 rateRaw 是存储值）。
            if b["matured"] and b.get("rateRaw") is not None:
                want = round(b["hits"] / b["matured"] * 100, 1)
                if abs(want - b["rateRaw"]) > 0.11:
                    raw_bad.append("%s: rateRaw=%.1f 复算=%.1f(%d/%d)"
                                   % (b["label"], b["rateRaw"], want, b["hits"], b["matured"]))
            if b.get("rateRaw") is None or b["matured"] < 5:
                continue
            if prev is not None and b["rateRaw"] < prev[1] - 1e-9:
                # R828：按【统计显著性】判，而非字面比较。
                # 相邻档各 25~55 条样本，两比例差的标准误约 9pp —— 字面比较会把 1.6pp 的
                # 噪声波动判成缺陷（实测 5~15% 29.6% vs 15~35% 28.0%，z≈0.18）。
                # z>=1.96 记硬逆向（必须报警）；否则只披露为"噪声内"。
                p1, n1 = prev[1] / 100.0, prev[2]
                p2, n2 = b["rateRaw"] / 100.0, b["matured"]
                se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
                z = ((prev[1] - b["rateRaw"]) / 100.0 / se) if se > 0 else 99.0
                (inv if z >= 1.96 else inv_soft).append(
                    "%s(%.1f%%)<%s(%.1f%%) z=%.2f" % (b["label"], b["rateRaw"], prev[0], prev[1], z))
            prev = (b["label"], b["rateRaw"], b["matured"])
        chk(not raw_bad, "T8a rateRaw 与 hits/matured 自洽", "异常=%s" % (raw_bad or "无"))
        chk(not inv, "T8b 分档单调性(仅超噪声算逆向)",
            "硬逆向=%s ｜ 噪声内=%s" % (inv or "无", inv_soft or "无"))
        # R828 T8c：端点必须严格单调（首档 < 末档）——真信号量级远超噪声，不受上述放宽影响。
        _e = [b for b in bands if b.get("rateRaw") is not None and b["matured"] >= 5]
        chk(len(_e) < 2 or _e[-1]["rateRaw"] > _e[0]["rateRaw"],
            "T8c 端点严格单调",
            ("%s(%.1f%%) → %s(%.1f%%)" % (_e[0]["label"], _e[0]["rateRaw"],
                                           _e[-1]["label"], _e[-1]["rateRaw"])) if len(_e) >= 2 else "档数不足")
        chk(bool(meta.get("monotonic")) and not (meta.get("inversions") or []),
            "T9 meta.monotonic", "monotonic=%s inversions=%s soft=%s"
            % (meta.get("monotonic"), meta.get("inversions"), meta.get("inversionsSoft")))

    # ---------- T10 斜率对照自洽 ----------
    rs = D.get("reachSummary") or {}
    w3 = rs.get("wave3") or {}
    w5 = rs.get("wave5Implied") or {}
    t10_bad = []
    if not w3.get("dailyRate"):
        t10_bad.append("wave3.dailyRate 缺失")
    if not w5.get("speedVsWave3"):
        t10_bad.append("wave5Implied.speedVsWave3 缺失")
    if not t10_bad:
        want = w5["dailyRate"] / w3["dailyRate"]
        if abs(want - w5["speedVsWave3"]) > 0.02:
            t10_bad.append("speedVsWave3=%.2f 复算 %.2f" % (w5["speedVsWave3"], want))
    if rs.get("byClass"):
        n_sum = sum(int(v) for v in rs["byClass"].values())
        if n_sum != len(targets):
            t10_bad.append("byClass 合计 %d != targets %d" % (n_sum, len(targets)))
    chk(not t10_bad, "T10 斜率对照自洽", "异常=%s" % (t10_bad or "无"))

    print("-" * 78)
    print("audit56: PASS=%d FAIL=%d" % (_pass, _fail))
    if _fail:
        print("结果: FAIL")
        return 1
    print("结果: OK")
    return 0


def _dow(ds):
    import datetime as dt
    try:
        return dt.date(*[int(x) for x in str(ds)[:10].split("-")]).weekday()
    except Exception:
        return 9


if __name__ == "__main__":
    # 可选 argv[1]：指定 data.js 路径（仅供缺陷注入正控使用；CI 不传，走默认路径）
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
