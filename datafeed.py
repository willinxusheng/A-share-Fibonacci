# -*- coding: utf-8 -*-
"""多源回退 K 线取数（R271 海外可达改造）。

背景：GitHub Actions 海外 runner 无法访问 eastmoney（取数失败），导致云端每日自动更新
整轮失败。本模块提供「eastmoney(中国本地主源) -> tencent(gtimg，海外可达，A 股返回完整
历史) -> yahoo(美国本土服务，海外可达) -> stooq(次回退)」的链式取数，归一化为与
read_kline_md 兼容的 6 列 markdown 表：

    | date | open | high | low | last | volume |

- eastmoney：仅中国网络可达，本地/Mac 走此路径（与历史行为完全一致）。
- yahoo：美国本土服务，从 GitHub 海外 runner 访问正常；是云端更新的关键回退。
- stooq：补充回退，部分网络会被反爬挑战，解析失败即跳过（绝不崩溃）。

纯标准库实现（urllib/http.cookiejar/csv/json/datetime），不引入第三方依赖，
可直接在 CI runner 与本地 venv 运行。
"""
import csv
import datetime
import http.cookiejar
import io
import json
import os
import sys
import time
import urllib.request

# 允许以脚本或模块方式运行，确保同目录可 import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# CI 自动输出详细诊断：GitHub Actions 默认设置 CI=true
_CI_DEBUG = os.environ.get("CI") == "true" or os.environ.get("DATAFEED_DEBUG") in ("1", "true", "yes")

# 取数完整性门槛：与 analyze.py 的 `len(rows) < 600` 硬闸一致。任一源回传行数低于此值
# 视为「退化/残破」（如海外腾讯 gtimg 对副指数偶发只回 1 根），不入链终止、继续回退到
# 下一源（yahoo/stooq 对 A 股副指数常含完整历史）。R283 修复：原 `if rows: return rows`
# 在拿到任意非空（但可能极短）结果即返回，导致副指数海外只取到 1 行、共振 breadth 退化
# （production_oos_brier cnt=0、indexCompare 副指数仅 1 点）。现要求「完整」才接受。
_MIN_ROWS = 600


def _debug(fmt, *args):
    if _CI_DEBUG:
        sys.stderr.write(("[datafeed] " + fmt) % args + "\n")

_EASTMONEY_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "identity",
    "Referer": "https://quote.eastmoney.com/",
    "Connection": "close",
}

# internal_key -> (eastmoney_secid, yahoo_symbol, stooq_symbol, tencent_secid)
# tencent_secid：腾讯 gtimg 代码（A股=sh/sz+6位；港股=hk+代码如 hkHSI/hkHSTECH），海外 runner 可达。
# 实测：hkHSI/hkHSTECH 经腾讯返回完整日K（约1300根至当日），故启用为港股主回退；
# 美股(usINX/usIXIC)腾讯仅回 1 日快照(<_MIN_ROWS)，不启用，仍走 yahoo（stooq 已失效）。
SYMBOLS = {
    "sh000001": ("1.000001", "000001.SS", "000001.ss", "sh000001"),
    "sh000300": ("1.000300", "000300.SS", "000300.ss", "sh000300"),
    "sz399006": ("0.399006", "399006.SZ", "399006.sz", "sz399006"),
    "sh000016": ("1.000016", "000016.SS", "000016.ss", "sh000016"),
    "sh000905": ("1.000905", "000905.SS", "000905.ss", "sh000905"),
    "sh000688": ("1.000688", "000688.SS", "000688.ss", "sh000688"),
    "hkHSI":    ("100.HSI", "^HSI", "hsi", "hkHSI"),
    "hkHSTECH": ("100.HSTECH", "^HSTECH", "hstech", "hkHSTECH"),
    "usINX":    ("100.SPX", "^GSPC", "spx", None),
    # 三源必须为同一指数：yahoo/stooq 的 ^IXIC/ixic = 纳斯达克综合指数(Nasdaq Composite)，
    # eastmoney 对应代码为 100.IXIC（100.NDX 是纳斯达克100，非同一标的，跨源回退会取错序列）。
    "usIXIC":   ("100.IXIC", "^IXIC", "ixic", None),
}


def _http_get(url, headers=None, timeout=25, retries=2, tag=None):
    """带 cookie jar 与 UA 的 GET；失败/限流重试；最终失败返回 None（不抛）。"""
    tag = tag or url[:60]
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    hdrs = {"User-Agent": UA, "Accept-Encoding": "identity", "Connection": "close"}
    if headers:
        hdrs.update(headers)
    for attempt in range(retries + 1):
        try:
            _debug("GET %s (attempt %d/%d, timeout=%ds)", tag, attempt + 1, retries + 1, timeout)
            req = urllib.request.Request(url, headers=hdrs)
            with opener.open(req, timeout=timeout) as r:
                code = r.getcode()
                data = r.read()
                text = data.decode("utf-8", "replace")
                _debug("GET %s -> HTTP %d, %d bytes", tag, code, len(data))
                return text
        except Exception as e:  # noqa: BLE001 - 网络层任意异常均降级
            _debug("GET %s attempt %d failed: %s", tag, attempt + 1, e)
            if attempt < retries:
                time.sleep(2.0)
    _debug("GET %s all attempts exhausted -> None", tag)
    return None


def _fmt_price(x):
    try:
        return "%.2f" % float(x)
    except (ValueError, TypeError):
        return "0.00"


def _fmt_vol(x):
    try:
        return str(int(round(float(x))))
    except (ValueError, TypeError):
        return "0"


def _eastmoney_rows(secid):
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
           "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56"
           "&klt=101&fqt=0&beg=20210805&end=20991231") % secid
    _debug("trying eastmoney secid=%s", secid)
    raw = _http_get(url, _EASTMONEY_HEADERS, timeout=10, retries=1, tag="eastmoney")
    if not raw:
        _debug("eastmoney secid=%s -> no response", secid)
        return None
    try:
        d = json.loads(raw)
        kl = (d.get("data") or {}).get("klines") or []
        if not kl:
            _debug("eastmoney secid=%s -> empty klines", secid)
            return None
        rows = []
        for row in kl:
            f = row.split(",")
            if len(f) < 6:
                continue
            # eastmoney: f51日期,f52开,f53收,f54高,f55低,f56量
            rows.append((f[0], f[1], f[3], f[4], f[2], f[5]))
        _debug("eastmoney secid=%s -> %d rows", secid, len(rows))
        return rows if rows else None
    except Exception as e:  # noqa: BLE001
        _debug("eastmoney secid=%s parse error: %s", secid, e)
        return None


def _tencent_rows(secid):
    """腾讯 gtimg 日K（R279 海外可达回退）：对 A 股指数/个股返回完整日K（约 1300 根，2021 起）。
    海外 runner 可达，弥补 yahoo/stooq 不含 A 股副指数代码的缺口，使副指数共振在 CI 复活。
    返回格式 [date, open, close, high, low, volume] —— 注意 close 在第 3 位（eastmoney 在第 5 位），
    须映射为 (date, open, high, low, close, volume) 以契合 read_kline_md 约定。"""
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,1300,qfq") % secid
    _debug("trying tencent secid=%s", secid)
    raw = _http_get(url, {"User-Agent": UA, "Referer": "https://gu.qq.com/"},
                   timeout=15, retries=1, tag="tencent")
    if not raw:
        _debug("tencent secid=%s -> no response", secid)
        return None
    try:
        d = json.loads(raw)
        node = (d.get("data") or {}).get(secid) or {}
        # qfq 请求一般落在 "day"；个别情形为 "qfqday"，二者兼容
        kl = node.get("day") or node.get("qfqday") or []
        if not kl:
            _debug("tencent secid=%s -> empty klines", secid)
            return None
        rows = []
        for f in kl:
            if len(f) < 6:
                continue
            # gtimg: [date, open, close, high, low, volume]
            rows.append((f[0], f[1], f[3], f[4], f[2], f[5]))
        _debug("tencent secid=%s -> %d rows", secid, len(rows))
        return rows if rows else None
    except Exception as e:  # noqa: BLE001
        _debug("tencent secid=%s parse error: %s", secid, e)
        return None


def _yahoo_rows(symbol):
    # 先取 fc.yahoo.com cookie，降低 sad-panda 概率
    _debug("trying yahoo symbol=%s", symbol)
    _http_get("https://fc.yahoo.com", timeout=10, retries=0, tag="yahoo-cookie")
    for host in ("query1", "query2"):
        url = ("https://%s.finance.yahoo.com/v8/finance/chart/%s"
               "?interval=1d&range=5y") % (host, symbol)
        _debug("trying yahoo host=%s symbol=%s", host, symbol)
        raw = _http_get(url, timeout=15, retries=1, tag="yahoo-chart")
        if not raw:
            continue
        try:
            d = json.loads(raw)
            _err = (d.get("chart") or {}).get("error")
            if _err:
                _debug("yahoo %s error: %s", symbol, _err)
                continue
            res = (d.get("chart") or {}).get("result")
            if not res:
                _debug("yahoo %s -> empty chart result", symbol)
                continue
            r = res[0]
            ts = r.get("timestamp") or []
            q = (r.get("indicators") or {}).get("quote") or [{}]
            q0 = q[0]
            o = q0.get("open") or []
            h = q0.get("high") or []
            lo = q0.get("low") or []
            c = q0.get("close") or []
            v = q0.get("volume") or []
            rows = []
            for i, t in enumerate(ts):
                if i >= len(c) or c[i] is None:
                    continue
                try:
                    dt = datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
                except Exception:
                    continue
                cv = c[i]
                ov = o[i] if (i < len(o) and o[i] is not None) else cv
                hv = h[i] if (i < len(h) and h[i] is not None) else cv
                lv = lo[i] if (i < len(lo) and lo[i] is not None) else cv
                vv = v[i] if (i < len(v) and v is not None and v[i] is not None) else 0
                rows.append((dt, _fmt_price(ov), _fmt_price(hv),
                             _fmt_price(lv), _fmt_price(cv), _fmt_vol(vv)))
            _debug("yahoo %s -> %d rows", symbol, len(rows))
            return rows if rows else None
        except Exception as e:  # noqa: BLE001
            _debug("yahoo %s parse error: %s", symbol, e)
            continue
    _debug("yahoo symbol=%s -> all hosts failed", symbol)
    return None


def _stooq_rows(symbol):
    url = "https://stooq.com/q/d/l/?s=%s&i=d" % symbol
    _debug("trying stooq symbol=%s", symbol)
    raw = _http_get(url, timeout=15, retries=0, tag="stooq")
    if not raw:
        _debug("stooq symbol=%s -> no response", symbol)
        return None
    if not raw.lstrip().lower().startswith("date,"):
        # 反爬挑战页或非 CSV -> 视为失败
        _debug("stooq symbol=%s -> not a CSV (len=%d head=%.80s)", symbol, len(raw), raw.lstrip())
        return None
    try:
        rd = csv.reader(io.StringIO(raw))
        header = next(rd)
        idx = {h.lower(): i for i, h in enumerate(header)}
        di = idx.get("date")
        oi = idx.get("open")
        hi = idx.get("high")
        li = idx.get("low")
        ci = idx.get("close")
        vi = idx.get("volume")
        if di is None or ci is None:
            _debug("stooq symbol=%s -> missing date/close column", symbol)
            return None
        rows = []
        for parts in rd:
            if len(parts) <= max(di, ci):
                continue
            try:
                dt = parts[di]
                c = parts[ci]
                o = parts[oi] if (oi is not None and oi < len(parts)) else c
                hh = parts[hi] if (hi is not None and hi < len(parts)) else c
                ll = parts[li] if (li is not None and li < len(parts)) else c
                v = parts[vi] if (vi is not None and vi < len(parts)) else "0"
                rows.append((dt, _fmt_price(o), _fmt_price(hh),
                             _fmt_price(ll), _fmt_price(c), _fmt_vol(v)))
            except Exception:  # noqa: BLE001
                continue
        _debug("stooq symbol=%s -> %d rows", symbol, len(rows))
        return rows if rows else None
    except Exception as e:  # noqa: BLE001
        _debug("stooq symbol=%s parse error: %s", symbol, e)
        return None


def fetch_rows(key):
    """链式取数：eastmoney -> tencent -> yahoo -> stooq。返回 [(date,open,high,low,close,volume)] 或 None。

    R283 修复：任一源回传行数 < _MIN_ROWS 视为退化残破结果，不立即返回，继续回退到下一源，
    使副指数海外（eastmoney 失效、腾讯偶发只回 1 行）能继续尝试 yahoo/stooq 拿完整历史，
    复活副指数共振。仅当某源返回「完整」(>=_MIN_ROWS) 结果才沿线接受（保主源优先级）；
    全部源均不足门槛则返回 None（副指数非致命、主指数将触发上层失败）。
    """
    if key not in SYMBOLS:
        _debug("fetch_rows unknown key=%s", key)
        return None
    em, yh, st, tnt = SYMBOLS[key]
    rows = _eastmoney_rows(em)
    if rows and len(rows) >= _MIN_ROWS:
        return rows
    if tnt:
        rows = _tencent_rows(tnt)
        if rows and len(rows) >= _MIN_ROWS:
            return rows
    rows = _yahoo_rows(yh)
    if rows and len(rows) >= _MIN_ROWS:
        return rows
    rows = _stooq_rows(st)
    if rows and len(rows) >= _MIN_ROWS:
        return rows
    _debug("fetch_rows key=%s -> all sources failed or all below _MIN_ROWS(%d)", key, _MIN_ROWS)
    return None


def write_raw_md(path, rows):
    """写出与 fetch_indices/_ensure_raw 完全一致的 6 列 markdown 表（last=收盘列）。"""
    lines = ["| date | open | high | low | last | volume |",
             "| --- | --- | --- | --- | --- | --- |"]
    for (d, o, h, l, c, v) in rows:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (d, o, h, l, c, v))
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")
    return True


def fetch_and_write(key, path):
    """取数并写 raw.md；成功返回 True，全源失败返回 False。"""
    rows = fetch_rows(key)
    if not rows:
        return False
    write_raw_md(path, rows)
    return True


if __name__ == "__main__":
    # 简单自检：打印各源首末行（调试用）
    for k in ("sh000001", "sh000300", "sz399006",
              "sh000016", "sh000905", "sh000688",
              "hkHSI", "hkHSTECH", "usINX", "usIXIC"):
        r = fetch_rows(k)
        if r:
            print("%s OK rows=%d %s~%s" % (k, len(r), r[0][0], r[-1][0]))
        else:
            print("%s FAIL (all sources)" % k)
