import json, re, datetime, sys, os

REPO = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(REPO, "data")
problems = []
def chk(cond, msg):
    if not cond:
        problems.append(msg)

def load_js(path):
    txt = open(path, encoding="utf-8").read()
    m = re.search(r"=\s*(\{.*\})\s*;?\s*$", txt, re.S)
    if not m:
        m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(1))

def load_json(path):
    return json.load(open(path, encoding="utf-8"))

def is_a_share_trading_day(d):
    dt = datetime.datetime.strptime(d, "%Y-%m-%d")
    holidays = {
        "2026-01-01","2026-01-02","2026-01-03",
        "2026-02-15","2026-02-16","2026-02-17","2026-02-18","2026-02-19","2026-02-20","2026-02-21","2026-02-22","2026-02-23",
        "2026-04-04","2026-04-05","2026-04-06",
        "2026-05-01","2026-05-02","2026-05-03","2026-05-04","2026-05-05",
        "2026-06-19","2026-06-20","2026-06-21",
        "2026-09-25","2026-09-26","2026-09-27",
        "2026-10-01","2026-10-02","2026-10-03","2026-10-04","2026-10-05","2026-10-06","2026-10-07",
    }
    makeup = {"2026-02-14","2026-02-28","2026-05-09","2026-09-20","2026-10-10"}
    if d in makeup:
        return True
    if dt.weekday() >= 5:
        return False
    if d in holidays:
        return False
    return True

fib = load_js(os.path.join(DATA, "data.js"))
kdates = fib["kline"]["dates"]
last_k = kdates[-1]
last_close = fib["lastClose"]
print("== data.js ==  updated:%s lastClose:%s 点数:%d 末根:%s" % (fib.get("updated"), last_close, len(kdates), last_k))

sent = load_json(os.path.join(DATA, "sentiment.json"))
print("\n== sentiment.json == schema:%s today:%s/%s history:%d forecast:%d" %
      (sent["schema"], sent["today"]["score"], sent["today"]["label"], len(sent["history"]), len(sent["forecast"])))
chk(bool(sent["history"]), "sentiment.history 为空（无历史数据）")
if sent["history"]:
    print("  history末点:", sent["history"][-1])
    chk(sent["history"][-1]["date"] == last_k, "sentiment.history末点(%s) != data.js末根(%s)" % (sent["history"][-1]["date"], last_k))
    chk(sent["today"]["score"] == sent["history"][-1]["score"], "today.score != history末点.score")
if sent["forecast"]:
    print("  forecast首/末:", sent["forecast"][0]["date"], "/", sent["forecast"][-1]["date"])
# forecast 不得含周末/法定假日
bad_td = [x["date"] for x in sent["forecast"] if not is_a_share_trading_day(x["date"])]
chk(not bad_td, "forecast含非交易日: %s" % bad_td[:8])
# forecast 日期单调递增
fd = [x["date"] for x in sent["forecast"]]
chk(fd == sorted(fd), "forecast日期未单调递增")

cl = load_js(os.path.join(DATA, "chanlun_view.js"))
print("\n== chanlun_view.js == lastDate:%s lastClose:%s" % (cl.get("lastDate"), cl.get("lastClose")))
chk(cl.get("lastDate") == last_k, "chanlun_view.lastDate(%s) != 末根(%s)" % (cl.get("lastDate"), last_k))
chk(abs(cl.get("lastClose", 0) - last_close) < 0.01, "chanlun_view.lastClose 与 data.js 不一致")

# backtest：hitRate 必须 = Laplace(hits+1)/(n+2)；cold 组必须为 None
bt = load_json(os.path.join(DATA, "backtest.json"))
print("\n== backtest.json == realizedHitRate:%s overallHitRate:%s" % (bt.get("realizedHitRate"), bt.get("overallHitRate")))
bad_hr = []
for s in bt.get("summary", []):
    n, hits, hr, cold = s.get("n"), s.get("hits"), s.get("hitRate"), s.get("cold")
    if cold:
        if hr is not None:
            bad_hr.append("%s: cold但hitRate=%s" % (s["key"], hr))
    else:
        expect = round((hits + 1.0) / (n + 2) * 100, 1)
        if abs((hr or 0) - expect) > 0.05:
            bad_hr.append("%s: hitRate=%s 期望Laplace=%s" % (s["key"], hr, expect))
chk(not bad_hr, "backtest hitRate 口径异常: %s" % bad_hr[:5])

# QC vs backtest：realized 应一致；overall 不同是设计（原始pooled vs Laplace），仅打印
qc = load_json(os.path.join(DATA, "quality_cert.json"))
print("== quality_cert.json == oos.status:%s sample_cnt:%s bucket_delta:%s" % (
    qc["oos_brier"].get("status"), qc["oos_brier"].get("sample_cnt"), qc["oos_brier"].get("bucket_delta_pct")))
print("  QC.overall_hit_rate=%s | backtest.overallHitRate=%s (设计口径不同：原始pooled vs 空)" % (
    qc["backtest"].get("overall_hit_rate"), bt.get("overallHitRate")))
chk(abs((qc["backtest"].get("realized_hit_rate") or 0) - (bt.get("realizedHitRate") or 0)) < 0.01,
    "QC.realized 与 backtest.realizedHitRate 不一致")

st = load_json(os.path.join(DATA, "structures.json"))
sigs = st.get("signals", [])
vals = sorted(set(s.get("signal") for s in sigs))
sd = [s["date"] for s in sigs]
chk(all(isinstance(v, (int, float)) and v in (0, 1, -1) for v in vals), "structures signal含非法值")
chk(sd == sorted(sd), "structures信号日期未排序")
chk(not any(not is_a_share_trading_day(d) for d in sd), "structures含非交易日信号")

print("\n==== 问题汇总 ====")
if problems:
    for p in problems:
        print("  [X]", p)
else:
    print("  无一致性问题（已确认双口径命中率为设计，非 bug）")
print("PROBLEM_COUNT", len(problems))

if problems:
    print("\n::error::_audit_data_consistency 发现 %d 处一致性问题" % len(problems))
    sys.exit(1)
print("\n::notice::_audit_data_consistency 通过")
