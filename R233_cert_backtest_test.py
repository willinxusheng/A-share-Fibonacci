# -*- coding: utf-8 -*-
"""R233：回归测试——quality_cert 的 overall_hit_rate 必须诚实。

回归点（R90 修复前）：gen_quality_cert 用
    ev = [s for s in summary if s.hitRate is not None and s.n]
    overall = sum(s.hitRate/100 * s.n for s in ev) / sum(s.n for s in ev)
引入两个错误：
  (1) 漏计 cold 起步组（其 hitRate=None 被过滤）→ 分母从 totalEvaluated(52) 掉到 44；
  (2) 对已收缩 Laplace 估计再做 n 加权 → 双重收缩，产出既非原始、亦非正确 pooled-Laplace 的失真值(91.0)。
修复后：overall = 全部已评估分组(含 cold)的原始 pooled 命中率。

本测试断言：
  A) cert.overall_hit_rate == 原始 pooled（全部 n>0 分组的原始 hits 求和 / n 求和）；
  B) 计入分母的分组样本量 == backtest.totalEvaluated（cold 组不再被漏计）；
  C) cert.overall_hit_rate 不等于旧的失真值 91.0（防回退）。
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))


def _load():
    with open(os.path.join(BASE, "data", "quality_cert.json"), encoding="utf-8") as f:
        cert = json.load(f)
    with open(os.path.join(BASE, "data", "backtest.json"), encoding="utf-8") as f:
        bt = json.load(f)
    return cert, bt


def test_overall_honest():
    cert, bt = _load()
    summary = bt.get("summary", [])
    # 全部已评估分组（n>0，含 cold 起步组）
    allg = [s for s in summary if s.get("n")]
    n_sum = sum(s["n"] for s in allg)
    h_sum = sum(s["hits"] for s in allg)
    expect_raw = round(h_sum / n_sum * 100.0, 1) if n_sum else None

    overall = cert["backtest"]["overall_hit_rate"]
    total_eval = bt.get("totalEvaluated", 0)

    assert overall is not None, "overall_hit_rate 不应为 None（已脱离冷启动）"
    assert overall == expect_raw, (
        "overall_hit_rate(%s) != 原始 pooled(%s)，公式可能回退到双重收缩口径"
        % (overall, expect_raw))
    # 关键：计入分母的样本量必须等于 totalEvaluated（cold 组不再被漏计）
    assert n_sum == total_eval, (
        "计入 overall 分母的分组样本量(%d) != totalEvaluated(%d)，cold 组被漏计"
        % (n_sum, total_eval))
    # 防回退：旧失真值 91.0 不应再出现
    assert overall != 91.0, "overall_hit_rate 回退到旧的失真值 91.0（漏计+cold 双重收缩）"
    print("[R233-A] overall=%s == 原始pooled=%s, 分母分组样本=%d == totalEvaluated=%d ✓"
          % (overall, expect_raw, n_sum, total_eval))
    return True


def test_level_precision():
    """#782 反假绿守门：精确价位精度(早期可观测)字段必须真实派生、透传一致、逐条对齐。

    防止新字段静默为 None 或 cert 与 backtest 复制漂移造成假绿：
      A) bt.level_precision_median_dev / level_precision_within5_pct 均非 None（已真实派生）；
      B) 用回测原生 evaluate 复算全局带符号中位偏差，须与 bt 字段一致（±1e-3）；
      C) cert.backtest 透传的两字段须 == bt 字段（无复制漂移）；
      D) cert.backtest.targets 每条 precDevMedian 须与 bt.summary 按 (cat,key) 对齐（含 None 一致）。
    """
    import re
    import numpy as np
    import pandas as pd
    import importlib.util

    # 动态加载 backtest.py（不污染全局命名），复算以 trust-but-verify
    spec = importlib.util.spec_from_file_location("bt_mod", os.path.join(BASE, "backtest.py"))
    bt_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bt_mod)

    cert, bt = _load()
    # 复算：与 run_backtest 同源数据（evaluate 只读，不改写 bt/文件）
    with open(os.path.join(BASE, "data", "data.js"), encoding="utf-8") as f:
        d = json.loads(re.search(r"window\.FIB_DATA\s*=\s*(\{.*\})\s*;?\s*$",
                                 f.read(), re.S).group(1))
    _df = pd.read_csv(os.path.join(BASE, "data", "sh000001.csv"),
                      parse_dates=["date"]).set_index("date")
    recs = bt_mod.evaluate(_df)
    all_dev = [r["precDev"] for r in recs if r.get("precDev") is not None]
    assert all_dev, "evaluate 未产出任何 precDev（#782 字段失效）"
    expect_median = round(float(np.median(all_dev)), 4)
    expect_within5 = round(sum(1 for x in all_dev if x <= 0.05) / len(all_dev) * 100, 1)

    # 重载 bt（取最新一致视图）
    _, bt = _load()
    # 注意 backtest.json 键为 camelCase(levelPrecisionMedianDev/levelPrecisionWithin5Pct)，
    # 与 cert.backtest 下划线约定不同——此处读 backtest.json 须用 camelCase。
    lpmd = bt.get("levelPrecisionMedianDev")
    lpw5 = bt.get("levelPrecisionWithin5Pct")
    assert lpmd is not None, "bt.level_precision_median_dev 为 None（#782 字段未真实派生·假绿风险）"
    assert lpw5 is not None, "bt.level_precision_within5_pct 为 None（#782 字段未真实派生·假绿风险）"
    assert abs(lpmd - expect_median) < 1e-3, (
        "bt.level_precision_median_dev(%s) != 复算带符号中位(%s)" % (lpmd, expect_median))
    assert abs(lpw5 - expect_within5) < 1e-6, (
        "bt.level_precision_within5_pct(%s) != 复算±5%%达标率(%s)" % (lpw5, expect_within5))

    # C) cert 透传一致性
    cbt = cert["backtest"]
    assert cbt.get("level_precision_median_dev") == lpmd, "cert 透传中位偏差与 bt 不一致（复制漂移）"
    assert cbt.get("level_precision_within5_pct") == lpw5, "cert 透传±5%达标率与 bt 不一致（复制漂移）"

    # D) targets 逐条对齐 summary
    summary = bt.get("summary", [])
    smap = {(s["cat"], s["key"]): s.get("precDevMedian") for s in summary}
    for t in cbt.get("targets", []):
        exp = smap.get((t["cat"], t["key"]))
        assert t.get("precDevMedian") == exp, (
            "cert.targets[%s/%s].precDevMedian(%s) != summary(%s)"
            % (t["cat"], t["key"], t.get("precDevMedian"), exp))
    print("[R233-B] #782 精确价位精度：中位偏差=%s(复算%s) within5=%s(复算%s) · cert一致 · targets对齐%d条 ✓"
          % (lpmd, expect_median, lpw5, expect_within5, len(cbt.get("targets", []))))
    return True


if __name__ == "__main__":
    try:
        test_overall_honest()
        test_level_precision()
        print("\n[R233] PASS：overall_hit_rate 口径诚实 + #782 精确价位精度字段真实派生/透传一致")
        sys.exit(0)
    except AssertionError as e:
        print("\n[R233] FAIL：%s" % e)
        sys.exit(1)
