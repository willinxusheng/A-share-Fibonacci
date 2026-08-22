# -*- coding: utf-8 -*-
"""R119 常驻审计：sentiment v3 契约深核（contra 单一真值 + forecast 插值一致性）。

覆盖（防回归）：
  A. today.contra.bands 各档 N/fwd20 与独立重算逐位一致（单一真值，防统计口径漂移）
  B. contra 各档 N 合计 = 有未来20日收益的样本数（history - 尾部20日，非全量）
  C. forecast 全部日期落在 subForecast 锚点区间 [首,末]，严格递增
  D. forecast 每个点 label 与 score 分档一致（<20冰点/<40偏冷/<60中性/<80偏热/else狂热）
  E. today.score/label 与 history 末点衔接一致（±0.011）
  F. contra.note 包含 N 合计与分数区间（诚实口径可追溯）

用法：python _audit_sentiment_contract.py  （退出码 0=全过，1=有问题）
"""
import json
import re
import sys

REPO = r"C:\Users\Administrator\WorkBuddy\2026-08-04-23-16-18\A-share-Fibonacci"

problems = []
checks = []


def chk(cond, msg):
    checks.append((cond, msg))
    if not cond:
        problems.append(msg)


def load_js(path, var):
    raw = open(path, encoding="utf-8").read()
    body = re.sub(r"^\s*window\.%s\s*=\s*" % re.escape(var), "", raw).rstrip().rstrip(";")
    return json.loads(body)


def main():
    data = load_js(REPO + r"\data\data.js", "FIB_DATA")
    sent = json.load(open(REPO + r"\data\sentiment.json", encoding="utf-8"))

    kd = data["kline"]["dates"]
    kc = data["kline"]["close"]
    hist = sent.get("history") or []
    fcst = sent.get("forecast") or []
    contra = (sent.get("today") or {}).get("contra") or {}
    bands = contra.get("bands") or []

    # ---------- A. contra 单一真值 ----------
    chk(len(bands) == 5, "contra.bands 应为 5 档，实际 %d" % len(bands))
    base = len(kd) - len(hist)
    exp_bands = [("冰点", 0, 20), ("偏冷", 20, 40), ("中性", 40, 60), ("偏热", 60, 80), ("狂热", 80, 101)]
    recomputed = {}
    for lab, lo, hi in exp_bands:
        n, s = 0, 0.0
        for off, h in enumerate(hist):
            if lo <= h["score"] < hi:
                j = base + off + 20
                if j < len(kc):
                    n += 1
                    s += (kc[j] / kc[base + off] - 1.0) * 100.0
        recomputed[lab] = {"n": n, "fwd20": round(s / n, 2) if n else None}
    for b in bands:
        lab = b.get("label")
        exp = recomputed.get(lab, {})
        n_ok = b.get("n") == exp.get("n")
        f_ok = (b.get("fwd20") is None and exp.get("fwd20") is None) or \
               abs((b.get("fwd20") or 0) - (exp.get("fwd20") or 0)) < 0.011
        chk(n_ok and f_ok, "contra[%s] 不一致: 存储 N=%s/fwd20=%s vs 独立 N=%s/fwd20=%s"
            % (lab, b.get("n"), b.get("fwd20"), exp.get("n"), exp.get("fwd20")))

    # ---------- B. N 合计语义 ----------
    tot_n = sum(b.get("n", 0) for b in bands)
    expect_n = max(len(hist) - 20, 0)  # 尾部 20 日无未来收益
    chk(tot_n == expect_n, "contra 各档 N 合计 %d != 预期 %d（history %d - 尾部20）"
        % (tot_n, expect_n, len(hist)))

    # ---------- C. forecast 日期范围 ----------
    sf_pts = (data.get("subForecast") or {}).get("points") or []
    chk(len(sf_pts) >= 2, "subForecast.points 不足 2 锚点")
    if sf_pts:
        sf_first, sf_last = sf_pts[0]["date"], sf_pts[-1]["date"]
        fcst_dates = [f["date"] for f in fcst]
        chk(all(sf_first <= d <= sf_last for d in fcst_dates),
            "forecast %d 个日期超出锚点区间 [%s,%s]: %r" % (
                len([d for d in fcst_dates if not (sf_first <= d <= sf_last)]),
                sf_first, sf_last,
                [d for d in fcst_dates if not (sf_first <= d <= sf_last)][:5]))
        chk(all(b > a for a, b in zip(fcst_dates, fcst_dates[1:])), "forecast 日期非严格递增")

    # ---------- D. forecast label 分档一致性 ----------
    def label_of(sc):
        for hi, lab in [(20, "冰点"), (40, "偏冷"), (60, "中性"), (80, "偏热")]:
            if sc < hi:
                return lab
        return "狂热"
    bad_lbl = [f for f in fcst if f.get("label") != label_of(f.get("score"))]
    chk(not bad_lbl, "forecast %d 个点 label 与 score 分档不符: %r" % (len(bad_lbl), bad_lbl[:3]))

    # ---------- E. today 与 history 末点衔接 ----------
    if hist:
        last_h = hist[-1]
        t = sent.get("today") or {}
        chk(abs((t.get("score") or 0) - last_h["score"]) <= 0.011,
            "today score %s 与 history 末点 %s 不一致" % (t.get("score"), last_h["score"]))
        chk(t.get("label") == last_h["label"],
            "today label %s 与 history 末点 %s 不一致" % (t.get("label"), last_h["label"]))

    # ---------- F. contra.note 诚实口径 ----------
    note = contra.get("note") or ""
    chk(str(tot_n) in note, "contra.note 未含 N 合计 %d（口径不可追溯）" % tot_n)
    if hist:
        sc_min = min(h["score"] for h in hist)
        sc_max = max(h["score"] for h in hist)
        chk(("%.1f~%.1f" % (sc_min, sc_max)) in note, "contra.note 未含分数区间 %.1f~%.1f" % (sc_min, sc_max))

    print("检查项 %d 条，问题 %d 条" % (len(checks), len(problems)))
    for cond, msg in checks:
        print(("[PASS] " if cond else "[FAIL] ") + msg)
    if problems:
        print("\n共 %d 个问题，退出码 1" % len(problems))
        sys.exit(1)
    print("\n全部通过 ✓")
    sys.exit(0)


if __name__ == "__main__":
    main()
