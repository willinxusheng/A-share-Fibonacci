#!/usr/bin/env python3
# gen_precision_report.py — A股回测·精确率首出报告生成器
#
# 只读 data/backtest.json，绝不写仓库任何文件（除临时 report.md 在 CI 工作区，不提交）。
# 关键行为：仅当 maturedCount > 0（观察窗首次闭合、精确命中率可计算）时才产出 report.md；
#           maturedCount == 0 时静默 exit 0，不产出、不通知（避免 CI 噪声）。
#
# 设计目的：配合 .github/workflows/backtest-watch.yml 在 GitHub 服务器定时运行，
#          脱离用户本机，用户不开电脑也能在精确率首出时收到 GitHub Issue 通知。
#
# 量纲约定（已核对真实 backtest.json）：
#  - 已是百分比(0-100)：realizedHitRate、levelPrecisionWithin5Pct
#  - 是比率/分数(0-1 或 -0.12 等)：preciseRealizedHitRate、levelPrecisionMedianDev*、
#    summary[].preciseHitRate、summary[].precDevMedian、realizationSummary.byCat[].openBestMed
#  => 前者用 fmt_pct（直接加%），后者用 fmt_ratio（×100 后加%）。
import json
import os
import sys
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
# 允许测试时通过环境变量指定数据源，CI 中默认读 data/backtest.json
SRC = os.environ.get("BACKTEST_SRC", os.path.join(BASE, "data", "backtest.json"))
OUT = os.path.join(BASE, "report.md")


def fmt_pct(v):
    """已是百分比(0-100)的值，直接加 %。"""
    if v is None:
        return "None"
    try:
        return "%.1f%%" % float(v)
    except Exception:
        return str(v)


def fmt_ratio(v):
    """比率/分数(0-1 或 -0.12 等)，乘 100 后加 %。"""
    if v is None:
        return "None"
    try:
        return "%.1f%%" % (float(v) * 100)
    except Exception:
        return str(v)


def fmt_side(obj):
    if isinstance(obj, dict):
        return "；".join("%s %s" % (k, fmt_ratio(v)) for k, v in obj.items())
    return str(obj)


def main():
    if not os.path.exists(SRC):
        print("[gen_precision_report] 缺少数据源 %s，跳过" % SRC, file=sys.stderr)
        sys.exit(0)
    with open(SRC, encoding="utf-8") as f:
        s = json.load(f)

    mc = s.get("maturedCount", 0) or 0
    if not mc or mc <= 0:
        # 未成熟：静默，不产出报告（CI 步骤据此跳过建 Issue）
        sys.exit(0)

    precise = s.get("preciseRealizedHitRate")          # 比率
    band = s.get("realizedHitRate")                    # 已是百分比
    within5 = s.get("levelPrecisionWithin5Pct")        # 已是百分比
    dev_side = s.get("levelPrecisionMedianDevBySide")  # 比率 dict
    p16 = s.get("levelPrecisionP16BySide")             # 比率 dict
    p84 = s.get("levelPrecisionP84BySide")             # 比率 dict
    realization = s.get("realizationSummary", {}) or {}
    summary = s.get("summary", []) or []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    L = []
    L.append("# 📊 A股斐波那契回测·精确率首出报告")
    L.append("")
    L.append("> 生成时间：%s ｜ 本报告由 GitHub Actions 自动生成，只读分析，未改动仓库。" % now)
    L.append("")
    L.append("## 一、精确率首出")
    L.append("")
    L.append("- **全局精确命中率（真实目标价位触达）**：%s" % fmt_ratio(precise))
    L.append("- **成熟样本量（窗口已闭合的 目标×锚点 对数）**：%d（精确已评估 preciseEvaluated=%s）" % (mc, s.get("preciseEvaluated")))
    L.append("- 对照此前代理指标：band 宽松触达率 %s、±5%% 价位达标率 %s。" % (fmt_pct(band), fmt_pct(within5)))
    L.append("- **本质差异**：band 触达率衡量“价格是否蹭到 ±σ 置信带”（宽松），精确命中率衡量“价格是否真实落在艾略特目标价位附近”（严格）。二者不可混为一谈——精确率才是校验目标定价精度的硬指标。")
    L.append("")
    L.append("## 二、分侧验证（sell / buy）")
    L.append("")
    L.append("- 分侧中位偏差：%s（此前分侧点估计校准：sell ≈ −12.6%%， buy ≈ +0.3%%）" % fmt_side(dev_side))
    if p16 is not None or p84 is not None:
        L.append("- 分侧 16~84 分位区间：P16=%s / P84=%s（若成熟样本足够，可用于收窄不确定带）" % (fmt_side(p16), fmt_side(p84)))
    L.append("")
    L.append("## 三、分类精确率")
    L.append("")
    L.append("| 类别 | 目标 | 精确命中率 | 精确已评 | 成熟 | 状态 |")
    L.append("|---|---|---|---|---|---|")
    for r in summary:
        L.append("| %s | %s | %s | %s | %s | %s |" % (
            r.get("cat"), r.get("key"), fmt_ratio(r.get("preciseHitRate")),
            r.get("preciseEval"), r.get("matured"), r.get("openStatus", "—")))
    L.append("")
    L.append("> 说明：matured<5 的类别样本偏薄，读数仅供参考、不宜据此重校准；matured≥5 可视为该类别进入高置信区。")
    L.append("")
    L.append("## 四、实时追踪现状")
    L.append("")
    L.append("- 观察中(open)目标数：%s ｜ 待观察窗闭合(pending)：%s" % (realization.get("totalOpen"), realization.get("totalPending")))
    bycat = realization.get("byCat", []) or []
    if bycat:
        L.append("")
        L.append("| 类别 | 目标 | 观察中 | 已过时长(中位) | 预期(中位) | 最佳接近(中位) | 状态 |")
        L.append("|---|---|---|---|---|---|---|")
        for c in bycat:
            obm = c.get("openBestMed")
            obm_s = fmt_ratio(obm) if isinstance(obm, (int, float)) else obm
            L.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                c.get("cat"), c.get("key"), c.get("open"),
                c.get("openElapsedMed"), c.get("openExpMed"), obm_s, c.get("status")))
    L.append("")
    L.append("## 五、下一轮回测改进建议")
    L.append("")
    enough = [r for r in summary if (r.get("matured") or 0) >= 5]
    thin = [r for r in summary if 0 < (r.get("matured") or 0) < 5]
    if enough:
        L.append("- ✅ 已有 %d 个类别成熟样本≥5，可从全局兜底升级为逐类高置信校准：" % len(enough))
        for r in enough:
            L.append("  - **%s / %s**：精确率 %s，建议据此微调该类别 calibPx 与 calibLo/Hi（仅收紧/放宽不确定带，不动艾略特目标价位/概率·铁律⑦）。" % (
                r.get("cat"), r.get("key"), fmt_ratio(r.get("preciseHitRate"))))
    if thin:
        L.append("- ⏳ 以下类别已有少量成熟样本但 <5，建议继续观察、暂不重校准：%s。" % "、".join(
            "%s/%s" % (r.get("cat"), r.get("key")) for r in thin))
    if not enough and not thin:
        L.append("- 当前首批成熟样本有限，维持现有校准（sell −12.6%% / buy +0.3%% 点估计 + 同侧 16~84 分位带）即可，待样本积累后再做下一轮。")
    L.append("- 所有改进均须**加性落地**：不改动艾略特目标价位/概率（铁律⑦）。如需落实，请在 WorkBuddy 会话中确认，由小一跑齐 13 道门禁后提交。")
    L.append("")
    L.append("---")
    L.append("本报告为只读分析，未改动仓库。若需落实上述改进，请在 WorkBuddy 会话中确认，由小一在会话内跑齐 13 道门禁后提交。")

    md = "\n".join(L) + "\n"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(md)
    print(md)


if __name__ == "__main__":
    main()
