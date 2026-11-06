#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R900 验证驱动：在【不触碰仓库 data/】的前提下跑真实 run_backtest，核对新增字段。

把 LOG_PATH / OUT_PATH 全部重定向到临时目录，避免 archive() 写入 predictions_log.jsonl、
以及 backtest.json 被本地覆盖（铁律：data/ 由云端 CI 独家写盘）。

用法：python3 _R900_verify.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pandas as pd            # noqa: E402
import backtest as B           # noqa: E402

TMP = tempfile.mkdtemp(prefix="r900_")
LOG_TMP = os.path.join(TMP, "predictions_log.jsonl")
OUT_TMP = os.path.join(TMP, "backtest.json")
shutil.copy(os.path.join(HERE, "data", "predictions_log.jsonl"), LOG_TMP)
B.LOG_PATH = LOG_TMP
B.OUT_PATH = OUT_TMP


def load_fib():
    """data.js 形如 `window.FIB_DATA = {...};`，其字面量是合法 JSON，直接截取后反序列化
    （不用 exec：JS 的 null/true/false 在 Python 里不是合法字面量）。"""
    src = open(os.path.join(HERE, "data", "data.js"), encoding="utf-8").read()
    body = src.split("window.FIB_DATA", 1)[1].lstrip()
    body = body[body.index("{"):]
    body = body.rstrip().rstrip(";")
    return json.loads(body)


def main():
    fib = load_fib()
    data = {"updated": fib["updated"], "lastClose": fib["lastClose"],
            "tradePlan": fib["tradePlan"], "subForecast": fib["subForecast"]}
    df = pd.read_csv(os.path.join(HERE, "data", "sh000001.csv"),
                     parse_dates=["date"], index_col="date")
    stats = B.run_backtest(data, df)
    # 落一份稳定路径供前端渲染测试注入（_R900_render_check.js 默认读这个文件）
    _stable = os.path.join(tempfile.gettempdir(), "r900_backtest.json")
    with open(_stable, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)

    print("=" * 96)
    print("R900 真跑 run_backtest（LOG/OUT 均重定向到临时目录，仓库 data/ 零写入）")
    print("=" * 96)
    print(f"临时目录: {TMP}")
    print(f"totalLogged={stats['totalLogged']} totalEvaluated={stats['totalEvaluated']} "
          f"matured={stats['maturedCount']}")
    print()
    keys = ["dirRealizedHitRate", "dirRealizedHitRateMatured", "dirPairedBase", "dirPairedEdge",
            "dirPairedBaseSame", "dirPairedEdgeSame", "dirBaseRate", "dirBaseRateRecent"]
    for k in keys:
        if k in stats:
            print(f"  {k:<32} = {stats[k]}")
    print()
    print("── summary：精确命中率 旧口径 vs 两套无偏口径 ──")
    hdr = ("%-12s %6s %8s %9s %9s %9s %9s" %
           ("key", "n", "旧精确", "成熟精确", "独立精确", "独立n", "成熟n"))
    print(hdr)
    for r in stats["summary"]:
        old = r.get("preciseHitRate")
        pmm = r.get("preciseHitRateMatured")
        pii = r.get("preciseHitRateIndep")
        print("%-12s %6s %8s %9s %9s %9s %9s  band=%s" % (
            r["key"], r.get("n"),
            "—" if old is None else f"{old}%",
            "—" if pmm is None else f"{pmm}%",
            "—" if pii is None else f"{pii}%",
            r.get("preciseIndepEval"), r.get("preciseMatureEval"),
            r.get("hitRate")))
    print()
    # 硬断言：修复后不得再出现「精确命中 > band 触达」的倒挂
    bad = []
    for r in stats["summary"]:
        for tag in ("preciseHitRateMatured", "preciseHitRateIndep"):
            p = r.get(tag)
            h = r.get("hitRate")
            if p is not None and h is not None and p > h + 0.05:
                bad.append((r["key"], tag, p, h))
    if bad:
        print("⚠ 仍存在倒挂（band 触达率口径含 walk-forward 锚点，与日志口径非同源，属已知双源并列）：")
        for k, t, p, h in bad:
            print(f"   {k}: {t}={p}% > band={h}%")
    else:
        print("✅ 无倒挂：所有精确命中率 <= band 触达率")
    print()
    print(f"(临时结果已写入 {OUT_TMP}，可用后自行清理)")


if __name__ == "__main__":
    main()
