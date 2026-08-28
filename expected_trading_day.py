#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
expected_trading_day.py — A股「预期最后交易日」单一真源计算器
================================================================================
daily.yml（每日更新）与 freshness-watch.yml（自愈看门狗）共用此脚本，
避免两处各自写脆弱的"小时>=15"启发式而漂移。

用法（stdout 仅输出 YYYY-MM-DD，便于 shell `$(...)` 捕获；调试用 --explain 走 stderr）：
  python expected_trading_day.py                 # 打印「当前北京时刻」预期最后交易日
  python expected_trading_day.py --as-of "2026-08-27 16:30"   # 打印该时刻「预期最后交易日」
  python expected_trading_day.py --covers "2026-08-28 07:04"  # 打印该 fetchedAt 时刻数据所覆盖的交易日
  python expected_trading_day.py --explain        # 同时打印判定依据（stderr）

语义定义：
  · A股交易日 = 周一~周五 且 不在 2026 休市清单内。
    （2026 全部「补班/调休上班」日 1/4、2/14、2/28、5/9、9/20、10/10 均为周末，
     且上交所明示为「周末休市」→ 2026 无周末开市日，故不必特殊处理周末开市。）
  · 预期最后交易日（as of 某时刻）：
        - 若该时刻落在某交易日 且 >=15:00（收盘后·北京）→ 即该日；
        - 否则 → 该时刻之前最近的一个交易日。
    例：周四 13:42 → 预期 = 周三（当日未收盘）；周四 16:30 → 预期 = 周四；周六 → 预期 = 周五。
  · 数据所覆盖的交易日（as of fetchedAt 构建时刻）：
        - 若 fetchedAt 落在某交易日 且 >=15:00 → 即该日（收盘后构建，含当日收盘）；
        - 否则 → fetchedAt 之前最近的一个交易日（盘中/非交易日快照只含前一交易日收盘）。
    注：这与 data.js 中的 `updated` 字段语义一致（updated = 本快照覆盖的交易日）。

零第三方依赖，CI(Linux) 与本地(Windows) 均可离线运行。
================================================================================
"""
import sys
import datetime as dt

# ── A股休市日（上交所官方公告，北京时区，含被明示为周末休市的补班日）──────────────
# 按年份存放；未在表中的年份由 _generic_closed() 做「公历固定法定节假日」保底推定。
# 2026 来源：上海证券交易所《关于2026年部分节假日休市安排的通知》(2025-12-22)。
#   元旦 1/1(四)-1/3(六)，1/4(日)周末休市
#   春节 2/14(六)周末休市，2/15(日)-2/23(一)，2/28(六)周末休市
#   清明节 4/4(六)-4/6(一)
#   劳动节 5/1(五)-5/5(二)，5/9(六)周末休市
#   端午节 6/19(五)-6/21(日)
#   中秋节 9/25(五)-9/27(日)
#   国庆节 9/20(日)周末休市，10/1(四)-10/7(三)，10/10(六)周末休市
# R813 说明：交易所休市安排通常于前一年 11-12 月才公布，无法提前数年填表。
#   因此未覆盖年份一律走 _generic_closed() 保底推定，并由 --coverage-check
#   在年检窗口内提醒补表。否则 2027 年起节假日会被误判为交易日 → 看门狗在假期
#   反复强制重建并刷屏告警，最终引发「告警疲劳」而掩盖真实故障。
CLOSED_BY_YEAR = {
    2026: {
        "2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04",
        "2026-02-14", "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
        "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23", "2026-02-28",
        "2026-04-04", "2026-04-05", "2026-04-06",
        "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05", "2026-05-09",
        "2026-06-19", "2026-06-20", "2026-06-21",
        "2026-09-25", "2026-09-26", "2026-09-27",
        "2026-09-20", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
        "2026-10-06", "2026-10-07", "2026-10-10",
    },
}
# 若某年存在「周末补班开市」日，在此按年份显式放行（2026 为空）。
OPEN_EXCEPTION_BY_YEAR = {
    2026: set(),
}

# ── 未覆盖年份的保底推定 ──────────────────────────────────────────────────────
# 仅含「公历日期固定、年年必休」的法定节假日：元旦 1/1、劳动节 5/1、国庆 10/1-10/3。
# 春节/清明/端午/中秋为农历或浮动日期，无法可靠推定 —— 这些节日在缺表年份仍会误判，
# 故必须由 --coverage-check 年检提醒人工补表。
_FIXED_HOLIDAYS_MD = [("01", "01"), ("05", "01"), ("10", "01"), ("10", "02"), ("10", "03")]

# 年检窗口：11-12 月检查「次年」休市表是否已补（交易所通常此时公布下一年安排）。
CHECK_MONTHS = (11, 12)


def _generic_closed(year: int):
    """缺表年份的保底休市日（仅公历固定法定节假日）。"""
    return {"%04d-%s-%s" % (year, m, d) for m, d in _FIXED_HOLIDAYS_MD}


def _closed_for_year(year: int):
    return CLOSED_BY_YEAR.get(year)


def missing_calendar_years(as_of: dt.datetime):
    """返回缺精确休市表的年份清单：当前年 + （年检窗口内）次年。"""
    years = [as_of.year]
    if as_of.month in CHECK_MONTHS:
        years.append(as_of.year + 1)
    return [y for y in years if _closed_for_year(y) is None]


CLOSE_HOUR = 15  # 北京收盘 15:00


def _beijing_now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))


def is_trading_day(d: dt.date) -> bool:
    if d.weekday() >= 5:  # 5=Sat, 6=Sun
        return d.isoformat() in OPEN_EXCEPTION_BY_YEAR.get(d.year, set())
    tbl = _closed_for_year(d.year)
    if tbl is None:
        tbl = _generic_closed(d.year)   # R813：缺表年份走公历固定节假日保底推定
    return d.isoformat() not in tbl


def prev_trading_day(d: dt.date) -> dt.date:
    d = d - dt.timedelta(days=1)
    while not is_trading_day(d):
        d = d - dt.timedelta(days=1)
    return d


def expected_trading_day(as_of: dt.datetime) -> dt.date:
    """as_of 时刻「预期最后已收盘交易日」：当日已收盘则取当日，否则取前一交易日。"""
    if is_trading_day(as_of.date()) and as_of.hour >= CLOSE_HOUR:
        return as_of.date()
    return prev_trading_day(as_of.date())


def covers_day(fetched_at: dt.datetime) -> dt.date:
    """fetchedAt 构建时刻，数据所覆盖的交易日（语义同 data.js 的 `updated` 字段）。"""
    if is_trading_day(fetched_at.date()) and fetched_at.hour >= CLOSE_HOUR:
        return fetched_at.date()
    return prev_trading_day(fetched_at.date())


def parse_as_of(s: str) -> dt.datetime:
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            d = dt.datetime.strptime(s, fmt)
            if fmt == "%Y-%m-%d":
                d = d.replace(hour=0, minute=0)
            return d
        except ValueError:
            continue
    raise SystemExit("无法解析时间参数：%r（期望 'YYYY-MM-DD HH:MM'）" % s)


def main(argv):
    mode = "expected"
    as_of_str = None
    explain = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--as-of", "--covers"):
            i += 1
            as_of_str = argv[i]
            if a == "--covers":
                mode = "covers"
        elif a == "--coverage-check":
            mode = "coverage"
        elif a == "--explain":
            explain = True
        else:
            raise SystemExit("未知参数：%s" % a)
        i += 1

    as_of = parse_as_of(as_of_str) if as_of_str else _beijing_now()

    if mode == "coverage":
        # R813 年检：输出缺精确休市表的年份（逗号分隔；无缺失则空行），供 CI 提醒补表。
        print(",".join(str(y) for y in missing_calendar_years(as_of)))
        return

    if mode == "covers":
        result = covers_day(as_of)
    else:
        result = expected_trading_day(as_of)

    if explain:
        print("# mode=%s as_of=%s is_trading_day=%s" % (
            mode, as_of, is_trading_day(as_of.date())), file=sys.stderr)
        print("# result=%s" % result.isoformat(), file=sys.stderr)

    print(result.isoformat())


if __name__ == "__main__":
    main(sys.argv[1:])
