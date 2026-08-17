# -*- coding: utf-8 -*-
"""深度校验脚本：实证找 bug（不靠肉眼）。

R117 修复：脚本此前只 print("  !! ...") 报告违例，却从不 sys.exit(1)，
自然结束时 EXIT 恒为 0——等于守门链第一道闸形同虚设（R116 自动化靠
EXIT≠0 中止部署，故 validate 失效会让坏构建被直接部署）。现改为累计
failures，任一违例即以 Exit=1 退出；并新增第 9 项“数据新鲜度”闸口。
"""
import json, math, re, os, sys, datetime

BASE = os.path.dirname(os.path.abspath(__file__))

# ---- 1) 加载 data.js ----
js = open(os.path.join(BASE, "data", "data.js"), encoding="utf-8").read()
m = re.search(r"window\.FIB_DATA\s*=\s*(\{.*\})\s*;?\s*$", js, re.S)
body = m.group(1)
# JSON 默认允许 NaN/Infinity，loads 能解析
data = json.loads(body)

problems = []
failures = 0  # 累计不变量违例；>0 时脚本以 Exit=1 退出，使自动化(R116)部署闸口真正生效

def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk(v, path + "." + str(k))
    elif isinstance(obj, list):
        for i, v in obj.items() if isinstance(obj, dict) else enumerate(obj):
            walk(v, path + "[%s]" % i)
    else:
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            problems.append("NaN/Infinity @ %s = %r" % (path, obj))

walk(data, "FIB_DATA")
print("== 1) NaN/Infinity 扫描 ==", "无问题" if not problems else "")
for p in problems:
    print("  !!", p)
failures += len(problems)

# ---- 2) K线 OHLC 不变量 ----
print("\n== 2) K线 OHLC 不变量 (low<=min(o,c), high>=max(o,c)) ==")
bad = 0
for i, ohlc in enumerate(data["kline"]["ohlc"]):
    o, c, l, h = ohlc
    if l > min(o, c) + 1e-6 or h < max(o, c) - 1e-6:
        bad += 1
        if bad <= 5:
            print("  !! idx", i, "date", data["kline"]["dates"][i], "ohlc", ohlc)
print("  违例条数:", bad)
failures += bad

# ---- 3) targets 与 sellTargets 一致性 ----
print("\n== 3) targets vs tradePlan.sellTargets 价格一致性 ==")
tgt_prices = sorted([t["price"] for t in data["targets"]])
sell_prices = sorted([s["price"] for s in data["tradePlan"]["sellTargets"]])
print("  targets:", tgt_prices)
print("  sell   :", sell_prices)
# 卖①~③ 应出现在 targets 中
for s in data["tradePlan"]["sellTargets"]:
    if s["price"] not in tgt_prices:
        print("  !! 卖目标", s["name"], s["price"], "不在 targets 表")
        failures += 1

# ---- 4) distances 一致性（从框架单一真值反推，避免硬编码字面漂移误判）----
print("\n== 4) distances 公式复核（参照 build 同源价格）==")
lc = data["lastClose"]
# 参照价全部从 data.js 自身 tradePlan/wavePoints 派生（与 build_data.py 单一真值同源），
# 不再写死 3674.40/4493.94/... 字面：浪型重校订改价时本检查自动跟随，真正校验 build 内部
# 算术一致性（旧式会与陈旧硬编码比对，重校订时误报或漏报回归）。wavePoints[8]=浪③顶、[-1]=浪④低。
_sl_p = data["tradePlan"]["stopLine"]["price"]
_sell = [s["price"] for s in data["tradePlan"]["sellTargets"]]
# R129 加固：原用硬编码索引 wavePoints[8]/[-1] 取浪③顶/浪④低。一旦人工在 wavePoints
# 中间插入/删除标注点，位置索引错位会让下方 distances 公式用错位价格算 exp 再比 got——
# 反而一致通过，check #4 彻底失效（正是本检查自身注释要防的"硬编码双份真值漂移"）。
# 改用 startswith 匹配"浪3"/"浪4"标注（当前标为"浪3"=浪③顶、"浪4?"=浪④低），真正
# 校验 build 内部算术，并在缺失时显式报错而非静默错算。
def _find_wp(kw):
    for _p in data["wavePoints"]:
        if _p["label"].startswith(kw):
            return _p.get("price")
    return None
_w3_hi = _find_wp("浪3")   # 浪③顶
_w4_low = _find_wp("浪4")  # 浪④低
if _w3_hi is None or _w4_low is None:
    print("  !! 未找到 浪3/浪4 标注点，distances 无法校验")
    failures += 1
checks = {
    "toKeyLine": (lc - _sl_p) / lc * 100,
    "toTargetLow": (_sell[0] - lc) / lc * 100,
    "toTargetHigh": (_sell[-1] - lc) / lc * 100,
    "toPrevHigh": (_w3_hi - lc) / lc * 100,
    "fromW4Low": (lc - _w4_low) / _w4_low * 100,
}
for k, exp in checks.items():
    got = data["distances"][k]
    ok = abs(round(exp, 2) - got) < 0.01
    if not ok:
        print("  !! %s 期望 %.2f 实际 %s" % (k, exp, got))
        failures += 1
    else:
        print("  ok %s = %.2f" % (k, got))

# ---- 5) 时间窗：基准日是否真实存在于交易日历 ----
print("\n== 5) 时间窗基准日存在性（从 data 派生，避免硬编码）==")
dates = set(data["kline"]["dates"])
for base in [data.get("tzBaseStart"), data.get("tzBaseTop")]:
    if base:
        print("  ", base, "在日历中:" , base in dates)

# ---- 6) 多指数归一化数据完整性 ----
print("\n== 6) indexCompare 完整性 ==")
for s in data["indexCompare"]:
    print("  ", s["name"], "点数:", len(s["data"]), "首点:", s["data"][0] if s["data"] else None)

# ---- 7) 浪型标注点日期是否在日历内 ----
print("\n== 7) wavePoints 日期存在性 ==")
for p in data["wavePoints"] + data["subWavePoints"]:
    if p["date"] not in dates:
        print("  !! 标注点日期不在日历:", p)
        failures += 1

# ---- 8) 买卖框架关键位 ----
print("\n== 8) tradePlan 关键位（从 data 派生，避免硬编码）==")
tp = data["tradePlan"]
print("  买区:", tp["buyZones"][0]["lo"], "~", tp["buyZones"][0]["hi"])
print("  风控:", tp["stopLine"]["price"])
print("  卖①~③:", [s["price"] for s in tp["sellTargets"]])

# 卖点价格严格单调递增（R64 锚点 bug 类回归守卫：浪⑤目标须自浪④低起算，
# 若误锚到浪②底会把卖②压到卖③附近、破坏单调梯度，此检查即刻暴露）。
_sell_sorted = [s["price"] for s in tp["sellTargets"]]
_mono = all(_sell_sorted[i] < _sell_sorted[i + 1] for i in range(len(_sell_sorted) - 1))
print("  卖点价格单调递增:", "ok" if _mono else "!! 违反(锚点可能错)")
if not _mono:
    failures += 1
    print("    ", _sell_sorted)

# 子浪ⅴ ≡ 卖① 几何自洽（审计49 锁 expDays；卖点无 date 字段，故比 价+expDays 防两路漂移）。
_sp = {p["label"]: p for p in data["subForecast"]["points"]}
if "子浪ⅴ" in _sp and tp["sellTargets"]:
    _v5, _s1 = _sp["子浪ⅴ"], tp["sellTargets"][0]
    _eq = (_v5["price"] == _s1["price"]) and (_v5.get("expDays") == _s1.get("expDays"))
    print("  子浪ⅴ≡卖①(价/expDays):", "ok" if _eq else "!! 不一致")
    if not _eq:
        failures += 1
        print("    子浪ⅴ %.2f/%s  vs 卖① %.2f/%s" % (_v5["price"], _v5.get("expDays"), _s1["price"], _s1.get("expDays")))

# ---- 9) 数据新鲜度（R117 新增：守门链此前只查内部不变量，从不查数据是否新鲜；
#        陈旧/空数据会被无新鲜度校验地部署。自动化(R116)靠 EXIT≠0 中止部署，故本检查
#        计入 failures 并以 Exit=1 退出）
print("\n== 9) 数据新鲜度（末根K线距今天数，闸门：>30天 或 未来日期 即失败）==")
_today = datetime.date.today()
_last = None
try:
    if data["kline"]["dates"]:
        _last = datetime.date.fromisoformat(str(data["kline"]["dates"][-1]))
except Exception:
    _last = None
if _last is None:
    print("  !! 无法解析末根K线日期")
    failures += 1
else:
    _gap = (_today - _last).days
    print("  末根K线:", _last, "| 今日:", _today, "| 间隔:", _gap, "天")
    if _gap < 0:
        print("  !! 数据含未来日期(疑似伪造/时区错)，阻断部署")
        failures += 1
    elif _gap > 30:
        print("  !! 数据陈旧 %d 天(>30)，疑似取数失败留下旧数据，阻断部署" % _gap)
        failures += 1
    else:
        print("  ok 数据新鲜度正常")

# ---- 10) data.js 末根日期 必须与 源CSV末根日期一致（R119 新增：堵 CSV↔data.js 过期盲区）----
# 仅查 内部不变量 + 末根距今天数 不足以发现"CSV 已追加新行、但 data.js 未重生"的脱节——
# 此时 audit50 会因从新 CSV 重算波动率/漂移结构而 FAIL（子浪ⅲ 偏差越过 0.6 容差）。
# 本检查在最早的闸口就比对两者末根日期，使过期构建在未部署前即被拦下。
print("\n== 10) data.js 末根日期 ≡ 源CSV(sh000001.csv)末根日期 ==")
_csv_path = os.path.join(BASE, "data", "sh000001.csv")
_csv_last = None
if os.path.exists(_csv_path):
    try:
        with open(_csv_path, encoding="utf-8") as _fh:
            _rows = [r for r in _fh.read().splitlines() if r.strip()]
        if len(_rows) >= 2:
            _csv_last = _rows[-1].split(",")[0].strip()
    except Exception as _e:
        _csv_last = None
        print("  !! 读取 CSV 失败:", _e)
if _csv_last is None:
    # CSV 缺失（如仅部署 data.js 的上下文）：无法比对，跳过但不算失败（避免误拦部署）。
    print("  (跳过) 源CSV不可用，无法比对末根日期")
else:
    if _last is None:
        print("  !! data.js 末根日期无法解析，无法比对")
        failures += 1
    else:
        _match = (str(_last) == str(_csv_last))
        print("  data.js 末根:", _last, "| CSV 末根:", _csv_last, "|", "ok 一致" if _match else "!! 不一致(CSV已更新但未重生data.js)")
        if not _match:
            failures += 1

# ---- 11) 盘中快照当收盘拦截（R142 新增：根治"盘中抓数未重跑 step1 即盘后构建发布"数据事故）----
# 上次事故根因：13:20 盘中抓数留下 CSV，17:20 盘后构建直接用了未刷新的盘中快照当收盘价发布。
# 本检查读 data.fetchedAt（=源 CSV 落盘时刻）：当末根K线==今日时，抓取日必须也==今日且
# 不得落在盘中交易时段[09:30,15:00)；否则视为"盘中快照当收盘"，阻断部署。每日 16:30 自动
# 化重写 CSV(mtime 盘后) 自然通过；手动盘中跑会被拦下，强制先重跑 step1 取真实收盘。
print("\n== 11) 数据抓取时刻检查（末根=今日时，禁止盘中[09:30,15:00)快照当收盘发布）==")
_fa_str = data.get("fetchedAt")
_block = False
if _fa_str and _last is not None:
    try:
        _fa = datetime.datetime.strptime(_fa_str, "%Y-%m-%d %H:%M:%S")
        # R273：fetchedAt 语义为北京时间(UTC+8)，与 runner 本地时区解耦。
        _fa = _fa.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
        if str(_last) == _today.strftime("%Y-%m-%d"):
            if _fa.date().strftime("%Y-%m-%d") != str(_last):
                print("  !! 抓取日(%s)与末根日(%s)不一致：疑似跨天陈旧 CSV 当新数据" % (_fa.date(), _last))
                _block = True
            else:
                _tmin = _fa.hour * 60 + _fa.minute
                if 570 <= _tmin < 900:  # 09:30=570分, 15:00=900分
                    print("  !! 抓取时刻 %s 落在盘中交易时段[09:30,15:00)，禁止将盘中快照当收盘价发布" % _fa_str)
                    _block = True
                else:
                    print("  ok 抓取时刻 %s 为收盘后/盘前，允许发布" % _fa_str)
    except Exception as _e:
        print("  !! fetchedAt 解析失败:", _e)
        _block = True
else:
    print("  (跳过) fetchedAt 缺失或末根日期无法解析")
if _block:
    failures += 1

print("\n全部实证检查完成。" if failures == 0 else "\n!! 共发现 %d 处问题，脚本将以 Exit=1 退出（阻断自动化部署）。" % failures)
sys.exit(1 if failures else 0)
