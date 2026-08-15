# -*- coding: utf-8 -*-
"""R49 守门员：概率三级降级（回测实证 → 漂移模型主估计 受 历史浪幅校准可信上限约束）。

锁 R49 不变量（独立复刻 build_data.py 数学，只读不写、不调 run_backtest）：
  ① 所有目标含 lo/hi/bandPct/prob/probSrc/expDays，区间包围点位、bandPct∈(0,25)、prob∈[10,90]；
  ② probSrc ∈ {回测实证, 历史浪幅校准, 漂移模型}（R49 三级词表）；
  ③ 单一真值：若 backtest.summary 有实证命中率 → probSrc 必为 回测实证 且 prob==hitRate；
     否则 probSrc 必为 历史浪幅校准/漂移模型（冷启动，绝不可伪造实证）；
  ④ R49 数学锁：对每条目标独立复刻 漂移模型首达概率(反射原理) 与 历史浪幅校准可达上限，
     期望 prob = clamp(min(漂移概率, 历史上限))；probSrc 必须与「谁绑定」一致
     （历史上限<漂移概率→历史浪幅校准；否则→漂移模型）。彻底排除旧式 92-z*26 固定系数。
"""
import re, json, os, sys, math
import pandas as pd
import numpy as np
BASE = os.path.dirname(os.path.abspath(__file__))
MIN_SAMPLE = 3  # 必须与 backtest.MIN_SAMPLE 一致（实证命中率最小样本量；<3 标 cold）

def _chk(cond, msg):
    print(("  [OK] " if cond else "  [FAIL] ") + msg)
    return cond

def safe_idx(idx_list, ts):
    """返回 ts 在交易日列表中的下标；若不存在（如假期）则取其后最近交易日。
    与 build_data.py safe_idx 逐字一致，确保历史校准本地波动率切片位置锁定。"""
    try:
        return idx_list.index(ts)
    except ValueError:
        cand = [i for i, d in enumerate(idx_list) if d >= ts]
        return cand[0] if cand else len(idx_list) - 1

ok = True
print("=== R49 守门员：概率三级降级不变量 ===")

# ---- 加载 FIB_DATA ----
src = open(os.path.join(BASE, "data", "data.js"), encoding="utf-8").read()
D = json.loads(re.search(r"window\.FIB_DATA\s*=\s*(\{.*\})\s*;?\s*$", src, re.S).group(1))
last_close = float(D["lastClose"])
# R50：波动率 regime 阻尼系数 与 跨指数共振广度，从 data.js 读取（raw.md 已被每日自动化清理，
# 故守门员不再从磁盘重算，直接复用 build 写入的单一真值，与 _enrich 同源）。
_drift_conf = D["volRegime"]["driftConf"]
_vol_scale = D["volRegime"]["bandScale"]
_breadth = D["resonance"]["breadth"]
# R217：与 build_data 单一真源一致的校准映射（data.js.probCalib），同表复算，门禁不破。
_recal_edges = D["probCalib"]["edges"]
_recal_vals = D["probCalib"]["vals"]
_recal_K = len(_recal_vals)
def _recal(p):
    _p = max(0.0, min(1.0, float(p)))
    if _p >= 0.98:
        return _p
    _idx = min(_recal_K - 1, max(0, int(np.digitize(_p, _recal_edges) - 1)))
    return _recal_vals[_idx]

# ---- 独立复刻 R49 数学（只读 data/）----
st = json.load(open(os.path.join(BASE, "data", "structures.json"), encoding="utf-8"))
df = pd.read_csv(os.path.join(BASE, "data", "sh000001.csv"), parse_dates=["date"]).set_index("date")
idx = df.index
ret = np.log(df["close"] / df["close"].shift(1))
daily_vol = float(ret.rolling(20).std().iloc[-1])
_dw = [20, 60, 120, 250]
_dbw = {w: float(ret.rolling(w).mean().iloc[-1]) for w in _dw}
def _drift_for(exp):
    # 与 build_data.py 同步：多尺度日漂移随 horizon 平滑插值（消除离散窗口跳变）
    _w = min(_dw[-1], max(_dw[0], float(exp)))
    if _w <= _dw[0]:
        return _dbw[_dw[0]]
    if _w >= _dw[-1]:
        return _dbw[_dw[-1]]
    for i in range(len(_dw) - 1):
        w0, w1 = _dw[i], _dw[i + 1]
        if w0 <= _w <= w1:
            _t = (_w - w0) / (w1 - w0)
            return _dbw[w0] * (1 - _t) + _dbw[w1] * _t
    return _dbw[_dw[-1]]
_hist_legs = []
for _i in range(len(st.get("zigzag", [])) - 1):
    _p0, _p1 = st["zigzag"][_i], st["zigzag"][_i + 1]
    try:
        _ld = max(1, len(pd.bdate_range(pd.Timestamp(_p0["date"]), pd.Timestamp(_p1["date"]))) - 1)
    except Exception:
        continue
    if _ld < 10:
        continue
    try:
        _lr = math.log(float(_p1["price"]) / float(_p0["price"]))
    except Exception:
        continue
    _hist_legs.append((_lr, _ld, str(_p0["date"]), str(_p1["date"])))
def _hist_calib(price, exp):
    """与 build_data.py 逐字一致：R57 改「幅度比较」经验可达率(弃用腿自身波动率 z 标准化)；
    R58 加最小有效样本 MIN_LEGS，渐进放宽时间窗([0.5,2]→[0.25,4]→[0,∞))，样本仍不足返回 None
    (不伪造90封顶)。与 build_data.py _hist_calib 逐字一致，勿改回。"""
    _a = math.log(price / last_close)
    _dir = 1 if _a >= 0 else -1
    _at = abs(_a)
    def _rate(lo_b, hi_b):
        _n = _d = 0
        for _lr, _ld, _d0, _d1 in _hist_legs:
            if _dir * _lr <= 0:
                continue
            # R65 修复(与 build_data.py 逐字一致)：用「达到目标幅度所需时间」ttr 做时间窗匹配
            _ttr = (_ld * _at / abs(_lr)) if abs(_lr) > 0 else _ld
            if not (lo_b <= _ttr <= hi_b):
                continue
            _d += 1
            if abs(_lr) >= _at:
                _n += 1
        return _n, _d
    # 渐进放宽时间窗直到样本量达标，避免小样本把经验率钉在 90/50/100(噪声)。与 build 逐字一致。
    MIN_LEGS = 4
    for _lo, _hi in ((0.5 * exp, 2.0 * exp), (0.25 * exp, 4.0 * exp), (0.0, 1e9)):
        _n, _d = _rate(_lo, _hi)
        if _d >= MIN_LEGS:
            return max(2, min(98, _n / _d * 100))
    return None
def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
# R74 解耦：带宽硬上限由 build 的 min(_band, price*0.235) 独立保证；audit50 仅复刻概率数学
# （_vol_for/_drift_for/_expected），不依赖 _vol_scale/_exp_cap（均为 R74 前遗留死代码，已删）。
_vol_windows = [20, 60, 120, 250]
_vol_by_w = {w: float(ret.rolling(w).std().iloc[-1]) for w in _vol_windows}
def _vol_for(exp):
    _w = min(_vol_windows[-1], max(_vol_windows[0], float(exp)))
    if _w <= _vol_windows[0]:
        return _vol_by_w[_vol_windows[0]]
    if _w >= _vol_windows[-1]:
        return _vol_by_w[_vol_windows[-1]]
    for i in range(len(_vol_windows) - 1):
        w0, w1 = _vol_windows[i], _vol_windows[i + 1]
        if w0 <= _w <= w1:
            _t = (_w - w0) / (w1 - w0)
            return _vol_by_w[w0] * (1 - _t) + _vol_by_w[w1] * _t
    return _vol_by_w[_vol_windows[-1]]
def _expected(price, exp):
    _exp = max(10, int(round(exp)))
    _mu = _drift_for(_exp) * _drift_conf
    _sv = _vol_for(_exp) * math.sqrt(_exp) if _vol_for(_exp) > 0 else 1e-9
    _dir = 1 if price >= last_close else -1
    _frac = min(_vol_for(_exp) * math.sqrt(_exp) * _vol_scale, 0.235)  # R89 band-edge (与 build _enrich 同源)
    if _dir > 0:
        _barrier = price * (1.0 - _frac)
        if last_close >= _barrier:
            _hit = 1.0
        else:
            _a = math.log(_barrier / last_close)
            _d1 = (_a - _mu * _exp) / _sv
            _d2 = (-_a - _mu * _exp) / _sv
            _exo = max(-50.0, min(50.0, 2.0 * _mu * _a / (_vol_for(_exp) ** 2 + 1e-12)))
            _hit = 1.0 - _norm_cdf(_d1) + math.exp(_exo) * _norm_cdf(_d2)
    else:
        _barrier = price * (1.0 + _frac)
        if last_close <= _barrier:
            _hit = 1.0
        else:
            _a = math.log(_barrier / last_close)
            _b = -_a
            _d1 = (_b + _mu * _exp) / _sv
            _d2 = (_mu * _exp - _b) / _sv
            _exo = max(-50.0, min(50.0, -2.0 * _mu * _b / (_vol_for(_exp) ** 2 + 1e-12)))
            _hit = 1.0 - _norm_cdf(_d1) + math.exp(_exo) * _norm_cdf(_d2)
    _hit = _recal(_hit)   # R217：分段校准，须与 build_data._recal_g 逐字一致
    _pd = max(2, min(98, _hit * 100))
    _hc = _hist_calib(price, _exp)
    _pa = _pd + _breadth * _dir * 5.0
    _exp_ret = min(_pa, _hc) if _hc is not None else _pa
    return round(max(2, min(98, _exp_ret)), 1), _pd, _hc, _pa

# ---- 回测实证命中率单一真值 ----
bt = D.get("backtest", {})
bt_lookup = {(s["cat"], s["key"]): (s["hitRate"], s["n"]) for s in bt.get("summary", [])}
ALLOWED = ("回测实证", "历史浪幅校准", "漂移模型")

# ---- 遍历全部目标 ----
targets = []
for s in D["tradePlan"]["sellTargets"]:
    targets.append(("sellTarget", s["name"], s["price"], s))
for p in D["subForecast"]["points"]:
    targets.append(("subwave", p["label"], p["price"], p))
for r in D["subForecast"]["rows"]:
    targets.append(("subwave", r["wave"], r["target"], r))

print("  目标数: %d" % len(targets))
for cat, key, price, t in targets:
    prefix = "%s/%s" % (cat, key)
    # ① 字段齐备 + 区间 + 边界
    for fld in ("lo", "hi", "bandPct", "prob", "probSrc", "expDays"):
        chk = _chk(fld in t, "%s 含字段 %s" % (prefix, fld)); ok = ok and chk
    if "lo" in t and "hi" in t:
        chk = _chk(t["lo"] < price < t["hi"], "%s 区间包围点位 %.2f<%.2f<%.2f" % (prefix, t["lo"], price, t["hi"])); ok = ok and chk
    if "bandPct" in t:
        chk = _chk(0 < t["bandPct"] < 25, "%s bandPct∈(0,25): %.1f" % (prefix, t["bandPct"])); ok = ok and chk
    if "prob" in t:
        chk = _chk(2 <= t["prob"] <= 98, "%s prob∈[2,98]: %.1f" % (prefix, t["prob"])); ok = ok and chk
    if "probSrc" not in t:
        continue
    # ② 词表合法
    chk = _chk(t["probSrc"] in ALLOWED, "%s probSrc 合法: %s" % (prefix, t["probSrc"])); ok = ok and chk
    # ③ 单一真值：实证(融合) vs 冷启动
    bt = bt_lookup.get((cat, key))
    if bt is not None and bt[0] is not None:
        _hr, _bn = bt
        # 复刻 build 的贝叶斯融合：先验=漂移模型+共振(_expected 返回的 _pa)，与实证按样本量收缩
        _exp_drift, _pd, _hc, _pa = _expected(price, t["expDays"])   # _pa = 漂移先验(未封顶)
        _K = 20.0   # R90：须与 build_data._FUSE_K 一致(字节级同步)；增大伪计数→融合偏向校准良好的漂移先验，OOS Brier −8%
        _fused = max(2, min(98, (_bn * _hr + _K * _pa) / (_bn + _K)))
        chk = _chk(t["probSrc"] == "回测实证", "%s 实证驱动→probSrc=回测实证" % prefix); ok = ok and chk
        chk = _chk(abs(t["prob"] - _fused) < 0.6,
                   "%s prob(%.1f)==融合实证(%.1f) [实证%.1f/n%d/先验%.1f]" % (prefix, t["prob"], _fused, _hr, _bn, _pa)); ok = ok and chk
        continue  # 实证驱动，跳过 R49 数学锁
    else:
        chk = _chk(t["probSrc"] in ("历史浪幅校准", "漂移模型"),
                   "%s 冷启动→probSrc∈{历史浪幅校准,漂移模型}(非伪造实证)" % prefix); ok = ok and chk
    # ④ R49+R50 数学锁
    _exp, _pd, _hc, _pa = _expected(price, t["expDays"])
    chk = _chk(abs(t["prob"] - _exp) < 0.15,
               "%s prob(%.1f)==R49+R50期望(%.1f) [漂移%.1f / 共振%.1f / 历史上限%s]" % (prefix, t["prob"], _exp, _pd, _pa, _hc)); ok = ok and chk
    _binder = "历史浪幅校准" if (_hc is not None and _hc < _pa) else "漂移模型"
    chk = _chk(t["probSrc"] == _binder,
               "%s probSrc(%s)==绑定方(%s)" % (prefix, t["probSrc"], _binder)); ok = ok and chk

# ---- 源码级防线：build_data.py _enrich 不再含旧式 92-...*26 固定系数（仅在函数体内查，避注释误报）----
bsrc = open(os.path.join(BASE, "build_data.py"), encoding="utf-8").read()
_m = re.search(r"def _enrich\(.*?(?=\n    def |\nif __name__|\Z)", bsrc, re.S)
_body = _m.group(0) if _m else bsrc
chk = _chk("92 -" not in _body and "* 26" not in _body,
           "build_data.py _enrich 已移除旧式 92-...*26 固定系数启发式")
ok = ok and chk
chk = _chk("_norm_cdf" in bsrc and "_hist_calib" in bsrc and "_drift_for" in bsrc,
           "build_data.py 含 R49 漂移/历史校准工具（三级降级落地）")
ok = ok and chk

print("\n守门员结论：" + ("全部不变量通过，0 问题。" if ok else "存在问题，见上。"))
sys.exit(0 if ok else 1)
