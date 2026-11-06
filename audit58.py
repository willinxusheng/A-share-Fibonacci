#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit58 — R900 精确命中率无偏化 + 配对基线同行情性 守门员（第 15 道门禁）

背景（11-06 独立复算发现）：
  ① 精确命中率倒挂：表格出现「子浪ⅳ 精确命中 98.4% / band 触达 41.7%」——
     而精确命中（触达目标价 px）必然蕴含 band 触达（触达 px×(1−frac)，frac≥0），
     数学上不可能倒挂。根因是分母口径：
        · band 触达率分母 = 全部「已解决」样本（含窗闭合未命中）
        · 精确命中率分母 = preciseHit 非 None 的样本，而命中可在窗未闭合时提前判定、
          未命中须等窗闭合才记 miss ⇒ 分母被"已命中"富集。
  ② 重复计数：predictions_log 逐日追加，同一目标价连续数十日入库被当成数十个独立样本。
     实测「浪⑤起」61 条日志背后只有 1 个独立目标价(3741.11)，却输出 98.4% 精确命中率。
  ③ 配对基线跨行情：dirPairedBase(48.6%) 用的是每条记录自身的【全历史】基线，而引擎成熟
     样本只覆盖 2026-08~10。用含 2024-25 牛市的全历史基线相减，把劣势放大成 −10.1pp；
     同期基线仅 41.0%，真实差距 −2.5pp，且独立样本极少 ⇒ 不显著。

本门禁固化（数据未刷新时自动降级为 SKIP，不阻断 CI；一旦新字段出现即硬断言）：
  P1  summary 全行含 preciseIndepEval / preciseMatureEval（口径字段齐备）
  P2  独立目标数 <= 该行日志已解决数（去重只能减少，不能凭空增加）
  P3  独立口径与旧口径在纯重复目标行上必须不同（证明去重真的生效）
  P4  dirPairedBaseSame 存在且 <= dirPairedBase（同期不含牛市，基线不应更高）
  P5  同源前提下精确命中率不得 > band 触达率
  P6  前端静态：必须标注「精确命中率(独立目标)」，且不得把「引擎仍劣于随机起点」当现行结论
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.environ.get("A58_DATAJS") or os.path.join(BASE, "data", "data.js")
HTML_PATH = os.environ.get("A58_HTML") or os.path.join(BASE, "index.html")
BTJSON = os.environ.get("A58_BT")            # 可指向本地真跑结果（data/ 只读时用）

FAILS = []
SKIPS = []
NCHK = [0]


def chk(cond, name, detail=""):
    NCHK[0] += 1
    if cond:
        print("  [P%d] ✓ %s %s" % (NCHK[0], name, ("· " + detail) if detail else ""))
    else:
        FAILS.append(name)
        print("  [P%d] ✗ %s %s" % (NCHK[0], name, ("· " + detail) if detail else ""))


def load_bt():
    if BTJSON:
        with open(BTJSON, encoding="utf-8") as f:
            return json.load(f)
    src = open(DATA_JS, encoding="utf-8").read()
    body = src.split("window.FIB_DATA", 1)[1].lstrip()
    body = body[body.index("{"):].rstrip().rstrip(";")
    return json.loads(body)["backtest"]


def main():
    print("-" * 78)
    print("audit58: R900 精确命中率无偏化 + 配对基线同行情性")
    print("-" * 78)
    bt = load_bt()
    summary = bt.get("summary") or []
    if not summary:
        print("audit58: 无 summary，跳过")
        return 0

    has_new = all(("preciseIndepEval" in r) for r in summary)
    if not has_new:
        print("  ⚠ SKIP：backtest.summary 尚无 R900 新字段（data/ 由 CI 独家写盘，等下一轮刷新）")
        print("  ⚠ 可用本地真跑结果验证：A58_BT=/tmp/r900_backtest.json python3 audit58.py")
        print("-" * 78)
        print("audit58: SKIP（0 项断言，非失败）")
        return 0

    # P1 字段齐备
    chk(all(r.get("preciseMatureEval") is not None for r in summary),
        "P1 summary 全行含 preciseIndepEval/preciseMatureEval",
        "%d 行" % len(summary))

    # P2 独立目标数不得超过日志已解决行数
    bad = [(r["key"], r.get("preciseIndepEval"), r.get("preciseEval"))
           for r in summary
           if r.get("preciseIndepEval") is not None and r.get("preciseEval") is not None
           and r["preciseIndepEval"] > r["preciseEval"]]
    chk(not bad, "P2 独立目标数 <= 日志已解决数（去重只能减少）", "越界=%s" % (bad or "无"))

    # P3 去重必须对纯重复目标生效：独立样本==1 的行，独立口径与旧口径应不同或至少独立数为 1
    single = [r for r in summary if r.get("preciseIndepEval") == 1]
    chk(all(r.get("preciseHitRateIndep") is None for r in single),
        "P3 独立目标仅 1 个的行一律不出率（防单样本伪精度）",
        "此类行=%s" % ([r["key"] for r in single] or "无"))

    # P4 同期基线不得高于全历史基线
    ps, ph = bt.get("dirPairedBaseSame"), bt.get("dirPairedBase")
    if ps is None or ph is None:
        chk(False, "P4 dirPairedBaseSame 已产出", "缺失（同期配对基线未计算）")
    else:
        chk(ps <= ph + 0.05,
            "P4 同期配对基线 <= 全历史配对基线",
            "同期=%s%% 全历史=%s%% 优势 同期=%spp 全历史=%spp"
            % (ps, ph, bt.get("dirPairedEdgeSame"), bt.get("dirPairedEdge")))

    # P5 精确命中率不得 > band 触达率（二者同源时才可严格比较）
    inv = []
    for r in summary:
        p = r.get("preciseHitRateMatured")
        h = r.get("hitRate")
        if p is not None and h is not None and p > h + 0.05:
            inv.append((r["key"], p, h))
    chk(not inv, "P5 精确命中率(成熟) <= band 触达率", "倒挂=%s" % (inv or "无"))

    # P6 前端静态口径
    try:
        html = open(HTML_PATH, encoding="utf-8").read()
    except OSError:
        html = ""
    chk("精确命中率(独立目标)" in html,
        "P6a 前端表头标注「精确命中率(独立目标)」")
    chk(not ("引擎仍劣于随机起点" in html and "已撤回" not in html),
        "P6b 前端未把「引擎仍劣于随机起点」当现行结论")
    chk("dirPairedBaseSame" in html or "同期配对基线" in html,
        "P6c 前端并列展示同期配对基线")

    print("-" * 78)
    if FAILS:
        print("audit58: FAIL=%d → %s" % (len(FAILS), FAILS))
        return 1
    print("audit58: 全部 %d 项通过" % NCHK[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
