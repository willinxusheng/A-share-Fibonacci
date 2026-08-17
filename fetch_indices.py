# -*- coding: utf-8 -*-
"""跨指数原始数据拉取（R62）：补全沪深300/创业板指（及上证）日线 *_raw.md。

为何需要：build_data.py 的跨指数共振(建议5)依赖 data/sh000300_raw.md、data/sz399006_raw.md、
data/sh000001_raw.md 三份 westock 格式 markdown 日线表；每日管线会清理 *_raw.md，导致共振静默失效。
本脚本从 eastmoney push2 kline 接口拉取三指数全量日线，写成 read_kline_md 兼容的 markdown 表，
使共振每日可用。build_data.py 内已内置「缺失即自拉取」自修复(_ensure_raw)，本脚本用于手动/首次补全。

表头列名：date/open/high/low/last/volume（last=收盘列；read_kline_md 按列名映射，不依赖位置，故列序变化不会错位）。
仅用只读，不产生其它副作用。
"""
import json
import os
import sys
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))

# (展示名, 文件名, eastmoney secid)
INDICES = [
    ("上证指数", "sh000001_raw.md", "1.000001"),
    ("沪深300", "sh000300_raw.md", "1.000300"),
    ("创业板指", "sz399006_raw.md", "0.399006"),
]

_KLINE_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}"
    "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56"
    "&klt=101&fqt=0&beg=20210805&end=20991231"
)

# eastmoney 近期拒绝仅带简单 User-Agent 的 urllib 请求(RemoteDisconnected)，
# 需补全浏览器级请求头(Referer/Accept/identity 编码)方可正常返回。
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "identity",
    "Referer": "https://quote.eastmoney.com/",
    "Connection": "close",
}


def fetch_one(name, fn, secid):
    """拉取单指数日线并写 data/<fn>。成功返回 True，任何异常返回 False。"""
    path = os.path.join(BASE, "data", fn)
    try:
        url = _KLINE_URL.format(secid=secid)
        req = urllib.request.Request(url, headers=_HEADERS)
        raw = urllib.request.urlopen(req, timeout=25).read().decode("utf-8")
        d = json.loads(raw)
        kl = (d.get("data") or {}).get("klines") or []
        if not kl:
            print("  [skip] %s 无数据(接口返回空)" % name)
            return False
        # eastmoney kline 字段顺序: f51日期,f52开,f53收,f54高,f55低,f56量
        lines = ["| date | open | high | low | last | volume |",
                 "| --- | --- | --- | --- | --- | --- |"]
        for row in kl:
            f = row.split(",")
            if len(f) < 6:
                continue
            # 表头按列名映射：date/open/high/low/last(收盘)/volume
            lines.append("| %s | %s | %s | %s | %s | %s |"
                         % (f[0], f[1], f[3], f[4], f[2], f[5]))
        with open(path, "w", encoding="utf-8") as fp:
            fp.write("\n".join(lines) + "\n")
        print("  [ok] %s -> %s (%d 根K线, %s~%s)"
              % (name, fn, len(kl), kl[0].split(",")[0], kl[-1].split(",")[0]))
        return True
    except Exception as e:  # 网络/格式异常 -> 优雅降级，不影响其余
        print("  [fail] %s 拉取失败: %s" % (name, e))
        return False


def main():
    print("== 拉取跨指数日线原始数据 ==")
    ok = 0
    main_ok = False  # 仅主指数(上证 sh000001)的成败决定整体退出码
    for name, fn, secid in INDICES:
        if fetch_one(name, fn, secid):
            ok += 1
            if fn == "sh000001_raw.md":
                main_ok = True
    print("完成：%d/%d 成功。" % (ok, len(INDICES)))
    # 仅主指数致命失败才非零退出；副指数失败不阻断更新——
    # 副指数共振 breadth 将走→0 兜底，由 preflight 以告警提示（不致命）。
    # 此前 (ok==len) 的判据会把副指数偶发网络抖动误判为整体失败，
    # 导致 CI 直接停止 job、连主指数正常的新数据都不更新。
    sys.exit(0 if main_ok else 1)


if __name__ == "__main__":
    main()
