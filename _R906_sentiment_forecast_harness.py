# -*- coding: utf-8 -*-
"""R906: 情绪 forecast 准确率回测 harness（纯标准库，不 import 生产模块）。

背景：R905 修了动量衔接量纲分裂后，首点跳变一度 11.0→5.8；但 11-27 情绪跌到
24.1 冰点时跳变反弹到 12.6，突破软审计阈值 12。上轮决策「不拍脑袋改校准栈，
先建 harness 用数据决定」。本脚本即该 harness。

数据源（trust-but-verify，全部真实可溯源）：
  - 预测值：git 历史中 data/sentiment.json 的 75 个版本（按 asOf 去重 = 75 个
    独立 forecast 快照），每个快照的 forecast[] = 当时对未来的情绪分预测。
  - 真值  ：当前 data/sentiment.json 的 history[]（逐日回算的已实现情绪分）。
  - 对齐  ：forecast[].date 命中 history[].date 才算成熟点；未命中（未来）剔除。

注意口径边界（诚实标注）：
  1) history 是「当前算法逐日回算」的已实现分，而老快照 forecast 是「当时算法」
     生成的预测 —— 这是真实 out-of-sample：当时的预测 vs 统一口径的已实现分。
  2) 快照非每个交易日都有（CI 每日多次重建，按 asOf 去重后约每 1-3 日一个），
     成熟点在同一快照内高度相关，故同时报「按点」与「按快照」两种聚合。
  3) 基线 A=今日分恒定延续；基线 B=快照当时 history 近 20 日均值；
     基线 C=首点跳变归零（pred0 := today.score，检验 R905 后剩余校准栈跳变
     是否有预测价值 —— 这是本 harness 的核心决策实验）。

输出：控制台结构化摘要 + data/_r906_forecast_harness.json（不写生产数据契约文件）。
"""
import collections
import json
import math
import os
import statistics
import subprocess

REPO = os.path.dirname(os.path.abspath(__file__))


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def git_snapshots():
    """从 git 历史提取 forecast 快照，按 asOf 去重（首遇=最新提交）。"""
    out = subprocess.check_output(
        ["git", "-C", REPO, "log", "--format=%H %ad", "--date=short", "--", "data/sentiment.json"],
        text=True,
    )
    snaps = {}
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        h, ad = parts[0], parts[1]
        try:
            raw = subprocess.check_output(
                ["git", "-C", REPO, "show", "%s:data/sentiment.json" % h],
                text=True, stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            continue
        try:
            d = json.loads(raw)
        except Exception:
            continue
        asof = d.get("asOf") or (d.get("today") or {}).get("date")
        fc = d.get("forecast") or []
        hist = d.get("history") or []
        if not asof or not fc or not hist:
            continue
        td = asof[:10]
        if td in snaps:
            continue
        snaps[td] = {
            "asof": td,
            "commit": h[:8],
            "today": (d.get("today") or {}).get("score"),
            "forecast": [(f.get("date"), f.get("score")) for f in fc if f.get("date") and f.get("score") is not None],
            "hist_tail20": [h0.get("score") for h0 in hist[-20:] if h0.get("score") is not None],
        }
    return snaps


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    den = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / den, (c + m) / den)


def mae(xs):
    return sum(abs(x) for x in xs) / len(xs) if xs else None


def bucket_jump(j):
    if j <= 3:
        return "跳变<=3"
    if j <= 6:
        return "跳变3-6"
    if j <= 10:
        return "跳变6-10"
    return "跳变>10"


def bucket_h(h):
    if h <= 1:
        return "T+1"
    if h <= 5:
        return "T+2~5"
    if h <= 10:
        return "T+6~10"
    if h <= 20:
        return "T+11~20"
    return "T+21+"


def main():
    cur = load_json(os.path.join(REPO, "data", "sentiment.json"))
    hist = cur["history"]
    hmap = {x["date"]: x["score"] for x in hist}
    hidx = {x["date"]: i for i, x in enumerate(hist)}
    snaps = git_snapshots()

    # 成熟点池：每条 = (asof, horizon, pred, actual, today, base_mean, jump, pred0)
    points = []
    snap_stats = []
    for td, s in sorted(snaps.items()):
        today = s["today"]
        if today is None or td not in hidx:
            continue
        base_mean = statistics.mean(s["hist_tail20"]) if s["hist_tail20"] else today
        fc = s["forecast"]
        if not fc:
            continue
        jump = abs(fc[0][1] - today)
        local = []
        for d, pred in fc:
            if d not in hmap:
                continue
            actual = hmap[d]
            h = hidx[d] - hidx[td]
            if h <= 0:
                continue
            local.append((td, h, pred, actual, today, base_mean, jump, fc[0][1]))
        if not local:
            continue
        snap_stats.append({"asof": td, "jump": jump, "n": len(local),
                           "mae": mae([abs(p[2] - p[3]) for p in local])})
        points.extend(local)

    print("=" * 74)
    print("R906 情绪 forecast 准确率回测 harness")
    print("=" * 74)
    print("快照 %d 个（有成熟点 %d）｜成熟预测点 %d 个｜真值 history %d 天"
          % (len(snaps), len(snap_stats), len(points), len(hist)))
    if not points:
        print("无成熟点，终止。")
        return

    # 1) 整体：三种预测口径的 MAE
    e_pred = [p[2] - p[3] for p in points]
    e_today = [p[4] - p[3] for p in points]
    e_mean = [p[5] - p[3] for p in points]
    # 反事实：仅首点跳变归零（forecast0 := today），其余不动 —— 检验剩余校准栈跳变是否有预测价值
    e_nosmooth = []
    for p in points:
        _, h, pred, actual, today, base_mean, jump, pred0 = p
        delta = today - pred0  # 需要平移到 today 的量
        # 保守反事实：仅对首点平移（h==1），其余不动 —— 检验「首点跳变是否有预测价值」
        adj = pred + (delta if h == 1 else 0.0)
        e_nosmooth.append(adj - actual)

    def report(label, errs):
        m = mae(errs)
        bias = sum(errs) / len(errs)
        print("  %-22s MAE=%5.2f  偏差=%+5.2f  n=%d" % (label, m, bias, len(errs)))
        return m

    print("\n── 1. 整体误差（越低越好；偏差=预测-实际，正=高估）──")
    m_pred = report("forecast 实际预测", e_pred)
    m_today = report("基线A 今日分延续", e_today)
    m_mean = report("基线B 近20日均值", e_mean)
    m_nosm = report("反事实 首点跳变归零", e_nosmooth)
    print("  → forecast vs 基线A: %+.2f 分（%s）"
          % (m_pred - m_today, "forecast 更准 ✅" if m_pred < m_today else "不如今日延续 ❌"))
    print("  → forecast vs 基线B: %+.2f 分（%s）"
          % (m_pred - m_mean, "forecast 更准 ✅" if m_pred < m_mean else "不如均值 ❌"))
    print("  → 首点归零反事实: %+.2f 分（%s）"
          % (m_nosm - m_pred, "平滑更准 → 支持削跳变 ✅" if m_nosm < m_pred else "平滑更差 → 保留跳变 ❌"))

    # 2) 按 horizon 分层
    print("\n── 2. 按预测距离分层 ──")
    byh = collections.defaultdict(list)
    for p in points:
        byh[bucket_h(p[1])].append(p)
    for k in ["T+1", "T+2~5", "T+6~10", "T+11~20", "T+21+"]:
        ps = byh.get(k, [])
        if not ps:
            continue
        print("  %-8s n=%4d  forecast MAE=%5.2f  今日延续 MAE=%5.2f  %s"
              % (k, len(ps), mae([p[2] - p[3] for p in ps]), mae([p[4] - p[3] for p in ps]),
                 "✅" if mae([p[2] - p[3] for p in ps]) < mae([p[4] - p[3] for p in ps]) else "❌"))

    # 3) 按跳变分层（核心决策实验）
    print("\n── 3. 按首点跳变分层（核心：跳变大是否=预测更差？）──")
    byj = collections.defaultdict(list)
    for p in points:
        byj[bucket_jump(p[6])].append(p)
    for k in ["跳变<=3", "跳变3-6", "跳变6-10", "跳变>10"]:
        ps = byj.get(k, [])
        if not ps:
            continue
        e = [p[2] - p[3] for p in ps]
        e0 = [p[4] - p[3] for p in ps]
        print("  %-10s n=%4d  forecast MAE=%5.2f  今日延续 MAE=%5.2f  forecast优势=%+.2f"
              % (k, len(ps), mae(e), mae(e0), mae(e) - mae(e0)))

    # 4) 首点方向命中（预测相对今日的涨跌方向 vs 实际相对今日的涨跌方向）
    print("\n── 4. 首点方向命中（T+1 预测方向 vs 实际方向）──")
    firsts = [p for p in points if p[1] == 1]
    hit = 0
    valid = 0
    for p in firsts:
        _, _, pred, actual, today, *_ = p
        pd_sign = 1 if pred > today else (-1 if pred < today else 0)
        ad_sign = 1 if actual > today else (-1 if actual < today else 0)
        if pd_sign == 0 or ad_sign == 0:
            continue
        valid += 1
        if pd_sign == ad_sign:
            hit += 1
    lo, hi = wilson(hit, valid)
    print("  n=%d 命中 %d = %.1f%%（95%%CI %.1f–%.1f）%s"
          % (valid, hit, 100.0 * hit / valid if valid else 0, 100 * lo, 100 * hi,
             "✅优于随机" if valid and lo > 0.5 else "❌与随机不可区分"))

    # 5) 按快照聚合（消除同快照内相关性的虚增）
    print("\n── 5. 按快照聚合（每快照一个 MAE，n=%d）──" % len(snap_stats))
    sms = [s["mae"] for s in snap_stats if s["mae"] is not None]
    print("  快照 MAE 中位数=%.2f  均值=%.2f  最差=%.2f"
          % (statistics.median(sms), statistics.mean(sms), max(sms)))

    # 保存证据文件
    out = {
        "asOf": cur.get("asOf"),
        "snapshots": len(snaps),
        "maturedSnapshots": len(snap_stats),
        "maturedPoints": len(points),
        "mae": {
            "forecast": round(m_pred, 3),
            "baseToday": round(m_today, 3),
            "baseMean20": round(m_mean, 3),
            "counterfactualNoJump": round(m_nosm, 3),
        },
        "firstDirHit": {"n": valid, "hit": hit, "ci95": [round(lo, 4), round(hi, 4)]},
        "byJump": {k: {"n": len(byj.get(k, [])), "mae": round(mae([p[2] - p[3] for p in byj.get(k, [])]), 3) if byj.get(k) else None} for k in ["跳变<=3", "跳变3-6", "跳变6-10", "跳变>10"]},
    }
    # 铁律：data/ 由 CI 独家写盘，本地只读。证据文件写到 /tmp，不进仓库、不碰 data/。
    op = "/tmp/r906_forecast_harness.json"
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n证据文件: %s（仓库外，不碰 data/）" % op)


if __name__ == "__main__":
    main()
