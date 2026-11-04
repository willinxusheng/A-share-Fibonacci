#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit57 — R828 观察窗口径 + 严格配对优势 守门员（第 14 道门禁）

背景（11-04 发现，推翻 R826 结论）：
  R826 把「引擎 40.6%（仅成熟）vs 基线 26.9%（全历史各键平均）」并排，并写下
  「引擎 ≈ 基线 ⇒ 无预测价值不成立」。但两者**分母不是同一批记录**：
    · 引擎：本次 185 条成熟记录
    · 基线：全历史各 (gap,window) 键的平均，每个键只投一票
  相减无意义。在同一批记录上逐条配对后：引擎 40.5% vs 配对基线 49.6% = **−9.1pp**，
  方向相反 —— 引擎相对「同目标随机起点」没有优势，卖出侧尤其差（0/37 vs 24.8%）。

同时发现第二个口径缺陷：
  旧式 _hz = max(HORIZON, expDays) 会给承诺 4~29 天的预测统一 30 天（最多放宽 7.5 倍，
  涉及 188/550=34% 的记录），等于替引擎多要时间 ⇒ 命中率虚高（44.9% vs 40.5%）。

本门禁固化（7 项，全部独立复算，不 import 生产模块）：
  P1  dirPairedN == 独立复算：成熟且有基线 的记录数
  P2  dirPairedBase == 独立复算（自实现 deque 滑窗，容差 0.2pp）
  P3  dirPairedEdge == dirMaturedRateRaw − dirPairedBase（自洽）
  P4  bySide 恒等式：Σhits == dirMaturedHits、Σn == dirPairedN、Σ(base×n)/Σn == dirPairedBase
  P5  每个 side 的 edge == rate − base（自洽）
  P6  dirWindowRule == "faithful:expDays"，且逐条 dirWindow == expDays（防口径回退）
  P7  前端静态：必须展示配对基线与优势，且**不得**再出现已被推翻的「那是巧合，不是结论」表述
"""
import json
import math
import os
import sys
from collections import deque

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(BASE, "data", "data.js")
# 路径可用环境变量覆盖：缺陷注入正控需要在不改生产文件的前提下跑断言
# （A57_BT = backtest.json，A57_HTML = 前端文件；data.js 走 argv）
BTJSON = os.environ.get("A57_BT") or os.path.join(BASE, "data", "backtest.json")
HTML_PATH = os.environ.get("A57_HTML") or os.path.join(BASE, "index.html")
LOG = os.path.join(BASE, "data", "predictions_log.jsonl")
CSV = os.path.join(BASE, "data", "sh000001.csv")
HORIZON = 30

FAILS = []


def chk(cond, name, detail=""):
    print("  [%s] %-46s %s" % ("PASS" if cond else "FAIL", name, detail))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def load_data(paths):
    p = paths[0] if paths else DATA_JS
    s = open(p, encoding="utf-8").read()
    return json.loads(s[s.index("{"): s.rindex("}") + 1])


def fwd_ext(arr, w, is_max):
    """out[i] = max/min(arr[i+1 : i+1+w])；i+w<=n-1 之外为 None。单调队列 O(n)。"""
    n = len(arr)
    out = [None] * n
    dq = deque()
    for j in range(n):
        while dq and ((arr[dq[-1]] <= arr[j]) if is_max else (arr[dq[-1]] >= arr[j])):
            dq.pop()
        dq.append(j)
        while dq[0] <= j - w:
            dq.popleft()
        i = j - w
        if 0 <= i <= n - w - 1:
            out[i] = arr[dq[0]]
    return out


def main(paths=None):
    print("=" * 78)
    print("audit57 — R828 观察窗口径 + 严格配对优势")
    print("=" * 78)

    D = load_data(paths)
    bt = json.load(open(BTJSON, encoding="utf-8"))
    recs = [json.loads(l) for l in open(LOG, encoding="utf-8") if l.strip()]

    rows = [r for r in open(CSV, encoding="utf-8").read().splitlines() if r.strip()]
    hdr = rows[0].split(",")
    ix_d, ix_h, ix_l, ix_c = (hdr.index(x) for x in ("date", "high", "low", "close"))
    dates, high, low, close = [], [], [], []
    for ln in rows[1:]:
        p = ln.split(",")
        dates.append(p[ix_d])
        high.append(float(p[ix_h]))
        low.append(float(p[ix_l]))
        close.append(float(p[ix_c]))
    n = len(dates)
    di = {d: i for i, d in enumerate(dates)}

    # ---------- P6a 窗口口径字段 ----------
    chk(D.get("backtest", {}).get("dirWindowRule") == "faithful:expDays",
        "P6a dirWindowRule 声明", "值=%s" % D.get("backtest", {}).get("dirWindowRule"))

    # ---------- 独立复算：忠实窗口下逐条判定 + 配对基线 ----------
    cache = {}

    def ext(w):
        if w not in cache:
            cache[w] = (fwd_ext(high, w, True), fwd_ext(low, w, False))
        return cache[w]

    bad_win, matured, pairs = [], [], []
    for r in recs:
        i0 = di.get(r.get("date"))
        if i0 is None:
            continue
        e = r.get("expDays")
        w = max(1, int(e) if e else HORIZON)
        c0 = close[i0]
        px = float(r["price"])
        d = px - c0
        if abs(d) <= 0.005 * c0 or i0 + 1 >= n:
            continue
        if r.get("dirWindow") is not None and int(r["dirWindow"]) != w:
            bad_win.append("%s %s: dirWindow=%s != expDays=%d"
                           % (r.get("date"), r.get("key"), r.get("dirWindow"), w))
        mx, mn = ext(w)
        full = mx[i0] is not None
        if full:
            v = (mx[i0] >= c0 + 0.5 * d) if d > 0 else (mn[i0] <= c0 + 0.5 * d)
            is_mat = (i0 + w) <= (n - 1)
            # 该目标自身的零信息基线：相对每个起点自己的收盘
            g = d / c0
            ok = 0
            tot = 0
            for i in range(0, n - w):
                lvl = close[i] * (1.0 + 0.5 * g)
                hit = (mx[i] >= lvl) if d > 0 else (mn[i] <= lvl)
                tot += 1
                ok += bool(hit)
            base = (ok / tot) if tot else None
        else:
            j1 = min(n, i0 + 1 + w)
            seg_h, seg_l = high[i0 + 1:j1], low[i0 + 1:j1]
            if not seg_h:
                continue
            v = (max(seg_h) >= c0 + 0.5 * d) if d > 0 else (min(seg_l) <= c0 + 0.5 * d)
            is_mat = False
            base = None
        if is_mat:
            matured.append({"v": bool(v), "base": base, "side": r.get("side") or "?"})
            if base is not None:
                pairs.append({"v": bool(v), "base": base, "side": r.get("side") or "?"})

    chk(not bad_win, "P6b 逐条 dirWindow == expDays", "异常=%d %s" % (len(bad_win), bad_win[:2] or ""))

    # ---------- P1 配对样本数 ----------
    chk(len(pairs) == bt.get("dirPairedN"),
        "P1 dirPairedN == 成熟且有基线者",
        "复算=%d 生产=%s（成熟总数 %d）" % (len(pairs), bt.get("dirPairedN"), len(matured)))

    # ---------- P2 配对基线 ----------
    # 容差 0.2pp 的依据（实测，非拍脑袋）：把窗口语义错一位（[i+1,i+w] → [i,i+w-1]）
    # 得 50.61 vs 生产 49.6（差 1.07pp，远大于容差）⇒ 容差挡得住边界错位，不会假绿。
    # 当前残差 0.06pp 来自生产 rolling 实现与本地 deque 实现的浮点/边界取整差异。
    pb = round(sum(x["base"] for x in pairs) / len(pairs) * 100, 1) if pairs else None
    chk(pb is not None and bt.get("dirPairedBase") is not None
        and abs(pb - bt["dirPairedBase"]) <= 0.2,
        "P2 dirPairedBase 独立复算一致", "复算=%s 生产=%s" % (pb, bt.get("dirPairedBase")))

    # ---------- P3 edge 自洽 ----------
    mr = round(sum(1 for x in pairs if x["v"]) / len(pairs) * 100, 1) if pairs else None
    chk(mr is not None and bt.get("dirMaturedRateRaw") is not None
        and abs(mr - bt["dirMaturedRateRaw"]) <= 0.2,
        "P3a dirMaturedRateRaw 独立复算一致", "复算=%s 生产=%s" % (mr, bt.get("dirMaturedRateRaw")))
    want_edge = round(bt["dirMaturedRateRaw"] - bt["dirPairedBase"], 1) \
        if (bt.get("dirMaturedRateRaw") is not None and bt.get("dirPairedBase") is not None) else None
    chk(want_edge is not None and bt.get("dirPairedEdge") is not None
        and abs(want_edge - bt["dirPairedEdge"]) <= 0.11,
        "P3b dirPairedEdge == maturedRaw − pairedBase",
        "应有=%s 生产=%s" % (want_edge, bt.get("dirPairedEdge")))

    # ---------- P4 bySide 恒等式 ----------
    side = bt.get("dirPairedBySide") or []
    s_hits = sum(x["hits"] for x in side)
    s_n = sum(x["n"] for x in side)
    s_base = (sum(x["base"] * x["n"] for x in side) / s_n) if s_n else None
    chk(s_hits == bt.get("dirMaturedHits") and s_n == bt.get("dirPairedN"),
        "P4a bySide 计数还原 matHits / pairedN",
        "Σhits=%d(matHits=%s) Σn=%d(pairedN=%s)"
        % (s_hits, bt.get("dirMaturedHits"), s_n, bt.get("dirPairedN")))
    chk(s_base is not None and bt.get("dirPairedBase") is not None
        and abs(s_base - bt["dirPairedBase"]) <= 0.2,
        "P4b bySide 加权基线还原 pairedBase", "加权=%s 生产=%s"
        % (round(s_base, 1) if s_base is not None else None, bt.get("dirPairedBase")))

    # ---------- P5 每个 side 的 edge 自洽 ----------
    bad_side = []
    for x in side:
        if x["n"]:
            want = round(x["hits"] / x["n"] * 100, 1)
            if abs(want - x["rate"]) > 0.11:
                bad_side.append("%s: rate=%s 复算=%.1f" % (x["side"], x["rate"], want))
            wb = round(sum(p["base"] for p in pairs if p["side"] == x["side"]) / x["n"] * 100, 1) \
                if x["n"] else None
            if wb is not None and abs(wb - x["base"]) > 0.2:
                bad_side.append("%s: base=%s 复算=%.1f" % (x["side"], x["base"], wb))
            if abs((x["rate"] - x["base"]) - x["edge"]) > 0.11:
                bad_side.append("%s: edge 不自洽" % x["side"])
    chk(not bad_side, "P5 bySide 的 rate/base/edge 自洽", "异常=%s" % (bad_side or "无"))

    # ---------- P7 前端静态 ----------
    html = open(HTML_PATH, encoding="utf-8").read()
    # 静态锚点必须是【披露块本身的特征串】，不能只查字段名：
    # 字段名在页面其它位置（页首摘要行）也出现，只查它则"删掉整段披露"也照样通过。
    need = ["严格配对检验", "dirPairedEdge", "dirPairedBase", "dirPairedBySide"]
    miss = [k for k in need if k not in html]
    chk(not miss, "P7a 前端保留配对披露块", "缺失=%s" % (miss or "无"))
    banned = ["那是巧合，不是结论", "不是判据的问题"]
    hit_ban = [k for k in banned if k in html]
    chk(not hit_ban, "P7b 前端无已被推翻的表述", "命中=%s" % (hit_ban or "无"))

    print("-" * 78)
    if FAILS:
        print("audit57: FAIL=%d → %s" % (len(FAILS), FAILS))
        return 1
    print("audit57: 全部 7 项通过（配对优势 %s pp｜配对基线 %s%%）"
          % (bt.get("dirPairedEdge"), bt.get("dirPairedBase")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or None))
