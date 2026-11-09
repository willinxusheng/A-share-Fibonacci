#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R900 独立回测复算（不 import 项目任何模块，纯标准库）。

目的：第三方复核 data/backtest.json 的方向命中率 / 配对基线 / 精确命中率，
并额外做项目自身未做的两件事：
  1) 样本独立性检验 —— 同一目标价连续多日重复入库，是否把 1 个观测算成 N 个；
  2) 块级（episode）命中率与置信区间 —— 以「目标价实质变化」为切块依据的有效样本。

判据与 backtest.py 的 R822/R826 保持同义（便于对照），但实现完全独立：
  方向达成 = 观察窗 (i0, i0+hz] 内，价格朝 Δ=price-close0 方向推进 >= 50%|Δ|
  成熟     = i0 + hz <= n-1（未来 hz 根 K 线齐全，达成与否一视同仁才计入）
  精确命中 = 窗内 high>=price（上行）/ low<=price（下行）
  平凡样本 = 目标价在预测当日已被自身收盘满足（不含预测信息）

用法：python3 _R900_indep_backtest.py [--json]
"""
import json
import math
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "data", "sh000001.csv")
LOG = os.path.join(HERE, "data", "predictions_log.jsonl")
HORIZON = 30
BLOCK_EPS = 0.001          # 相对变化 >0.1% 才视为新目标（与 build 无关，本脚本自定）


def load_bars(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        head = f.readline().strip().split(",")
        for line in f:
            p = line.strip().split(",")
            if len(p) < 5:
                continue
            rows.append({
                "date": p[0],
                "open": float(p[1]), "close": float(p[2]),
                "high": float(p[3]), "low": float(p[4]),
            })
    return rows


def load_log(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def evaluate(bars, recs):
    dates = [b["date"] for b in bars]
    idx = {d: i for i, d in enumerate(dates)}
    n = len(bars)
    for r in recs:
        i0 = idx.get(r["date"])
        if i0 is None:
            r["_skip"] = "date_not_in_bars"
            continue
        px = float(r["price"])
        c0 = bars[i0]["close"]
        hz = max(1, int(r.get("expDays") or HORIZON))
        end = min(n, i0 + 1 + hz)
        matured = (i0 + hz) <= (n - 1)
        delta = px - c0
        win = bars[i0 + 1:end]
        r["_i0"] = i0
        r["_close0"] = c0
        r["_hz"] = hz
        r["_delta"] = delta
        r["_gap"] = delta / c0 if c0 else None
        r["_matured"] = matured
        if not win:
            r["_dir"] = None
            r["_precise"] = None
            continue
        hi = max(b["high"] for b in win)
        lo = min(b["low"] for b in win)
        if abs(delta) <= 0.005 * c0:
            r["_dir"] = None
        elif delta > 0:
            r["_dir"] = hi >= c0 + 0.5 * delta
        else:
            r["_dir"] = lo <= c0 + 0.5 * delta
        buy = r.get("side") == "buy"
        r["_precise"] = (lo <= px) if buy else (hi >= px)
        # 平凡：目标价在预测当日已被自收盘满足
        r["_trivial"] = (px >= c0) if buy else (px <= c0)
    return recs


def rate(num, den):
    return round(num / den * 100, 1) if den else None


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round((c - m) / d * 100, 1), round((c + m) / d * 100, 1))


def binom_p(k, n, p0):
    """单侧二项检验 P(X<=k | p0)（检验引擎是否优于基线）。纯标准库。"""
    if not n:
        return None
    s = 0.0
    for i in range(k + 1):
        s += math.comb(n, i) * (p0 ** i) * ((1 - p0) ** (n - i))
    return round(s, 4)


def paired_baseline(bars, recs):
    """同批配对基线（双口径 × 双记录集）。保留每条记录的 |Δ|/c0 与 hz，在同一判据下复算：
      · baseHist  = 在【全历史】可完整观测起点上的达成率（≈ 项目 dirPairedBase 的口径）
      · baseSame  = 在【同期区间】可完整观测起点上的达成率（唯一能与同期引擎样本相减的口径）
    记录集两种（R903 修正）：
      · recordWeighted   = 全部有基线值的记录（参考值，与项目口径【不可】直接相减）
      · maturedWeighted  = 仅【成熟】记录（与项目 dirPairedBaseSame 同法，唯一可相减口径；
        R900 初版误用全记录加权得出"+12.3pp 假优势"，与项目 -2.9pp 矛盾——根因是
        未成熟记录的 (gap,hz) 键分布不同，把基线拉低，与成熟引擎率相减即错配。）"""
    dates = [b["date"] for b in bars]
    idx = {d: i for i, d in enumerate(dates)}
    n = len(bars)
    i0s = [idx[r["date"]] for r in recs if r["date"] in idx]
    if not i0s:
        return {}
    start = max(0, min(min(i0s), n - 1 - 60))
    per_key = {}
    for r in recs:
        if r.get("_gap") is None or abs(r["_gap"]) <= 0.005 or r.get("_dir") is None:
            continue
        key = (round(r["_gap"], 4), r["_hz"])
        if key not in per_key:
            up = key[0] > 0
            w = key[1]
            hh = hn = sh = sn = 0
            for i in range(0, n):
                if i + w > n - 1:          # 成熟度对齐：未来 w 天须完整
                    continue
                c = bars[i]["close"]
                need = c * (1 + 0.5 * key[0])
                if up:
                    ok = max(bars[j]["high"] for j in range(i + 1, i + 1 + w)) >= need
                else:
                    ok = min(bars[j]["low"] for j in range(i + 1, i + 1 + w)) <= need
                hh += 1 if ok else 0
                hn += 1
                if i >= start:
                    sh += 1 if ok else 0
                    sn += 1
            per_key[key] = (rate(hh, hn), rate(sh, sn))
        r["_baseHist"], r["_baseSame"] = per_key[key]
    out = {}
    for tag, field in (("Hist", "_baseHist"), ("Same", "_baseSame")):
        vals_all = [r[field] for r in recs if r.get(field) is not None]
        vals_mat = [r[field] for r in recs if r.get(field) is not None and r.get("_matured")]
        # 全记录加权（参考）；成熟记录加权（与项目 _paired_base / _paired_base_same 同法）
        recw = round(sum(vals_all) / len(vals_all), 1) if vals_all else None
        matw = round(sum(vals_mat) / len(vals_mat), 1) if vals_mat else None
        # 键等权（与项目 dirBaseRate / dirBaseRateRecent 同法）
        kv = {k: v[0 if tag == "Hist" else 1] for k, v in per_key.items()}
        kvv = [x for x in kv.values() if x is not None]
        out[tag] = {"recordWeighted": recw, "maturedWeighted": matw,
                    "keyMean": round(sum(kvv) / len(kvv), 1) if kvv else None,
                    "keys": len(kvv)}
    return out


def blocks(recs):
    """按 (cat,key) + price 实质变化切块，块内只保留首个成熟观测作代表。"""
    by = defaultdict(list)
    for r in recs:
        if r.get("_i0") is None:
            continue
        by[(r.get("cat"), r.get("key"))].append(r)
    out = []
    for k, v in by.items():
        v.sort(key=lambda r: r["date"])
        prev = None
        cur = []
        for r in v:
            p = float(r["price"])
            if prev is None or abs(p - prev) / prev > BLOCK_EPS:
                if cur:
                    out.append((k, cur))
                cur = [r]
                prev = p
            else:
                cur.append(r)
        if cur:
            out.append((k, cur))
    return out


def main():
    as_json = "--json" in sys.argv
    bars = load_bars(CSV)
    recs = load_log(LOG)
    evaluate(bars, recs)
    ok = [r for r in recs if r.get("_i0") is not None]
    dirs = [r for r in ok if r.get("_dir") is not None]
    mat = [r for r in dirs if r["_matured"]]

    res = {"asOf": bars[-1]["date"], "bars": len(bars), "logged": len(recs),
           "dirJudgeable": len(dirs), "dirMaturedN": len(mat)}

    # ---- 1. 方向命中率（两种口径，复现 backtest.json）----
    res["dirAllWindow"] = rate(sum(1 for r in dirs if r["_dir"] is True), len(dirs))
    res["dirMatured"] = rate(sum(1 for r in mat if r["_dir"] is True), len(mat))
    res["dirPending"] = sum(1 for r in dirs if r["_dir"] is False and not r["_matured"])

    # ---- 2. 精确命中（含/去平凡，成熟样本）----
    pmat = [r for r in ok if r.get("_precise") is not None and r["_matured"]]
    ntriv = [r for r in pmat if not r.get("_trivial")]
    res["preciseMatured"] = rate(sum(1 for r in pmat if r["_precise"]), len(pmat))
    res["preciseNonTrivial"] = rate(sum(1 for r in ntriv if r["_precise"]), len(ntriv))
    res["trivialShare"] = rate(len(pmat) - len(ntriv), len(pmat))
    res["preciseMaturedN"] = len(pmat)
    res["preciseNonTrivialN"] = len(ntriv)

    # ---- 3. 配对基线（全历史 vs 同期；成熟记录加权为唯一可相减口径）----
    bs = paired_baseline(bars, recs)
    base_hist = bs.get("Hist", {}).get("maturedWeighted")
    base_hist_all = bs.get("Hist", {}).get("recordWeighted")
    base_same = bs.get("Same", {}).get("maturedWeighted")
    base_same_all = bs.get("Same", {}).get("recordWeighted")
    res["baseHistMatW"] = base_hist
    res["baseHistRecW"] = base_hist_all
    res["baseSameMatW"] = base_same
    res["baseSameRecW"] = base_same_all
    res["baseHistKeyMean"] = bs.get("Hist", {}).get("keyMean")
    res["baseSameKeyMean"] = bs.get("Same", {}).get("keyMean")
    res["edgeVsHist"] = round(res["dirMatured"] - base_hist, 1) if (base_hist and res["dirMatured"] is not None) else None
    res["edgeVsSame"] = round(res["dirMatured"] - base_same, 1) if (base_same and res["dirMatured"] is not None) else None

    # ---- 4. 样本独立性（项目缺这项）----
    bl = blocks(recs)
    res["blocks"] = len(bl)
    res["blockRatio"] = round(len(recs) / len(bl), 1) if bl else None
    # 块内方向一致性：同一块内 matured 记录的方向结论是否相同
    blk_hit = blk_miss = blk_mixed = blk_none = 0
    rep = []          # 每块代表值（首个成熟观测）
    for k, v in bl:
        mv = [r for r in v if r.get("_dir") is not None and r["_matured"]]
        if not mv:
            blk_none += 1
            continue
        vals = {r["_dir"] for r in mv}
        if vals == {True}:
            blk_hit += 1
            rep.append(True)
        elif vals == {False}:
            blk_miss += 1
            rep.append(False)
        else:
            blk_mixed += 1
            rep.append(mv[0]["_dir"])
    res["blockHit"] = blk_hit
    res["blockMiss"] = blk_miss
    res["blockMixed"] = blk_mixed
    res["blockNoMature"] = blk_none
    res["blockHitRate"] = rate(blk_hit + sum(1 for x in [blk_mixed] if x), blk_hit + blk_miss + blk_mixed) if (blk_hit + blk_miss + blk_mixed) else None
    # 更严格：块级代表 = 块内首个成熟观测的方向（不受块大小加权）
    res["blockRepRate"] = rate(sum(1 for x in rep if x is True), len(rep))
    res["blockRepN"] = len(rep)

    # ---- 5. 置信区间与显著性 ----
    k_mat = sum(1 for r in mat if r["_dir"] is True)
    res["dirMaturedCI"] = wilson(k_mat, len(mat))
    k_blk = sum(1 for x in rep if x is True)
    res["blockRepCI"] = wilson(k_blk, len(rep))
    if base_same is not None and len(rep):
        res["binom_p_vs_same"] = binom_p(k_blk, len(rep), base_same / 100.0)
    if base_hist is not None and len(rep):
        res["binom_p_vs_hist"] = binom_p(k_blk, len(rep), base_hist / 100.0)
    if len(rep):
        res["binom_p_vs_50"] = binom_p(k_blk, len(rep), 0.5)

    # ---- 6. 分 key 明细 ----
    per = defaultdict(lambda: {"n": 0, "hit": 0, "mat": 0, "mhit": 0,
                               "prec": 0, "pv": 0, "baseS_n": 0, "baseS_s": 0.0,
                               "baseH_n": 0, "baseH_s": 0.0, "gap": []})
    for r in dirs:
        d = per[r["key"]]
        d["n"] += 1
        d["gap"].append(abs(r["_gap"]) * 100 if r.get("_gap") is not None else 0)
        d["hit"] += 1 if r["_dir"] is True else 0
        if r["_matured"]:
            d["mat"] += 1
            d["mhit"] += 1 if r["_dir"] is True else 0
        # R903：基线取该 key 全部记录的均值（旧版取末条记录的值当 key 基线，是覆盖 bug）
        if r.get("_baseSame") is not None:
            d["baseS_n"] += 1
            d["baseS_s"] += r["_baseSame"]
        if r.get("_baseHist") is not None:
            d["baseH_n"] += 1
            d["baseH_s"] += r["_baseHist"]
    for r in pmat:
        per[r["key"]]["pv"] += 1
        per[r["key"]]["prec"] += 1 if r["_precise"] else 0
    res["byKey"] = {}
    for k, d in sorted(per.items()):
        g = sorted(d["gap"])
        baseS = round(d["baseS_s"] / d["baseS_n"], 1) if d["baseS_n"] else None
        baseH = round(d["baseH_s"] / d["baseH_n"], 1) if d["baseH_n"] else None
        res["byKey"][k] = {
            "n": d["n"], "matured": d["mat"],
            "dirMaturedRate": rate(d["mhit"], d["mat"]),
            "baseSame": baseS, "baseHist": baseH,
            "edgeVsSame": (round(rate(d["mhit"], d["mat"]) - baseS, 1)
                           if (baseS is not None and d["mat"]) else None),
            "edgeVsHist": (round(rate(d["mhit"], d["mat"]) - baseH, 1)
                           if (baseH is not None and d["mat"]) else None),
            "gapMed": round(g[len(g) // 2], 1) if g else None,
            "preciseRate": rate(d["prec"], d["pv"]),
        }

    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    print("=" * 78)
    print("R900 独立回测复算（纯标准库，未 import 项目模块）")
    print("=" * 78)
    print(f"数据截止 {res['asOf']} ｜ K线 {res['bars']} 根 ｜ 日志 {res['logged']} 条")
    print(f"可判方向 {res['dirJudgeable']} 条 ｜ 窗口已成熟 {res['dirMaturedN']} 条 ｜ 未到期未达成(删失) {res['dirPending']} 条")
    print()
    print("── 1. 方向推进率（复现 backtest.json）──")
    print(f"  全窗口口径     {res['dirAllWindow']}%")
    print(f"  仅成熟口径     {res['dirMatured']}%  95%CI {res['dirMaturedCI']}")
    print(f"  基线·全历史(成熟记录加权)  {res['baseHistMatW']}%   ← 项目 dirPairedBase 的口径")
    print(f"  基线·同期  (成熟记录加权)  {res['baseSameMatW']}%   ← 项目 dirPairedBaseSame 的口径（唯一可与成熟样本相减）")
    print(f"  （参考·全历史全记录加权    {res['baseHistRecW']}% / 同期 {res['baseSameRecW']}% —— 混入未成熟记录，不可相减）")
    print(f"  基线·全历史(键等权)    {res['baseHistKeyMean']}%  ← 对应 dirBaseRate")
    print(f"  基线·同期  (键等权)    {res['baseSameKeyMean']}%  ← 对应 dirBaseRateRecent")
    print(f"  优势·对全历史基线      {('%+.1f'%res['edgeVsHist'])+'pp' if res['edgeVsHist'] is not None else '—'}")
    print(f"  优势·对同期基线        {('%+.1f'%res['edgeVsSame'])+'pp' if res['edgeVsSame'] is not None else '—'}")
    print()
    print("── 2. 精确命中（成熟样本）──")
    print(f"  含平凡         {res['preciseMatured']}%  (n={res['preciseMaturedN']})")
    print(f"  去平凡         {res['preciseNonTrivial']}%  (n={res['preciseNonTrivialN']})")
    print(f"  平凡样本占比   {res['trivialShare']}%（目标价在预测当日已被自身收盘满足，零信息即成立）")
    print()
    print("── 3. 样本独立性（★项目未考察）──")
    print(f"  日志行数                 {res['logged']}")
    print(f"  实质不同目标(块)数       {res['blocks']}")
    print(f"  平均膨胀倍数             {res['blockRatio']}x")
    print(f"  块内同向命中/全miss/混合 {res['blockHit']}/{res['blockMiss']}/{res['blockMixed']}"
          f"（无成熟块 {res['blockNoMature']}）")
    print(f"  块不混合比例             {rate(res['blockHit']+res['blockMiss'], res['blockHit']+res['blockMiss']+res['blockMixed'])}%"
          " → 越高说明行与行高度相关，按行算 n 是虚的")
    print(f"  块级代表命中率           {res['blockRepRate']}%（n={res['blockRepN']}，95%CI {res['blockRepCI']}）")
    for tag, label in (("same", "同期基线"), ("hist", "全历史基线"), ("50", "50%")):
        k = "binom_p_vs_" + tag
        if k in res and res[k] is not None:
            verdict = "证据不足以判定（不显著）" if res[k] >= 0.05 else (
                "显著【劣于】对照" if tag != "50" and res[k] < 0.5 else "显著【优于】对照")
            print(f"  块级二项检验 vs {label:<10} P(X<=k) = {res[k]:<7} → {verdict}")
    print()
    print("── 4. 分 key 明细（成熟口径）──")
    print(f"  {'key':<12}{'可判':>5}{'成熟':>5}{'命中':>7}{'同期基':>7}{'对同期':>8}{'全历基':>7}{'目标距':>7}{'精确':>7}")
    for k, d in res["byKey"].items():
        print(f"  {k:<12}{d['n']:>5}{d['matured']:>5}"
              f"{(str(d['dirMaturedRate'])+'%') if d['dirMaturedRate'] is not None else '—':>7}"
              f"{(str(d['baseSame'])+'%') if d['baseSame'] is not None else '—':>7}"
              f"{(('%+.1f'%d['edgeVsSame'])+'pp') if d['edgeVsSame'] is not None else '—':>8}"
              f"{(str(d['baseHist'])+'%') if d['baseHist'] is not None else '—':>7}"
              f"{(str(d['gapMed'])+'%') if d['gapMed'] is not None else '—':>7}"
              f"{(str(d['preciseRate'])+'%') if d['preciseRate'] is not None else '—':>7}")


if __name__ == "__main__":
    main()
