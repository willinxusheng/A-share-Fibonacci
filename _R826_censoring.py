#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R826 诊断三：方向命中率的删失偏差（censoring bias）量化。

发现：backtest.py:507-512 明确把「观察窗未闭合(仅累计数日)」的样本计入方向命中率分母，
      且 dirCorrect 的定义是「窗内是否已推进 >=50%|Δ|」——未闭合时当然尚未推进 ⇒ 记 False。
      这与 R822 修的删失偏差方向相反：那里是"分母只含已解决"⇒高估；这里是"把未解决当失败"⇒低估。

量化：
  T1 matured × dirCorrect 交叉表
  T2 排除未成熟样本后的方向命中率（无偏口径）
  T3 按目标距离桶 × 成熟度 双层拆分（验证"早期可判定"假设只对近距离成立）
  T4 已达成者在第几天达成（若在早期即达成，说明 True 无需等成熟；False 才需要）
自校验：全量必须复现 18.9%。
"""
import csv
import json
from collections import defaultdict

HORIZON = 30
rows = list(csv.DictReader(open("data/sh000001.csv", encoding="utf-8")))
dates = [r["date"] for r in rows]
high = [float(r["high"]) for r in rows]
low = [float(r["low"]) for r in rows]
close = [float(r["close"]) for r in rows]
di = {d: i for i, d in enumerate(dates)}
recs = [json.loads(l) for l in open("data/predictions_log.jsonl", encoding="utf-8") if l.strip()]
prod = json.load(open("data/backtest.json", encoding="utf-8"))


def judge(rec):
    """返回 (verdict, matured, hz, i0)。verdict: True/False/None"""
    i0 = di.get(rec["date"])
    if i0 is None:
        return None, None, None, None
    px, c0 = float(rec["price"]), close[i0]
    hz = max(HORIZON, int(rec.get("expDays") or HORIZON))
    j1 = min(len(dates), i0 + 1 + hz)
    d = px - c0
    if j1 <= i0 + 1 or abs(d) <= 0.005 * c0:
        return None, None, hz, i0
    matured = (i0 + 1 + hz) <= len(dates)
    if d > 0:
        v = max(high[i0 + 1:j1]) >= c0 + 0.5 * d
    else:
        v = min(low[i0 + 1:j1]) <= c0 + 0.5 * d
    return bool(v), matured, hz, i0


# ---- 自校验（期望值从生产产物动态读取，避免"探针陈旧伪装成回归"）----
print("生产 dirRealizedHitRate =", prod["dirRealizedHitRate"], "| 生产 matured 口径 =",
      prod.get("dirRealizedHitRateMatured"), "| dirPendingCount =", prod.get("dirPendingCount"))
ok = n = 0
for r in recs:
    v, _, _, _ = judge(r)
    if v is None:
        continue
    n += 1
    ok += v
print("自校验：全量 %d/%d = %.1f%%" % (ok, n, ok / n * 100))
assert abs(ok / n * 100 - prod["dirRealizedHitRate"]) < 0.3, "自校验失败（全窗口口径）"

# 生产口径复算：对称成熟（只有窗已闭合才计入，达成与否一视同仁）
_mat_h = _mat_n = _pend = _early = 0
for r in recs:
    v, m, _, _ = judge(r)
    if v is None:
        continue
    if m:
        _mat_n += 1
        _mat_h += bool(v)
    elif v is True:
        _early += 1
    else:
        _pend += 1
_rep = round((_mat_h + 1.0) / (_mat_n + 2.0) * 100, 1)
print("自校验：对称成熟口径 %d/%d → Laplace %.1f%%（生产 %s）| pending %d（生产 %s）| early %d（生产 %s）"
      % (_mat_h, _mat_n, _rep, prod.get("dirRealizedHitRateMatured"), _pend, prod.get("dirPendingCount"),
         _early, prod.get("dirEarlyHitCount")))
assert abs(_rep - prod["dirRealizedHitRateMatured"]) < 0.3, "自校验失败（对称成熟口径）"
assert _pend == prod.get("dirPendingCount"), "自校验失败（pending 计数）"
assert _early == prod.get("dirEarlyHitCount"), "自校验失败（early 计数）"
print("  ✓ 两口径 + pending/early 全部复现\n")

print("=== T1 matured × dirCorrect 交叉表 ===")
tab = defaultdict(lambda: [0, 0])
for r in recs:
    v, m, _, _ = judge(r)
    if v is None:
        continue
    tab[m][0] += 1
    tab[m][1] += v
for m in (True, False):
    tot, hits = tab[m]
    print("  matured=%-5s 共 %3d | 命中 %3d | 命中率 %5.1f%%" % (m, tot, hits, hits / tot * 100 if tot else 0))

print("\n=== T2 无偏口径（仅 matured）vs 现行口径（含未成熟）===")
allv = [(r, *judge(r)) for r in recs]
allv = [x for x in allv if x[1] is not None]
cur = sum(1 for x in allv if x[1]) / len(allv) * 100
mat = [x for x in allv if x[2]]
unb = sum(1 for x in mat if x[1]) / len(mat) * 100 if mat else 0
print("  现行（含未成熟，n=%d）      = %5.1f%%" % (len(allv), cur))
print("  无偏（仅 matured，n=%d）    = %5.1f%%" % (len(mat), unb))
print("  差值                          = %+5.1f pp（现行口径低估幅度）" % (cur - unb))

print("\n=== T3 距离桶 × 成熟度 ===")
EDGES = [(0.005, 0.03), (0.03, 0.10), (0.10, 0.20), (0.20, 9)]
for a, b in EDGES:
    sub = [x for x in allv if a <= abs(float(x[0]["price"]) - close[x[3]]) / close[x[3]] < b]
    mm = [x for x in sub if x[2]]
    um = [x for x in sub if not x[2]]
    f = lambda s: "%5.1f%%(n=%d)" % (sum(1 for x in s if x[1]) / len(s) * 100, len(s)) if s else "  —  (n=0)"
    print("  [%4.0f%%,%4.0f%%)  含未成熟 %s | 仅成熟 %s | 未成熟占比 %2.0f%%"
          % (a * 100, b * 100, f(sub), f(mm), len(um) / len(sub) * 100 if sub else 0))

print("\n=== T4 已达成者的达成日分布（证明 True 不需要等成熟）===")
days = []
for r in recs:
    v, m, hz, i0 = judge(r)
    if not v:
        continue
    px, c0 = float(r["price"]), close[i0]
    d = px - c0
    thr = c0 + 0.5 * d
    j1 = min(len(dates), i0 + 1 + hz)
    seq = high[i0 + 1:j1] if d > 0 else low[i0 + 1:j1]
    k = 1
    for s in seq:
        if (s >= thr) if d > 0 else (s <= thr):
            break
        k += 1
    days.append(k)
if days:
    days.sort()
    print("  n=%d  中位达成日=%d  最早=%d  最晚=%d  | 落在前 5 日内的占比 %.0f%%"
          % (len(days), days[len(days) // 2], days[0], days[-1],
             sum(1 for x in days if x <= 5) / len(days) * 100))
