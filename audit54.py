# -*- coding: utf-8 -*-
"""audit54（R824）：浪⑤启动闸门 / 情景路径时间序 / 校验卡 ok 派生 —— 一致性守门员。

背景（R824 实证，均为"判据退化"同族缺陷）：
  ① 旧状态判据 `last_close >= 浪④底` 在「浪④成立」前提下**恒真** —— 回测 53/53 = 100%，
     看板连续 52 个交易日显示"浪⑤已启动"，而同期指数净推进仅 +0.41%（卖① 完成度 28%）；
     且 `scenarioSwitch.active` 复制同一条恒真判据，两处"互相印证"不构成独立验证。
  ② `scenarios` 各序列的近端锚点硬编码字面量 "2026-09-30"（写码当日日期），数据推进后
     该点落在 last_date 之前 ⇒ 三个情景 points **全部时间倒序**，前端折线 x 轴折返。
  ③ `rules`（5/6 条）与 `ratioCheck`（4/5 项）的 ok 硬编码 True ⇒ 前端 index.html 的
     warn 分支成为死代码，且实测越界值（浪2 64.4% / 浪4 42.5% / 子浪ⅱ 22.0%）被标"通过"。

本守门员把上述三条固化为**可复算**的不变量，防回退。
只读 data/data.js；不写任何文件。
"""
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
# 支持可选路径参数（便于正控测试：注入缺陷后应 FAIL），默认读产物 data/data.js
_p = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "data", "data.js")
D = json.loads(open(_p, encoding="utf-8").read().split("=", 1)[1].rstrip().rstrip(";"))

problems = []


def chk(cond, msg):
    if not cond:
        problems.append(msg)


# ---------- ① 情景路径日期必须非降序 ----------
for i, sc in enumerate(D.get("scenarios") or []):
    ds = [q[0] for q in sc.get("points") or []]
    chk(len(ds) >= 2, "scenarios[%d] points 少于 2 点" % i)
    chk(all(ds[j] <= ds[j + 1] for j in range(len(ds) - 1)),
        "scenarios[%d] 日期非单调（时间倒序）: %s" % (i, ds))

# ---------- ② 浪⑤启动闸门自洽 + 与 state/scenarioSwitch 同源 ----------
wg = D.get("wave5Gate")
chk(isinstance(wg, dict), "wave5Gate 缺失（R824 可证伪披露字段）")
if isinstance(wg, dict):
    lc = D.get("lastClose")
    kl = float(D["tradePlan"]["stopLine"]["price"])
    cl = float(wg["confirmLevel"])
    exp_status = "invalid" if lc < kl else ("confirmed" if lc >= cl else "pending")
    chk(wg.get("status") == exp_status,
        "wave5Gate.status=%s 与按 (lastClose=%s, 铁律=%s, 确认位=%s) 复算的 %s 不一致"
        % (wg.get("status"), lc, kl, cl, exp_status))
    chk(wg.get("confirmed") == (wg.get("status") == "confirmed"),
        "wave5Gate.confirmed=%s 与 status=%s 不符" % (wg.get("confirmed"), wg.get("status")))
    chk(abs((lc / cl - 1.0) * 100.0 - float(wg.get("deviationPct"))) < 0.02,
        "wave5Gate.deviationPct=%s 与复算值 %.2f 不符"
        % (wg.get("deviationPct"), (lc / cl - 1.0) * 100.0))
    chk(float(wg.get("daysBudget", 0)) > 0, "wave5Gate.daysBudget 应为正数（=卖① expDays）")

    st = D.get("state") or {}
    exp_cls = {"confirmed": "gold", "invalid": "danger", "pending": "ghost"}[wg["status"]]
    chk(st.get("cls") == exp_cls,
        "state.cls=%s 与 wave5Gate.status=%s 应对应的 %s 不符（状态与披露脱节）"
        % (st.get("cls"), wg["status"], exp_cls))
    chk(isinstance(st.get("wave5Gate"), dict), "state.wave5Gate 缺失（应与顶层同源）")

    ss = D.get("scenarioSwitch") or {}
    exp_scn = {"confirmed": "strong", "invalid": "risk", "pending": "base"}[wg["status"]]
    chk(ss.get("active") == exp_scn,
        "scenarioSwitch.active=%s 与 wave5Gate.status=%s 应对应的 %s 不符（情景与徽章自相矛盾）"
        % (ss.get("active"), wg["status"], exp_scn))
    chk(isinstance(ss.get("w5Gate"), dict), "scenarioSwitch.w5Gate 缺失")

    # 判据不得退化为恒真：confirmLevel 必须**严格高于**浪④底（否则等价于旧的恒真判据）
    w4 = float(ss.get("w4Low") if ss.get("w4Low") is not None else 0)
    chk(cl > w4,
        "wave5Gate.confirmLevel=%.2f 未严格高于浪④底 %.2f ⇒ 判据退化为恒真" % (cl, w4))

# ---------- ③ ratioCheck 的 ok 必须由 actual 对 theory 区间复算得出 ----------
for r in D.get("ratioCheck") or []:
    try:
        actual = float(str(r.get("actual", "")).rstrip("%"))
    except Exception:
        continue
    th = str(r.get("theory", ""))
    # 注意：theory 形如 "50%~61.8%"（百分号夹在波浪号**之前**），故正则须容许 ~ 前的可选 %。
    # 若写成 ([-\d.]+)\s*[~～] 会静默匹配失败 → continue → 该条永不校验（正控实测漏报）。
    m = re.search(r"([-\d.]+)\s*%?\s*[~～]\s*([-\d.]+)", th)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        exp_ok = lo <= actual <= hi
    elif th.strip() == "1.618":
        exp_ok = actual >= 1.5          # 与 build_data._ok3 同口径
    else:
        continue
    chk(r.get("ok") is exp_ok,
        "ratioCheck[%s] actual=%s theory=%s ok=%s，按区间复算应为 %s（疑似硬编码）"
        % (r.get("item"), r.get("actual"), th, r.get("ok"), exp_ok))

# ---------- ④ 结构性铁律必须通过（ok 由实测派生，不得被写成恒真或恒假） ----------
_rn = [r.get("name", "") for r in (D.get("rules") or [])]
chk(any(n.startswith("铁律") for n in _rn), "rules 中缺少铁律条目")
for r in D.get("rules") or []:
    if str(r.get("name", "")).startswith("铁律"):
        chk(r.get("ok") is True,
            "结构性铁律「%s」未通过 ⇒ 浪型标注可能已损坏" % r.get("name"))

# ---------- 结果 ----------
if problems:
    print("audit54 失败：")
    for p in problems:
        print("  ✗", p)
    sys.exit(1)
print("audit54 结论：浪⑤闸门 / 情景路径时间序 / 校验卡 ok 派生 全部一致 ✓")
