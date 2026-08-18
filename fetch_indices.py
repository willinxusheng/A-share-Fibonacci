# -*- coding: utf-8 -*-
"""跨指数原始数据拉取（R62/R271）：补全沪深300/创业板指（及上证）日线 *_raw.md。

R271：改用 datafeed 多源链式取数（eastmoney 中国主源 -> yahoo/stooq 海外可达回退），
使本脚本在 GitHub 海外 runner 上也能取到数据，支撑云端每日自动更新。
输出格式与 read_kline_md 兼容：| date | open | high | low | last | volume |
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import datafeed

# (展示名, 文件名)；取数 key = 文件名去掉 "_raw.md" 后缀
# 必须与 preflight.py 的 CRITICAL_FILES + SECONDARY_FILES 以及 build_data._idx_raw /
# crossMarket 取的索引【完全一致】。否则每日 CI 编排（daily.yml: fetch_indices→preflight→
# analyze→build）里 preflight 在 build 之前(step2)检查不到这些副指数文件、副指数守门员永久
# 失灵（本该拦坏数据却永远看不到文件），且每次 CI 误报"文件缺失"告警（假绿/噪音）。
# R271 多源回退链(datafeed)对所有指数生效，海外 runner 也能取到，故全部纳入本步预取。
INDICES = [
    ("上证指数", "sh000001_raw.md"),   # 主指数(关键)：缺失即全盘作废
    ("沪深300", "sh000300_raw.md"),
    ("创业板指", "sz399006_raw.md"),
    ("上证50", "sh000016_raw.md"),
    ("中证500", "sh000905_raw.md"),
    ("科创50", "sh000688_raw.md"),
    ("恒生指数", "hkHSI_raw.md"),
    ("恒生科技", "hkHSTECH_raw.md"),
    ("标普500", "usINX_raw.md"),
    ("纳斯达克", "usIXIC_raw.md"),
]


def fetch_one(name, fn):
    """拉取单指数日线并写 data/<fn>。成功返回 True，全源失败返回 False。"""
    path = os.path.join(BASE, "data", fn)
    key = fn[:-7] if fn.endswith("_raw.md") else fn
    try:
        ok = datafeed.fetch_and_write(key, path)
    except Exception as e:  # noqa: BLE001
        print("  [fail] %s 取数异常: %s" % (name, e))
        return False
    if not ok:
        print("  [fail] %s 全源取数失败(eastmoney/yahoo/stooq 均不可用)" % name)
        return False
    # 仅用于日志：统计行数与首末日期
    try:
        with open(path, encoding="utf-8") as fp:
            lines = [l.strip() for l in fp
                     if l.strip().startswith("|") and "---" not in l]
        n = len(lines) - 1
        if n > 0:
            first = lines[1].split("|")[1].strip()
            last = lines[-1].split("|")[1].strip()
            print("  [ok] %s -> %s (%d 根K线, %s~%s)" % (name, fn, n, first, last))
        else:
            print("  [ok] %s -> %s (空)" % (name, fn))
    except Exception:
        print("  [ok] %s -> %s" % (name, fn))
    return True


def main():
    print("== 拉取跨指数日线原始数据（多源回退）==")
    ok = 0
    main_ok = False  # 仅主指数(上证 sh000001)的成败决定整体退出码
    for name, fn in INDICES:
        if fetch_one(name, fn):
            ok += 1
            if fn == "sh000001_raw.md":
                main_ok = True
    print("完成：%d/%d 成功。" % (ok, len(INDICES)))
    # 仅主指数致命失败才非零退出；副指数失败不阻断更新（共振 breadth 走 0 兜底）。
    sys.exit(0 if main_ok else 1)


if __name__ == "__main__":
    main()
