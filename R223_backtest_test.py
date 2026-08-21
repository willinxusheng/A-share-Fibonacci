# -*- coding: utf-8 -*-
"""R223：实证验证「回测闭环在观察窗闭合后能正确产出命中率」，而非永远 totalEvaluated=0。

前几轮(R214~R222)把 totalEvaluated=0 定性为冷启动(观察窗未闭合)，本脚本用两种手段给出硬证据：
  A) 机制测试：真实 predictions_log.jsonl(副本) + 真实 K 线向后合成延伸(覆盖30日地板窗口)，
     在临时文件上跑 evaluate()，断言最老记录由 unevaluated→evaluated（证明闭环接线有效、会随数据推进自动评估）。
  B) 语义测试：完全合成数据 + 单条受控记录，断言 命中/未命中 判定与 days_to_hit 计算正确
     （证明命中定义前两行/后两行的 band-edge 逻辑无 off-by-one、idxmax 守卫正确）。
严守 R85：只读复刻、临时文件、不碰任何生产文件、不部署。
"""
import json
import os
import shutil
import tempfile

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
import backtest as bt


def _load_real_df():
    df = pd.read_csv(os.path.join(BASE, "data", "sh000001.csv"),
                      parse_dates=["date"]).set_index("date").sort_index()
    return df


def test_A_mechanism():
    """真实日志副本 + 真实数据向后延伸 → 断言评估条数由 0 变正。"""
    tmp = tempfile.mkdtemp()
    log_copy = os.path.join(tmp, "predictions_log.jsonl")
    shutil.copyfile(os.path.join(BASE, "data", "predictions_log.jsonl"), log_copy)
    bt.LOG_PATH = log_copy
    bt.OUT_PATH = os.path.join(tmp, "backtest.json")

    df = _load_real_df()
    last_date = df.index[-1]
    last_close = float(df["close"].iloc[-1])
    # 向后延伸 40 个交易日（覆盖 30 日地板观察窗），轻微上行路径制造触及机会。
    fut = pd.bdate_range(last_date + pd.Timedelta(days=1), periods=40)
    prev = last_close
    rows = []
    for d in fut:
        prev = prev * 1.001
        rows.append({"open": prev, "high": prev * 1.004,
                     "low": prev * 0.996, "close": prev,
                     "volume": 1e9, "amount": 1e10, "exchange": 1.0})
    ext = pd.DataFrame(rows, index=fut)
    df2 = pd.concat([df, ext]).sort_index()

    recs = bt.evaluate(df2)
    total_eval = sum(1 for r in recs if r.get("evaluated"))
    oldest = min(r["date"] for r in recs)
    oldest_eval = any(r.get("evaluated") for r in recs if r["date"] == oldest)

    print("\n[A] 机制测试")
    print("  数据末日: %s → 延伸后 %s" % (last_date.date(), df2.index[-1].date()))
    print("  记录总数: %d | 已评估: %d" % (len(recs), total_eval))
    print("  最老记录 %s 是否已开始评估: %s" % (oldest, oldest_eval))
    assert total_eval > 0, "FAIL: 窗口闭合后仍 totalEvaluated=0，闭环或接线有 bug"
    assert oldest_eval, "FAIL: 最老记录观察窗闭合后仍 unevaluated"
    print("  PASS: 闭环有效——窗口闭合后自动评估，非永久 0")


def test_B_semantics():
    """完全合成数据 + 受控单记录，断言命中/未命中与 days_to_hit 正确。"""
    tmp = tempfile.mkdtemp()
    bt.LOG_PATH = os.path.join(tmp, "predictions_log.jsonl")
    bt.OUT_PATH = os.path.join(tmp, "backtest.json")
    bt.HORIZON = 5  # 缩小地板，使合成短期窗口可闭合（仅本测试）

    # 11 个交易日，价格全平(close=1000) → 滚动 vol=0 → band _frac=0 → 判定边界=精确价。
    days = pd.bdate_range("2026-01-02", periods=11)
    close = 1000.0
    rows = []
    for i, d in enumerate(days):
        # 第3个未来日(i=3, 即 D3)把 high 抬到 1010，其余 high=1000。
        hi = 1010.0 if i == 3 else 1000.0
        lo = 1000.0
        rows.append({"open": close, "high": hi, "low": lo, "close": close,
                     "volume": 1e9, "amount": 1e10, "exchange": 1.0})
    df = pd.DataFrame(rows, index=days)

    # 记录1：sell 目标 1005（处于 D3 的 high=1010 之上）→ 应命中，days_to_hit=3。
    # 记录2：sell 目标 1100（所有 high<=1010）→ 应未命中，evaluated=True。
    recs_in = [
        {"date": "2026-01-02", "key": "hitT", "cat": "sellTarget",
         "side": "sell", "price": 1005.0, "expDays": 5.0},
        {"date": "2026-01-02", "key": "missT", "cat": "sellTarget",
         "side": "sell", "price": 1100.0, "expDays": 5.0},
    ]
    with open(bt.LOG_PATH, "w", encoding="utf-8") as f:
        for r in recs_in:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    out = {r["key"]: r for r in bt.evaluate(df)}
    h, m = out["hitT"], out["missT"]

    print("\n[B] 语义测试（band-edge 命中定义 + days_to_hit）")
    print("  hitT : evaluated=%s hit=%s days_to_hit=%s" %
          (h["evaluated"], h["hit"], h.get("days_to_hit")))
    print("  missT: evaluated=%s hit=%s best=%s" %
          (m["evaluated"], m["hit"], m.get("best")))
    assert h["evaluated"] and h["hit"], "FAIL: 应命中却未命中"
    assert h["days_to_hit"] == 3, "FAIL: days_to_hit 应为 3，实际 %s" % h.get("days_to_hit")
    assert m["evaluated"] and not m["hit"], "FAIL: 应判定未命中却出错"
    assert abs(m["best"] - 1010.0) < 1e-6, "FAIL: 最近接近价应为 1010"
    print("  PASS: 命中定义、days_to_hit 计算、未命中 best 记录均正确")


def test_C_earlyhit():
    """回归（R91 设计漏洞修复）：观察窗未闭合(i0+_hz>末日)但目标已被触及 → 须判命中，
    不得整条标 unevaluated 而白等 ~1 年；反之未触及且窗口未闭合 → 保持 unevaluated(不误判 miss)。
    """
    tmp = tempfile.mkdtemp()
    bt.LOG_PATH = os.path.join(tmp, "predictions_log.jsonl")
    bt.OUT_PATH = os.path.join(tmp, "backtest.json")
    df = _load_real_df()
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    dt = "2026-08-04"          # 近期真实日期
    i0 = dates.index(dt)
    fwd = df.iloc[i0 + 1: len(df.index)]
    hi_max = float(fwd["high"].max())

    # 命中侧：目标价远低于前视最高 → 必被触及；expDays=280 使窗口远未闭合。
    rec = {"date": dt, "key": "earlyHitT", "cat": "sellTarget",
           "side": "sell", "price": hi_max * 0.5, "expDays": 280.0}
    with open(bt.LOG_PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    r = {x["key"]: x for x in bt.evaluate(df)}["earlyHitT"]
    print("\n[C] 提前命中回归")
    print("  dt=%s expDays=280 → i0+_hz=%d > 末日 %d（窗口未闭合）；目标价=%.0f 前视最高=%.0f"
          % (dt, i0 + 280, len(dates) - 1, hi_max * 0.5, hi_max))
    print("  earlyHitT: evaluated=%s hit=%s" % (r["evaluated"], r["hit"]))
    assert r["evaluated"] and r["hit"], "FAIL: 窗口未闭合但已触及却未判命中（回归 R91 漏洞）"

    # 反向：目标价远高于前视最高 → 未命中且窗口未闭合 → 保持 unevaluated（不误判 miss）。
    rec2 = {"date": dt, "key": "pendingT", "cat": "sellTarget",
            "side": "sell", "price": hi_max * 5.0, "expDays": 280.0}
    with open(bt.LOG_PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec2, ensure_ascii=False) + "\n")
    r2 = {x["key"]: x for x in bt.evaluate(df)}["pendingT"]
    print("  pendingT : evaluated=%s hit=%s" % (r2["evaluated"], r2["hit"]))
    assert (not r2["evaluated"]) and (not r2["hit"]), \
        "FAIL: 窗口未闭合未命中应 unevaluated，不应误判 miss"
    print("  PASS: 提前命中生效；未触及且窗口未闭合正确保持 unevaluated（不误判 miss）")


if __name__ == "__main__":
    test_A_mechanism()
    test_B_semantics()
    test_C_earlyhit()
    print("\n=== R223 结论：回测闭环逻辑健全；R91 提前命中修复后，窗口未闭合但已触及的目标当天即计入命中"
          "（不再恒 totalEvaluated=0）；未触及且窗口未闭合保持 unevaluated；无新 bug ===")
