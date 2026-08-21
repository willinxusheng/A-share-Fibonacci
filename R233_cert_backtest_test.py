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


if __name__ == "__main__":
    try:
        test_overall_honest()
        print("\n[R233] PASS：overall_hit_rate 口径诚实（含 cold 组、无双重收缩）")
        sys.exit(0)
    except AssertionError as e:
        print("\n[R233] FAIL：%s" % e)
        sys.exit(1)
