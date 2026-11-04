# -*- coding: utf-8 -*-
"""R828 · 观察窗口径：max(HORIZON, expDays) 对短期限预测是「放宽承诺」

生产：_hz = max(30, expDays)
  - expDays >= 30 的目标：窗 = 承诺窗（忠实）
  - expDays <  30 的目标：窗 = 30 天（比承诺宽 1.7~3.75 倍）→ 命中更容易，比率被抬高

本探针在同一判据下比较两种窗口径，并按 side / expDays 分组量化影响。
自校验：current 口径必须复现生产 dirRealizedHitRate / matured 等。
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

    def judge(r, hz):
        i0 = di.get(r.get("date"))
        if i0 is None:
            return None, None
        c0 = close[i0]
        px = float(r["price"])
        d = px - c0
        if abs(d) <= 0.005 * c0 or i0 + 1 >= n:
            return None, None
        j1 = min(n, i0 + 1 + hz)
        v = bool(high[i0 + 1:j1].max() >= c0 + 0.5 * d) if d > 0 else bool(low[i0 + 1:j1].min() <= c0 + 0.5 * d)
        m = (i0 + hz) <= (n - 1)
        return v, m

    def agg(hz_fn):
        out = {"all": [0, 0], "mat": [0, 0], "pend": 0, "early": 0}
        for r in recs:
            hz = hz_fn(r)
            v, m = judge(r, hz)
            if v is None:
                continue
            out["all"][0] += 1
            out["all"][1] += v
            if m:
                out["mat"][0] += 1
                out["mat"][1] += v
            else:
                if v:
                    out["early"] += 1
                else:
                    out["pend"] += 1
        return out

    cur = agg(lambda r: max(HORIZON, int(r.get("expDays") or HORIZON)))
    fai = agg(lambda r: max(1, int(r.get("expDays") or HORIZON)))

    def pct(a):
        return round(a[1] / a[0] * 100, 1) if a[0] else None

    print("=" * 78)
    print("R828 观察窗口径：max(30,expDays)  vs  expDays（忠实承诺）")
    print("=" * 78)

    # ---- 自校验 ----
    checks = [
        ("全窗口", pct(cur["all"]), bt.get("dirRealizedHitRate")),
        ("成熟", pct(cur["mat"]), bt.get("dirRealizedHitRateMatured")),
        ("pending", cur["pend"], bt.get("dirPendingCount")),
        ("early", cur["early"], bt.get("dirEarlyHitCount")),
    ]
    bad = 0
    for lbl, a, b in checks:
        ok = (a is not None and b is not None and abs(a - b) <= 0.2)
        print("  [自校验] %-8s 复算=%-6s 生产=%-6s %s" % (lbl, a, b, "OK" if ok else "FAIL"))
        bad += (not ok)
    if bad:
        print("!! 自校验失败，后续作废 !!")
        return 1
    print()

    print("  A. 两口径总览")
    print("     %-10s %-18s %-18s" % ("", "max(30,expDays)", "expDays(忠实)"))
    print("     %-10s %-18s %-18s" % ("全窗口", "%s%% (n=%d)" % (pct(cur["all"]), cur["all"][0]),
                                     "%s%% (n=%d)" % (pct(fai["all"]), fai["all"][0])))
    print("     %-10s %-18s %-18s" % ("成熟", "%s%% (n=%d)" % (pct(cur["mat"]), cur["mat"][0]),
                                     "%s%% (n=%d)" % (pct(fai["mat"]), fai["mat"][0])))
    print("     %-10s %-18s %-18s" % ("pending", cur["pend"], fai["pend"]))
    print()

    # ---- B. 受影响的记录 ----
    hit_lt30 = [r for r in recs if max(1, int(r.get("expDays") or HORIZON)) < HORIZON]
    print("  B. 有多少条被「放宽」")
    print("     expDays < 30 的记录 %d 条（占 %d 条的 %.0f%%）"
          % (len(hit_lt30), len(recs), len(hit_lt30) / len(recs) * 100))
    if hit_lt30:
        ex = [int(r.get("expDays") or HORIZON) for r in hit_lt30]
        print("     其 expDays 范围 %d~%d 天 ⇒ 实际给了 30 天，放宽 %.1f~%.1f 倍"
              % (min(ex), max(ex), 30 / max(ex), 30 / min(ex)))
    print()

    # ---- C. 分组：宽松组 vs 忠实组 ----
    print("  C. 按是否被放宽分组（成熟样本）")
    grp = {"放宽(expDays<30)": [[0, 0], [0, 0]], "忠实(expDays>=30)": [[0, 0], [0, 0]]}
    for r in recs:
        e = max(1, int(r.get("expDays") or HORIZON))
        k = "放宽(expDays<30)" if e < HORIZON else "忠实(expDays>=30)"
        vc, mc = judge(r, max(HORIZON, e))
        vf, mf = judge(r, e)
        if vc is not None and mc:
            grp[k][0][0] += 1
            grp[k][0][1] += vc
        if vf is not None and mf:
            grp[k][1][0] += 1
            grp[k][1][1] += vf
    for k in grp:
        a, b = grp[k]
        print("     %-18s 现口径 %s%% (n=%d) → 忠实口径 %s%% (n=%d)"
              % (k, pct(a) if a[0] else "—", a[0], pct(b) if b[0] else "—", b[0]))
    print()

    # ---- D. 按 side（忠实口径） ----
    print("  D. 按 side · 两种口径的成熟命中率")
    by = {}
    for r in recs:
        s = r.get("side") or "?"
        e = max(1, int(r.get("expDays") or HORIZON))
        for tag, hz in (("cur", max(HORIZON, e)), ("fai", e)):
            v, m = judge(r, hz)
            if v is None or not m:
                continue
            d = by.setdefault((s, tag), [0, 0])
            d[0] += 1
            d[1] += v
    for s in ("sell", "buy", "hold"):
        a = by.get((s, "cur"), [0, 0])
        b = by.get((s, "fai"), [0, 0])
        print("     %-6s 现 %s%% (%d/%d)  → 忠实 %s%% (%d/%d)"
              % (s, pct(a) if a[0] else "—", a[1], a[0], pct(b) if b[0] else "—", b[1], b[0]))
    print()
    print("  E. 说明")
    print("     「忠实」口径下 pending 会大幅增加（短窗更早闭合？反之更晚未闭合）——")
    print("     注意：窗变短 ⇒ 更早闭合 ⇒ pending 减少、且命中更难。两者方向相反，须看实测。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
