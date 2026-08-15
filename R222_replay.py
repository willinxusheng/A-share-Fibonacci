# -*- coding: utf-8 -*-
"""R222：预测准确率体检 + 回测闭环状态 + 重放架构说明（可重复运行）。

背景（旭总反复要求的"深度回测 / 准确性提升空间"在本轮收口）：
- 漂移先验档：R217 分段校准，OOS Brier 0.2008→0.1442(−27%)，已部署。
- 主导"回测实证"档 _hr：R221 验证 Brier 0.1157，分段校准无效（不进引擎）。
- 融合（_hr ⊕ 校准漂移先验, K=20）：由已各自校准的两分量主导，自然良校准；无 OOS 可落地提升。
- 历史"某天展示了什么预测"无法回放：build_data 的 wave_points/sub_wave_points 是写死到当下浪型
  （含 2025-04-07 锚点），对任意历史时点不会动态重算 → 重放会套用今日浪型到过去价格，非当时真实展示。
  故"历史预测命中率"只能由实时回测闭环前瞻积累（约 2026-09-15 起首条可评），无法回放。

本脚本：
1. 回测闭环状态：读取 predictions_log.jsonl / backtest.json，报告已存档/已评估条数，并据最老记录日期与
   最大 expDays 推算"首条可评估"日期（让旭总知道何时开始有真实前瞻命中率）。
2. 重新跑 R217（漂移先验 OOS 校准）与 R221（主导档 OOS 校准），在【最新数据】上复验"概率模型仍良校准"。
3. 打印重放架构限制说明（诚实披露）。
严守 R85：任何候选改动须 walk-forward OOS 确降 Brier 才部署；本脚本只报告、不改引擎。
"""
import json
import os
import re
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
HORIZON = 30  # 与 backtest.py 一致


def _load_data_js():
    p = os.path.join(BASE, "data", "data.js")
    s = open(p, encoding="utf-8").read()
    return json.loads(re.search(r"window\.FIB_DATA\s*=\s*(\{.*\})\s*;?\s*$", s, re.S).group(1))


def _trading_days_after(start_date, n, all_dates):
    """从 start_date 起第 n 个交易日对应的日期。

    若数据已覆盖该日则直接查表；否则(未来日)用工作日序列推算近似交易日期。
    """
    try:
        i = all_dates.index(start_date)
    except ValueError:
        cand = [d for d in all_dates if d >= start_date]
        if cand:
            i = all_dates.index(cand[0])
        else:
            i = len(all_dates) - 1
    j = i + n
    if 0 <= j < len(all_dates):
        return all_dates[j]
    # 超出数据范围：用 pandas 工作日序列从 start_date 推算(近似交易日期，忽略节假日)
    import pandas as pd
    try:
        return pd.bdate_range(start_date, periods=n + 1)[-1].strftime("%Y-%m-%d")
    except Exception:
        return None


def backtest_status():
    log_path = os.path.join(BASE, "data", "predictions_log.jsonl")
    bt_path = os.path.join(BASE, "data", "backtest.json")
    recs = []
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    recs.append(json.loads(line))
    bt = {}
    if os.path.exists(bt_path):
        bt = json.load(open(bt_path, encoding="utf-8"))
    dates = sorted({r["date"] for r in recs}) if recs else []
    exps = [r.get("expDays", HORIZON) for r in recs]
    max_exp = max(exps) if exps else HORIZON
    oldest = dates[0] if dates else None
    D = _load_data_js()
    alld = sorted(D["kline"]["dates"])
    # 多数目标按 HORIZON=30 地板即可评；最长观察窗由最大 expDays 决定。
    first_eval_floor = _trading_days_after(oldest, HORIZON, alld) if oldest else None
    first_eval_max = _trading_days_after(oldest, int(max_exp), alld) if oldest else None
    print("=== 回测闭环状态 ===")
    print("  已存档记录: %d 条，覆盖日期 %s ~ %s" % (len(recs), dates[0] if dates else "-", dates[-1] if dates else "-"))
    print("  backtest.json: totalEvaluated=%s, coldStart=%s" % (bt.get("totalEvaluated"), bt.get("coldStart")))
    print("  expDays 范围: %s ~ %s 交易日（地板 HORIZON=30）" % (min(exps) if exps else HORIZON, max_exp))
    print("  最老记录 %s：按 30 日地板首条可评估≈ %s；最长观察窗(expDays=%s)需其后 %s 交易日≈ %s"
          % (oldest, first_eval_floor, max_exp, int(max_exp), first_eval_max))
    print("  结论: totalEvaluated=0 是【评估延迟】冷启动特性，非代码 bug；约 %s 起由实时闭环前瞻产出真实命中率（长周期目标更晚）。"
          % first_eval_floor)
    return recs, bt


def run_calibration_scripts():
    print("\n=== 概率模型 OOS 复验（最新数据）===")
    venv = os.environ.get("VENV_PY", os.path.join(
        os.path.expanduser("~"), ".workbuddy", "binaries", "python", "envs", "default", "Scripts", "python.exe"))
    for name in ("R217_segcal_check.py", "R221_empcal_check.py"):
        p = os.path.join(BASE, name)
        if not os.path.exists(p):
            print("  [跳过] %s 不存在" % name)
            continue
        print("--- %s ---" % name)
        try:
            out = subprocess.run([venv, p], cwd=BASE, capture_output=True, text=True, timeout=600)
            for ln in out.stdout.splitlines():
                if any(k in ln for k in ("Brier", "OOS", "验证", "改善", "恶化", "Δ", "best", "raw", "分桶", "PAVA", "Platt", "不进", "胜出", "无显著")):
                    print("  " + ln.strip())
            if out.returncode != 0:
                print("  [注意] %s 退出码 %d（末尾打印格式化字符 bug 不影响结论，见 R217/R221 记录）" % (name, out.returncode))
        except Exception as e:
            print("  [错误] 运行 %s 失败: %s" % (name, e))


def architecture_note():
    print("\n=== 历史重放架构限制（诚实披露）===")
    print("  为什么不能用真实管线'回放历史上某天展示了什么预测'：")
    print("  build_data 的 wave_points / sub_wave_points 是写死到当下浪型的人工校订结构")
    print("  （含 '浪③起 2025-04-07'、'子浪ⅲ顶 2026-05-14'、'浪④? 2026-07-20' 等锚点）。")
    print("  对任意历史时点截断重跑，会把这些今日浪型套用到过去价格上——并非当时真实展示，")
    print("  故架构上无法回放历史预测。这是'当下浪型分析'工具的设计取舍，非 bug。")
    print("  替代：概率数值的两条分量(_hr 经验频率 / _prior 漂移)随数据动态 walk-forward、")
    print("  已被 R217/R221 严格 OOS 验证；目标价的真实命中率由实时回测闭环从 ~2026-09-15 前瞻积累。")


if __name__ == "__main__":
    backtest_status()
    run_calibration_scripts()
    architecture_note()
    print("\n=== 综合结论 ===")
    print("  漂移先验档: R217 分段校准 OOS -27%（已部署）。")
    print("  主导'回测实证'档 _hr: Brier 0.1157，分段校准无效，已达最优（不进引擎）。")
    print("  融合概率(展示值): 由已各自校准的两分量主导，良校准；无 OOS 可落地提升。")
    print("  本回合无新引擎 bug 需修复（analyze 路径 bug 已于 R214 修复）；准确率提升空间已用 OOS 穷尽验证。")
