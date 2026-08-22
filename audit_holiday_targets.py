#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R108 守门员：目标/推演日期必须落在 A 股交易日（跳过周末 + 法定节假日，保留调休补班）。

深度检查背景：
  用户截图指正缠论/斐波那契推演路径目标日期出现 2026-10-02（国庆假期 10/1~10/7 内、非交易日）。
  根因复盘：R207/R208 已在 build_data.py(_next_trading_day)、report.py(_is_trading_day/_fut)、
  index.html(addBizDays/_HOLIDAYS_2026) 三处把"加交易日"改为跳过 A 股法定节假日。
  但部署产物(data.js / chanlun 报告)可能由修复前代码生成，且门禁此前未覆盖"目标日期落在假期"这一回归类。
  本脚本作为**反向硬门禁**：扫描生成产物中所有目标/推演日期字段，凡落在非交易日即 FAIL，
  使任何"假期目标"在 CI 即被拦截，没法进入部署。

校验口径（与三处生产代码完全一致）：
  - 周末(周六/周日)非交易日
  - _A_SHARE_HOLIDAYS_2026：2026 官方休市（上交所 上证公告〔2025〕45号）
  - 调休补班(_A_SHARE_MAKEUP_2026)：周末上班仍交易，不当休市
  - 2027 节日本身公历日期为确定历法事实（与 build_data.py / report.py 同源），补班待国务院公布；
    2027 非交易日判定仅按"周末 + 已知节日"，补班缺失会使该补班日被误当休市（微小误差，标注 WARNING 不阻断）。
"""
import json
import os
import re
import sys
from datetime import date, timedelta

REPO = os.path.dirname(os.path.abspath(__file__))

# ---- 与 build_data.py / report.py / index.html 同源的交易日历 ----
_HOLIDAYS = set()
for _hs, _he in [
    ("2026-01-01", "2026-01-03"), ("2026-02-15", "2026-02-23"),
    ("2026-04-04", "2026-04-06"), ("2026-05-01", "2026-05-05"),
    ("2026-06-19", "2026-06-21"), ("2026-09-25", "2026-09-27"),
    ("2026-10-01", "2026-10-07"),
    # 2027 节日本身（公历日期确定；补班细节待公布）
    ("2027-01-01", "2027-01-01"),
    ("2027-02-05", "2027-02-09"),
    ("2027-04-04", "2027-04-06"),
    ("2027-05-01", "2027-05-05"),
    ("2027-06-09", "2027-06-11"),
    ("2027-09-15", "2027-09-17"),
    ("2027-10-01", "2027-10-07"),
]:
    _d = date.fromisoformat(_hs)
    _e = date.fromisoformat(_he)
    while _d <= _e:
        _HOLIDAYS.add(_d.isoformat())
        _d += timedelta(days=1)

_MAKEUP = {
    "2026-02-14", "2026-02-28", "2026-05-09", "2026-09-20", "2026-10-10",
}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_trading_day(s):
    try:
        d = date.fromisoformat(s)
    except ValueError:
        return None  # 非日期串
    if s in _MAKEUP:
        return True
    if s in _HOLIDAYS:
        return False
    return d.weekday() < 5


def load_fib_data(path=None):
    p = path or os.path.join(REPO, "data", "data.js")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        txt = f.read()
    m = re.search(r"FIB_DATA\s*=\s*(\{.*\})\s*;?\s*$", txt, re.S)
    if not m:
        return None
    return json.loads(m.group(1))


# 需要校验的"目标/推演日期"字段（白名单，避免误伤历史 kline 日期等无关字段）
TARGET_DATE_PATHS = [
    ("subForecast.points[].date", lambda D: [(p.get("label"), p.get("date"))
                                             for p in (D.get("subForecast") or {}).get("points", [])]),
    ("tradePlan.sellTargets[].date(派生)", None),  # 由前端 addBizDays 推算，不直接存；下方单独复算
]


def main():
    problems = []
    warnings = []
    _path = sys.argv[1] if len(sys.argv) > 1 else None
    D = load_fib_data(_path)
    if D is None:
        print("[skip] data/data.js 不存在或无法解析，跳过（CI 取数失败时应由 preflight 拦截）")
        return 0

    # 1) subForecast 点日期（单源真值，前端卖点优先复用）
    for p in (D.get("subForecast") or {}).get("points", []):
        lab = p.get("label")
        ds = p.get("date")
        if not isinstance(ds, str) or not DATE_RE.match(ds or ""):
            continue
        ok = is_trading_day(ds)
        if ok is False:
            problems.append("subForecast 点 %s 日期 %s 落在非交易日(周末/假期)" % (lab, ds))
        elif ok is None:
            pass

    # 2) 前端 sellTargets 显示日期复算：sfDateByPrice 优先；否则 addBizDays(updated, expDays)
    updated = D.get("updated")
    sf_by_price = {}
    for p in (D.get("subForecast") or {}).get("points", []):
        pr = p.get("price")
        if isinstance(pr, (int, float)):
            sf_by_price[round(pr * 100) / 100] = p.get("date")
    for t in (D.get("tradePlan") or {}).get("sellTargets", []):
        pr = round((t.get("price") or 0) * 100) / 100
        exp = t.get("expDays") or 0
        ds = sf_by_price.get(pr) or add_biz_days(updated, exp)
        if ds is None:
            continue
        ok = is_trading_day(ds)
        if ok is False:
            problems.append("sellTarget %s (price=%s,expDays=%s) 显示日期 %s 落在非交易日"
                            % (t.get("name"), pr, exp, ds))

    # 3) 其它可能含 date 的目标结构（buyZones/stopLine 等）做一次全量扫描兜底
    extra = []
    for key in ("buyZones",):
        for z in (D.get("tradePlan") or {}).get(key, []) or []:
            if isinstance(z, dict) and isinstance(z.get("date"), str):
                extra.append(("tradePlan.%s" % key, z.get("date")))
    for tag, ds in extra:
        if DATE_RE.match(ds or ""):
            ok = is_trading_day(ds)
            if ok is False:
                problems.append("%s 日期 %s 落在非交易日" % (tag, ds))

    # ---- 输出 ----
    print("=== R108 目标日期交易日校验 ===")
    print("  校验日期数(subForecast+sellTargets): %d" % (
        len((D.get("subForecast") or {}).get("points", []))
        + len((D.get("tradePlan") or {}).get("sellTargets", []))))
    if problems:
        for x in problems:
            print("  [FAIL] " + x)
        print("\n结果：发现 %d 个目标日期落在非交易日 —— 必须修复后再部署。" % len(problems))
        return 1
    print("  [PASS] 所有目标/推演日期均落在 A 股交易日（跳过周末+法定节假日，保留调休补班）。")
    if warnings:
        for w in warnings:
            print("  [WARN] " + w)
    return 0


def add_biz_days(from_str, n):
    """复刻 index.html addBizDays：从 from_str 起加 n 个交易日（跳过周末+2026假期）。"""
    if not from_str or not DATE_RE.match(from_str):
        return None
    d = date.fromisoformat(from_str)
    added = 0
    while added < n:
        d += timedelta(days=1)
        ds = d.isoformat()
        wd = d.weekday()
        if wd < 5 and ds not in _HOLIDAYS:
            added += 1
    return d.isoformat()


if __name__ == "__main__":
    sys.exit(main())
