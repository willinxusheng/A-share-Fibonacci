# -*- coding: utf-8 -*-
"""audit54（R824 建 / R825 扩）：浪⑤启动闸门 / 情景路径时间序 / 校验卡 ok 派生 —— 一致性守门员。

R825 扩展（同一「判据退化」族的三个新实例，均已被市场事实验证）：
  ④ 铁律三 `ok = w4_low > KEY_LINE` —— **两端都是人工标注常量** ⇒ 对任何行情恒真。
     2026-10-22 收盘 3658.03 已跌破铁律线、最低 3574.27 已跌破标注浪④底，校验卡仍全绿。
     原断言「所有铁律 ok 必须 is True」本身也是恒真导向的（只能发现被写成 False，
     发现不了被写成"常量比常量"）。现改为**从 kline 复算**并与产物比对，允许合法 False。
  ⑤ wave5Gate 只看最后一根收盘、无记忆 ⇒ 10-22 的证伪被 10-23 反弹洗掉，invalid 无痕退回
     pending。现要求 status 与「破位记账(closeDays)」同口径复算，且 latched 必须自洽。
  ⑥ 浪④锚点披露 wave4Anchor 的 actual/undercut/breach/双口径回撤必须与 kline 一致；
     ratioCheck 的浪4回撤必须等于**实际最低**口径（旧口径用失效锚点，42.5% vs 实际 56.2%）。

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


# ---------- ① 情景路径日期必须非降序 + 不得出现过期/同日双价锚点 ----------
_ld = str(D.get("lastCloseDate") or D.get("updated") or "")
for i, sc in enumerate(D.get("scenarios") or []):
    ds = [q[0] for q in sc.get("points") or []]
    chk(len(ds) >= 2, "scenarios[%d] points 少于 2 点" % i)
    chk(all(ds[j] <= ds[j + 1] for j in range(len(ds) - 1)),
        "scenarios[%d] 日期非单调（时间倒序）: %s" % (i, ds))
    # R824b：硬编码未来日期字面量一旦过期会致时间倒序；护栏须把 ≤ last_date 的点剔除。
    # 三项断言把护栏本身也变成可复算不变量：
    #   ① 不得存在早于 last_date 的点（过期锚点必须已被剔除）；
    #   ② 至多一个点等于 last_date，且必须是首点（今日收盘现状锚）；
    #   ③ 同一日期不得出现两个不同价位（同日双价 = 自相矛盾，R824 初版曾引入）。
    if _ld:
        chk(all(d >= _ld for d in ds), "scenarios[%d] 含早于 last_date(%s) 的过期锚点: %s" % (i, _ld, ds))
        chk(sum(1 for d in ds if d == _ld) <= 1,
            "scenarios[%d] 有 %d 个点锚在同一日期 %s（同日双价自相矛盾）: %s"
            % (i, sum(1 for d in ds if d == _ld), _ld, ds))
        chk(not ds or ds[0] == _ld, "scenarios[%d] 首点应为 last_date(%s) 现状锚: %s" % (i, _ld, ds))
    _same_day = {}
    for _d, _p in (sc.get("points") or []):
        _same_day.setdefault(_d, set()).add(round(float(_p), 4))
    _dup = {k: v for k, v in _same_day.items() if len(v) > 1}
    chk(not _dup, "scenarios[%d] 同日期多价位（自相矛盾）: %s" % (i, _dup))

# ---------- ② 浪⑤启动闸门自洽 + 与 state/scenarioSwitch 同源 ----------
wg = D.get("wave5Gate")
chk(isinstance(wg, dict), "wave5Gate 缺失（R824 可证伪披露字段）")
if isinstance(wg, dict):
    lc = D.get("lastClose")
    kl = float(D["tradePlan"]["stopLine"]["price"])
    cl = float(wg["confirmLevel"])
    # R825：闸门改「破位记账（latching）」——只要自浪④底以来**曾有收盘**跌破铁律线，
    # 数浪即已证伪且不得随反弹复原。故此处的期望值必须与 build_data 同口径复算。
    _br0 = wg.get("breach") or {}
    _bd0 = int(_br0.get("closeDays") or 0)
    exp_status = ("invalid" if (_bd0 > 0 or lc < kl)
                  else ("confirmed" if lc >= cl else "pending"))
    chk(wg.get("status") == exp_status,
        "wave5Gate.status=%s 与按 (lastClose=%s, 铁律=%s, 确认位=%s, 破位记账=%d日) 复算的 %s 不一致"
        % (wg.get("status"), lc, kl, cl, _bd0, exp_status))
    chk(bool(wg.get("latched")) == (_bd0 > 0),
        "wave5Gate.latched=%s 与 breach.closeDays=%d 不符（破位记账自洽性）"
        % (wg.get("latched"), _bd0))
    if _bd0 > 0:
        chk(wg.get("status") == "invalid",
            "自浪④底以来收盘已 %d 个交易日跌破铁律线，status 却为 %s ⇒ 状态机可被反弹『自愈』"
            % (_bd0, wg.get("status")))
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

# ---------- ④ 铁律必须与 kline 实测一致（防「常量对常量」的恒真判据） ----------
# R825：原断言是「所有铁律 ok 必须 is True」。这本身就是一条**恒真导向**的断言：
# 它只能发现"被写成 False"，无法发现"被写成两个常量的比较"（如旧版
# `ok = w4_low > KEY_LINE`，两端都是人工标注常量 ⇒ 对任何行情都返回 True）。
# 事实：2026-10-22 收盘 3658.03 已跌破铁律线 3674.40、最低 3574.27 已跌破标注浪④底，
# 旧断言仍全绿。现改为**从 kline 复算**并与产物比对，允许合法为 False。
wp = D.get("wavePoints") or []
k = D.get("kline") or {}
ks_d = k.get("dates") or []
ks_o = k.get("ohlc") or []
ks_c = k.get("close") or []
chk(len(ks_d) == len(ks_o) == len(ks_c) and len(ks_d) > 0,
    "kline 数组长度不一致或为空 (dates=%d ohlc=%d close=%d)"
    % (len(ks_d), len(ks_o), len(ks_c)))
# data.js 的 kline 没有独立 low/high 字段，最低/最高价在 ohlc 里。
# 元素序实测为 [open, close, low, high]（非常规序！）—— 先自校验再取用，
# 避免"列序假设错误"导致复算静默取到错误价位（这正是 R825 初次运行踩到的坑）。
_ORDER_OK = bool(ks_o) and all(
    abs(float(o[1]) - float(c)) < 1e-6 for o, c in zip(ks_o[:5], ks_c[:5]))
chk(_ORDER_OK, "kline.ohlc 元素序不是 [open, close, low, high]，无法定位最低价")
ks_l = [float(o[2]) for o in ks_o] if _ORDER_OK else []
ks_h = [float(o[3]) for o in ks_o] if _ORDER_OK else []
_w3_top = next((p["date"] for p in wp if str(p.get("label", "")).startswith("浪3")), None)
chk(_w3_top is not None, "wavePoints 缺少浪3顶")
chk(any(str(r.get("name", "")).startswith("铁律") for r in (D.get("rules") or [])),
    "rules 中缺少铁律条目")
if _w3_top and ks_d and ks_l:
    _seg = [(d, l, c) for d, l, c in zip(ks_d, ks_l, ks_c) if d > _w3_top]
    chk(len(_seg) > 0, "浪③顶之后无 K 线，无法复算铁律三")
    _key = float(D["tradePlan"]["stopLine"]["price"])
    _ann = float(wp[-1]["price"])
    if _seg:
        _min_low = min(x[1] for x in _seg)
        _min_close = min(x[2] for x in _seg)
        _r3 = next((r for r in (D.get("rules") or [])
                    if str(r.get("name", "")).startswith("铁律三")), None)
        chk(_r3 is not None, "rules 缺少铁律三条目")
        if _r3 is not None:
            chk(_r3.get("ok") == (_min_low > _key),
                "铁律三 ok=%s，按 kline 复算（浪③顶以来最低 %.2f vs 铁律线 %.2f）应为 %s "
                "—— 疑似仍用标注常量比对（恒真、零判别力）"
                % (_r3.get("ok"), _min_low, _key, _min_low > _key))
            chk(_r3.get("live") is True,
                "铁律三是唯一随行情变化的结构判据，live 应为 True（否则用户会误当每日风控）")
        # 浪④锚点披露（R825）必须与 kline 一致
        wa = D.get("wave4Anchor")
        chk(isinstance(wa, dict), "wave4Anchor 缺失（R825 锚点有效性披露）")
        if isinstance(wa, dict):
            chk(abs(float(wa["actual"]["price"]) - _min_low) < 0.02,
                "wave4Anchor.actual=%.2f 与 kline 复算最低 %.2f 不一致"
                % (float(wa["actual"]["price"]), _min_low))
            chk(wa.get("undercut") == (_min_low < _ann),
                "wave4Anchor.undercut=%s 与复算（最低 %.2f vs 标注 %.2f）不符"
                % (wa.get("undercut"), _min_low, _ann))
            _br = wa.get("breach") or {}
            _exp_bd = sum(1 for x in _seg if x[2] < _key)
            chk(int(_br.get("closeDays", -1)) == _exp_bd,
                "wave4Anchor.breach.closeDays=%s 与复算 %d 不符"
                % (_br.get("closeDays"), _exp_bd))
            chk(int(_br.get("lowDays", -1)) == sum(1 for x in _seg if x[1] < _key),
                "wave4Anchor.breach.lowDays=%s 与复算 %d 不符"
                % (_br.get("lowDays"), sum(1 for x in _seg if x[1] < _key)))
            # 回撤双口径：必须与 kline 一致，且 ratioCheck 的浪4回撤 = 实际口径（不得用失效锚点）
            _w2p = next((p["price"] for p in wp if str(p.get("label", "")).startswith("浪2")), None)
            _w3p = next((p["price"] for p in wp if str(p.get("label", "")).startswith("浪3")), None)
            if _w2p and _w3p:
                _amp = float(_w3p) - float(_w2p)
                _exp_ret = round((float(_w3p) - _min_low) / _amp * 100, 1)
                chk(abs(float(wa.get("retraceActual", -999)) - _exp_ret) < 0.15,
                    "wave4Anchor.retraceActual=%s 与复算 %.1f%% 不符"
                    % (wa.get("retraceActual"), _exp_ret))
                chk(abs(float(wa.get("retraceAnnotated", -999))
                        - round((float(_w3p) - _ann) / _amp * 100, 1)) < 0.15,
                    "wave4Anchor.retraceAnnotated=%s 与复算不符" % wa.get("retraceAnnotated"))
                _rc = next((r for r in (D.get("ratioCheck") or [])
                            if "浪4" in str(r.get("item", ""))), None)
                if _rc is not None:
                    _av = float(str(_rc.get("actual", "")).rstrip("%"))
                    chk(abs(_av - _exp_ret) < 0.15,
                        "ratioCheck 浪4回撤=%s 与 kline 复算 %.1f%% 不一致（疑似仍以失效锚点计）"
                        % (_rc.get("actual"), _exp_ret))

# ---------- 结果 ----------
if problems:
    print("audit54 失败：")
    for p in problems:
        print("  ✗", p)
    sys.exit(1)
print("audit54 结论：浪⑤闸门 / 情景路径时间序 / 校验卡 ok 派生 全部一致 ✓")
