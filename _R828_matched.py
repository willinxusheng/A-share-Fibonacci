# -*- coding: utf-8 -*-
"""R828 · 引擎命中率 vs 零信息基线 的【严格配对】比较

背景（R826 遗留）：
  R826 把「零信息基线」改成了成熟度对齐（只取未来 w 天完整的起算点），
  但引擎侧上报的 dirRealizedHitRate(18.4%) 仍然是**全窗口**口径（含 353 条未闭合 = 删失）。
  ⇒ 前端把 18.4% 和 31.4% 并排显示，读者必然得出「引擎远差于随机」，
    而这个差距至少有一部分是两个分母不同造成的。

本探针：把引擎记录也限制到「未来窗完整闭合」的样本，与基线取同一批记录，
        逐条配对（每条记录自带 dirBase）后比较。这才是可比的。

本脚本不 import 生产模块（只用标准库 + numpy/pdf 自算），并要求自校验通过才输出结论。
"""
import csv
import json
import math
import sys

import numpy as np

CSV = "data/sh000001.csv"
LOG = "data/predictions_log.jsonl"
BT = "data/backtest.json"
DATA_JS = "data/data.js"

HORIZON = 30  # 与生产 MIN window 同界


def load_csv():
    rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
    dates = [r["date"] for r in rows]
    high = np.array([float(r["high"]) for r in rows])
    low = np.array([float(r["low"]) for r in rows])
    close = np.array([float(r["close"]) for r in rows])
    return dates, high, low, close


def load_log():
    return [json.loads(l) for l in open(LOG, encoding="utf-8") if l.strip()]


def load_bt():
    return json.load(open(BT, encoding="utf-8"))


def main():
    dates, high, low, close = load_csv()
    n = len(dates)
    di = {d: i for i, d in enumerate(dates)}
    recs = load_log()
    bt = load_bt()

    # ---------- 逐条判定（与生产同判据，但独立实现） ----------
    # dirCorrect : 窗内朝 Δ 方向推进 >= 50%*|Δ|
    # matured    : 未来窗完整（i+1+w <= n-1）
    for r in recs:
        i0 = di.get(r.get("date"))
        if i0 is None:
            r["_i"] = None
            r["_v"] = None
            r["_m"] = None
            continue
        px = float(r["price"])
        c0 = close[i0]
        w = max(HORIZON, int(r.get("expDays") or HORIZON))
        d = px - c0
        j1 = min(n, i0 + 1 + w)
        r["_i"] = i0
        r["_w"] = w
        r["_gap"] = d / c0
        if j1 <= i0 + 1 or abs(d) <= 0.005 * c0:
            r["_v"] = None
            r["_m"] = None
            continue
        r["_v"] = bool(max(high[i0 + 1:j1]) >= c0 + 0.5 * d) if d > 0 else bool(min(low[i0 + 1:j1]) <= c0 + 0.5 * d)
        # ⚠ 边界必须与生产逐字一致：生产 window = df.iloc[i0+1 : i0+1+w]，
        #   matured = (i0 + w) <= n-1（end 端 i0+1+w <= n 即完整）。
        #   首版写成 i0+1+w <= n-1（多严一天）⇒ matured 134 vs 生产 138、pending 354 vs 353。
        r["_m"] = bool(i0 + w <= n - 1)

    valid = [r for r in recs if r["_v"] is not None]
    strict = [r for r in valid if r["_m"]]          # 未来窗完整闭合
    censored = [r for r in valid if not r["_m"]]     # 窗未闭合

    def rate(xs):
        if not xs:
            return None
        return round(sum(1 for x in xs if x["_v"]) / len(xs) * 100, 1)

    print("=" * 78)
    print("R828 严格配对：引擎 vs 零信息基线")
    print("=" * 78)
    print("  可判样本 %d ｜ 已闭合(严格) %d ｜ 未闭合(删失) %d"
          % (len(valid), len(strict), len(censored)))

    # ---------- 自校验：与生产上报对表 ----------
    prod_all = bt.get("dirRealizedHitRate")
    prod_mat = bt.get("dirRealizedHitRateMatured")
    # 生产 matured 口径 = 早达成(即使未闭合) 或 窗已闭合；此处只复现"严格闭合"以便区分
    early = [r for r in censored if r["_v"]]
    # 生产是【严格对称】：只有窗已闭合才计入 matured 口径，早达成另记 dirEarlyHit 且不进比率。
    mat_set = strict
    rep_all = rate(valid)
    rep_mat = rate(mat_set)
    rep_pending = len([r for r in censored if not r["_v"]])
    ok_all = prod_all is not None and abs(rep_all - prod_all) <= 0.2
    ok_mat = prod_mat is not None and abs(rep_mat - prod_mat) <= 0.2
    ok_pend = rep_pending == bt.get("dirPendingCount")
    ok_early = len(early) == bt.get("dirEarlyHitCount")
    print("  [自校验] 全窗口  复算=%.1f 生产=%s  %s" % (rep_all, prod_all, "OK" if ok_all else "FAIL"))
    print("  [自校验] 成熟口径 复算=%.1f 生产=%s  %s" % (rep_mat, prod_mat, "OK" if ok_mat else "FAIL"))
    print("  [自校验] pending 复算=%d 生产=%s  %s" % (rep_pending, bt.get("dirPendingCount"), "OK" if ok_pend else "FAIL"))
    print("  [自校验] early   复算=%d 生产=%s  %s" % (len(early), bt.get("dirEarlyHitCount"), "OK" if ok_early else "FAIL"))
    if not (ok_all and ok_mat and ok_pend and ok_early):
        print("\n!! 自校验失败，后续读数全部作废 !!")
        return 1
    print()

    # ---------- A. 严格闭合集上的配对比较 ----------
    # dirBase 只在 aggregate() 内存中赋值、不落盘，故此处独立复算基线。
    # 独立实现：按 distinct w 预构建滑窗极值，再逐 key 求历史达成率。
    Ws = sorted({r["_w"] for r in valid})
    rmax, rmin, fullmask = {}, {}, {}
    for w in Ws:
        m = np.full(n, np.nan)
        lo = np.full(n, np.nan)
        for i in range(0, n - w):          # 需 i+1+w <= n  ⇒ i <= n-w-1
            m[i] = high[i + 1:i + 1 + w].max()
            lo[i] = low[i + 1:i + 1 + w].min()
        rmax[w], rmin[w] = m, lo
        fullmask[w] = ~np.isnan(m)

    keycache = {}

    def base_of(gap, w):
        k = (round(float(gap), 4), int(w))
        if k not in keycache:
            up = k[0] > 0
            arr = rmax[k[1]] if up else rmin[k[1]]
            need = close * (1.0 + 0.5 * abs(k[0])) * (1 if up else 1)
            if up:
                ok = arr >= close * (1.0 + 0.5 * k[0])
            else:
                ok = arr <= close * (1.0 + 0.5 * k[0])
            mm = fullmask[k[1]]
            keycache[k] = float(ok[mm].mean()) if mm.any() else None
        return keycache[k]

    for r in valid:
        r["_base"] = base_of(r["_gap"], r["_w"])

    # 自校验：全历史基线应复现生产 dirBaseRate
    allb = [v for v in keycache.values() if v is not None]
    rep_glob = round(sum(allb) / len(allb) * 100, 1) if allb else None
    ok_glob = rep_glob is not None and abs(rep_glob - (bt.get("dirBaseRate") or -1)) <= 0.2
    print("  [自校验] 基线(每key一票) 复算=%s 生产=%s  %s"
          % (rep_glob, bt.get("dirBaseRate"), "OK" if ok_glob else "FAIL"))

    paired = [r for r in strict if r["_base"] is not None]
    eng = rate(paired)
    base_rec = round(sum(r["_base"] for r in paired) / len(paired) * 100, 1) if paired else None
    _keys = {}
    for r in paired:
        _keys.setdefault((round(r["_gap"], 4), r["_w"]), r["_base"])
    base_key = round(sum(_keys.values()) / len(_keys) * 100, 1) if _keys else None
    lift_rec = round(eng - base_rec, 1) if (eng is not None and base_rec is not None) else None
    lift_key = round(eng - base_key, 1) if (eng is not None and base_key is not None) else None
    print()
    print("  A. 严格闭合集 · 同批记录配对（%d 条 / %d 个独立目标）" % (len(paired), len(_keys)))
    print("     引擎 %s%% ｜ 基线(每条一票) %s%% → 差 %+s pp"
          % (eng, base_rec, lift_rec))
    print("     引擎 %s%% ｜ 基线(每key一票) %s%% → 差 %+s pp"
          % (eng, base_key, lift_key))
    print()

    # ---------- B. 不同口径下"引擎 vs 基线"的差值矩阵 ----------
    print("  B. 差值是口径的产物（同一份数据，换个分母结论就变）")
    print("     %-34s %-8s %-8s %s" % ("口径", "引擎", "基线", "差"))
    rows = [
        ("① 全窗口引擎 vs 成熟基线（前端现用）", rep_all, bt.get("dirBaseRateRecent")),
        ("② 成熟引擎(严格对称) vs 成熟基线", rep_mat, bt.get("dirBaseRateRecent")),
        ("③ 成熟引擎 vs 同批配对基线(每条)", rep_mat, base_rec),
        ("③b 成熟引擎 vs 同批配对基线(每key)", rep_mat, base_key),
        ("④ 全窗口引擎 vs 全历史基线", rep_all, bt.get("dirBaseRate")),
    ]
    for lbl, e, b in rows:
        if e is None or b is None:
            continue
        print("     %-34s %-8s %-8s %+.1f pp" % (lbl, str(e) + "%", str(b) + "%", e - b))
    print()

    # ---------- C. 按 side 拆分严格集 ----------
    print("  C. 严格闭合集 · 按 side")
    by = {}
    for r in paired:
        s = r.get("side") or "?"
        a = by.setdefault(s, [0, 0, []])
        a[0] += 1
        a[1] += bool(r["_v"])
        a[2].append(r["_base"])
    for s in sorted(by):
        c, h, bs = by[s]
        print("     %-6s n=%-3d 命中 %-3d = %5.1f%% ｜ 配对基线 %5.1f%% ｜ 差 %+.1f pp"
              % (s, c, h, h / c * 100, sum(bs) / len(bs) * 100, h / c * 100 - sum(bs) / len(bs) * 100))
    print()

    # ---------- D. 严格集里被剔除的样本有多少（选择偏差的量级） ----------
    dropped = [r for r in valid if not r["_m"]]
    print("  D. 严格集丢了什么")
    print("     未闭合被剔除 %d 条（占可判 %.0f%%），其中已达成 %d 条 ⇒ 移除的是【成功者】"
          % (len(dropped), len(dropped) / len(valid) * 100, len(early)))
    print("     ⇒ 严格集本身含负向选择偏差（成功者被系统剔除），所以 A 的差值应读作【下界】")
    return 0


if __name__ == "__main__":
    sys.exit(main())
