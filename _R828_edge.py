# -*- coding: utf-8 -*-
"""R828 决定性分析：忠实观察窗 + 严格配对基线 → 引擎到底有没有胜率

三个口径缺陷叠加方向不同，必须一次算清：
  ① 删失（R826）：未闭合记为未做到 → 偏低
  ② 选择（成熟集只覆盖 2026-08~09）→ 不代表当前
  ③ 放宽（max(30,expDays)）：短期预测被给 3~7.5 倍时间 → 偏高

结论指标：在【引擎自己的承诺窗】内、在【同批成熟记录】上，
        引擎命中率 vs 该目标自身的零信息基线（逐条配对），差值 = 真实优势。

自校验：必须先复现生产 4 个已发布数字，否则全盘作废。
"""
import csv
import json
import sys

import numpy as np

CSV = "data/sh000001.csv"
LOG = "data/predictions_log.jsonl"
BT = "data/backtest.json"
HORIZON = 30


def main():
    rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
    dates = [r["date"] for r in rows]
    high = np.array([float(r["high"]) for r in rows])
    low = np.array([float(r["low"]) for r in rows])
    close = np.array([float(r["close"]) for r in rows])
    n = len(dates)
    di = {d: i for i, d in enumerate(dates)}
    recs = [json.loads(l) for l in open(LOG, encoding="utf-8") if l.strip()]
    bt = json.load(open(BT, encoding="utf-8"))

    # 预先构建滑窗极值（按窗口长度缓存）
    _rmax, _rmin = {}, {}

    def win_ext(w):
        if w not in _rmax:
            mx = np.full(n, np.nan)
            mn = np.full(n, np.nan)
            for i in range(0, n - w):
                mx[i] = high[i + 1:i + 1 + w].max()
                mn[i] = low[i + 1:i + 1 + w].min()
            _rmax[w], _rmin[w] = mx, mn
        return _rmax[w], _rmin[w]

    def hz_of(r, faithful):
        e = r.get("expDays")
        e = int(e) if e else HORIZON
        return max(1, e) if faithful else max(HORIZON, e)

    def evaluate(faithful):
        out = []
        for r in recs:
            i0 = di.get(r.get("date"))
            if i0 is None:
                continue
            c0 = close[i0]
            px = float(r["price"])
            d = px - c0
            if abs(d) <= 0.005 * c0 or i0 + 1 >= n:
                continue
            w = hz_of(r, faithful)
            mx, mn = win_ext(w)
            m = (i0 + w) <= (n - 1)
            # ⚠ 未闭合时 mx[i0] 为 NaN（滑窗极值只在 i+w<=n-1 有定义）。
            #   首版把它当"无数据"直接 continue ⇒ 等于把全部删失样本删掉，
            #   全窗口口径退化成成熟口径（复算 44.9% vs 生产 18.4%）。
            #   正确做法与生产一致：用 mint 后的残缺窗判定（win = df.iloc[i0+1 : min(n, i0+1+w)]）。
            if np.isnan(mx[i0]):
                j1 = min(n, i0 + 1 + w)
                seg_hi = high[i0 + 1:j1]
                seg_lo = low[i0 + 1:j1]
                if seg_hi.size == 0:
                    continue
                v = bool(seg_hi.max() >= c0 + 0.5 * d) if d > 0 else bool(seg_lo.min() <= c0 + 0.5 * d)
                base = None          # 基线只能用完整窗，未闭合无基线
            else:
                v = bool(mx[i0] >= c0 + 0.5 * d) if d > 0 else bool(mn[i0] <= c0 + 0.5 * d)
                # 该目标自身的零信息基线：全历史里「同样走完 50% 距离」的起点占比。
                # ⚠ 阈值必须【相对每个起点自己的收盘】换算（close[i]*(1+0.5*g)），
                #   首版写成绝对价 need=c0+0.5*d ⇒ 把问题变成"历史上有没有到过这个价位"，
                #   与生产 dir_baseline 的口径不同（实测 43.9% vs 正解 56.9%）。
                g = d / c0
                lvl = close * (1.0 + 0.5 * g)
                ok = (mx >= lvl) if d > 0 else (mn <= lvl)
                mm = ~np.isnan(mx)
                base = float(ok[mm].mean()) if mm.any() else None
            out.append({"r": r, "v": v, "m": m, "base": base, "w": w,
                        "gap": d / c0, "side": r.get("side") or "?"})
        return out

    def pct(h, k):
        return round(h / k * 100, 1) if k else None

    print("=" * 78)
    print("R828 决定性分析：引擎优势 = 命中率 − 同批配对基线")
    print("=" * 78)

    cur = evaluate(False)
    fai = evaluate(True)

    # ---- 自校验（复现生产已发布数字）----
    c_all = [x for x in cur]
    c_mat = [x for x in cur if x["m"]]
    c_pend = sum(1 for x in cur if not x["m"] and not x["v"])
    c_early = sum(1 for x in cur if not x["m"] and x["v"])
    chk = [
        ("全窗口", pct(sum(x["v"] for x in c_all), len(c_all)), bt.get("dirRealizedHitRate")),
        ("成熟", pct(sum(x["v"] for x in c_mat), len(c_mat)), bt.get("dirRealizedHitRateMatured")),
        ("pending", c_pend, bt.get("dirPendingCount")),
        ("early", c_early, bt.get("dirEarlyHitCount")),
    ]
    bad = 0
    for lbl, a, b in chk:
        ok = (a is not None and b is not None and abs(a - b) <= 0.2)
        print("  [自校验] %-8s 复算=%-7s 生产=%-7s %s" % (lbl, a, b, "OK" if ok else "FAIL"))
        bad += (not ok)
    if bad:
        print("\n!! 自校验失败，结论作废 !!")
        return 1
    print()

    def report(tag, data):
        mat = [x for x in data if x["m"]]
        paid = [x for x in mat if x["base"] is not None]
        e = pct(sum(x["v"] for x in paid), len(paid))
        b = round(sum(x["base"] for x in paid) / len(paid) * 100, 1) if paid else None
        print("  %s" % tag)
        print("    成熟 %d 条 / 配对可用 %d 条 ｜ 引擎 %s%% ｜ 配对基线 %s%% ｜ 优势 %+.1f pp"
              % (len(mat), len(paid), e, b, (e - b) if (e is not None and b is not None) else 0))
        return mat, paid, e, b

    print("  A. 两口径对比（均为：同批成熟记录 + 逐条配对基线）")
    report("① 现口径 max(30,expDays)", cur)
    print()
    report("② 忠实口径 expDays", fai)
    print()

    # ---- B. 按 side（忠实口径）----
    print("  B. 忠实口径 · 按 side")
    for s in ("sell", "buy", "hold"):
        paid = [x for x in fai if x["m"] and x["base"] is not None and x["side"] == s]
        if not paid:
            print("    %-6s 无样本" % s)
            continue
        e = pct(sum(x["v"] for x in paid), len(paid))
        b = round(sum(x["base"] for x in paid) / len(paid) * 100, 1)
        print("    %-6s n=%-3d 引擎 %5.1f%% ｜ 配对基线 %5.1f%% ｜ 优势 %+.1f pp"
              % (s, len(paid), e, b, e - b))
    print()

    # ---- C. 按 ecpDays 组（忠实口径）----
    print("  C. 忠实口径 · 按承诺窗长短分组")
    for lbl, lo, hi in (("短期 <30日", 0, 30), ("中期 30~60日", 30, 60), ("长期 >=60日", 60, 10 ** 6)):
        paid = [x for x in fai if x["m"] and x["base"] is not None and lo <= x["w"] < hi]
        if not paid:
            print("    %-14s 无样本" % lbl)
            continue
        e = pct(sum(x["v"] for x in paid), len(paid))
        b = round(sum(x["base"] for x in paid) / len(paid) * 100, 1)
        print("    %-14s n=%-3d 引擎 %5.1f%% ｜ 配对基线 %5.1f%% ｜ 优势 %+.1f pp"
              % (lbl, len(paid), e, b, e - b))
    print()

    # ---- D. 汇总判决 ----
    paid = [x for x in fai if x["m"] and x["base"] is not None]
    e = pct(sum(x["v"] for x in paid), len(paid))
    b = round(sum(x["base"] for x in paid) / len(paid) * 100, 1)
    print("  D. 判决（忠实口径 · 配对）")
    print("     引擎 %.1f%% vs 配对基线 %.1f%% → %+.1f pp" % (e, b, e - b))
    print("     胜/负：%s" % ("有增量" if e > b else "无增量（劣于同目标随机起点）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
