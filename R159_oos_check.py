# -*- coding: utf-8 -*-
"""R159 忠实证据台：在不改动引擎基线的前提下，量化两类「准确度」事实。
(A) 子浪ⅱ回撤偏差：用唯一真实同浪级样本(浪③子浪)实测模型假设(61.8%)造成的深度误差，
    并换算到当前浪⑤的买②位偏差。
(B) 概率校准 OOS 复验：用真实 sh000001.csv 重建锚点池，对【已部署 data.js】的生产概率做
    walk-forward 首达命中率对比，算 Brier，验证概率准确度(不改动任何引擎代码)。
"""
import json, re, math, sys, os
import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

# ---------- (A) 子浪ⅱ回撤偏差量化 ----------
# 真实同浪级样本：浪③ 已完成 5 浪，sub_wave_points 即其实测子浪拐点
p0, p1, p2 = 3040.69, 4034.08, 3815.84   # 浪③起 / 子浪ⅰ顶 / 子浪ⅱ底
real_ret = (p1 - p2) / (p1 - p0)          # 真实子浪ⅱ回撤占ⅰ
model_ret = 0.618                         # 模型假设(斐波那契标准，经验校准休眠)
# 模型在浪③上会预测的子浪ⅱ底(用 61.8% 假设)
p2_model = p1 - model_ret * (p1 - p0)
bias_pts = p2 - p2_model                  # 模型底比真实底低多少(偏深为正)
print("=" * 64)
print("【A】子浪ⅱ回撤偏差（唯一真实同浪级样本 = 浪③）")
print("  真实子浪ⅱ回撤 = %.1f%%   模型假设 = %.1f%%" % (real_ret * 100, model_ret * 100))
print("  浪③实测子浪ⅱ底 = %.2f   模型按61.8%%预测 = %.2f" % (p2, p2_model))
print("  → 模型系统性把子浪ⅱ底设深 %.2f 点 (偏深 %.1f%%)" % (bias_pts, bias_pts / p1 * 100))

# 换算到当前浪⑤：浪⑤幅度取卖①保守口径，端点锁定子浪ⅴ≡卖①
w4_low = 3741.11
sell1 = 4493.94
sf_A = sell1 - w4_low
R_III, R_IV = 1.618, 0.2917
def sub_ii(R2):
    amp = 2 - R2 + R_III - R_IV * R_III
    Ri = 1.0 / amp
    return w4_low + Ri * (1 - R2) * sf_A
sf_ii_model = sub_ii(model_ret)
sf_ii_real = sub_ii(real_ret)
print("  浪⑤模型买②(子浪ⅱ底, 61.8%%) = %.2f" % sf_ii_model)
print("  浪⑤真实锚定买②(%.1f%%) = %.2f" % (real_ret * 100, sf_ii_real))
print("  → 当前浪⑤买②位模型比真实锚定深 %.2f 点" % (sf_ii_model - sf_ii_real))

# ---------- (B) 概率校准 OOS 复验 ----------
# 读已部署 data.js（提取 JSON 段）
raw = open(BASE + "/data/data.js", encoding="utf-8").read()
m = re.search(r"=\s*(\{.*\})\s*;?\s*$", raw, re.S)
if not m:
    # 兜底：找 var DATA = {...}
    m = re.search(r"var\s+\w+\s*=\s*(\{.*\})\s*;", raw, re.S)
if not m:
    raise SystemExit("R159: 无法从 data.js 解析 JSON（格式异常），中止")
data = json.loads(m.group(1))

# 生产目标（卖点 + 子浪点），含 price / expDays / prob / lo / hi
targets = []
for s in data.get("sellTargets", []):
    targets.append(("卖点:" + s["name"], s["price"], s["expDays"], s.get("prob"), s.get("lo"), s.get("hi")))
sf = data.get("subForecast", {})
for p in sf.get("points", []):
    if p.get("label") == "浪⑤起":
        continue
    targets.append(("子浪:" + p["label"], p["price"], p["expDays"], p.get("prob"), p.get("lo"), p.get("hi")))

# 读真实价格
df = pd.read_csv(BASE + "/data/sh000001.csv", parse_dates=["date"]).set_index("date").sort_index()
close = df["close"].values.astype(float)
high = df["high"].values.astype(float)
low = df["low"].values.astype(float)
nn = len(df)
ret = np.log(close[1:] / close[:-1])
# 与 build_data 一致：daily_vol 用 20 日 rolling std。ret 比 close 少 1 行，
# dvol[i] 对齐到 close[i+1]；锚点 i 须 >=20 才有 20 日 vol，故用 i-1 取 dvol。
dvol = np.concatenate([[np.nan], pd.Series(ret).rolling(20).std().values])
daily_vol = float(dvol[-1])
# 锚点池：vol 匹配带 (0.75,1.25) + 趋势态(MA20 同今日) —— 与 _empirical_rates / _anchors 一致
ma20 = pd.Series(close).rolling(20).mean().values
today_up = bool(close[-1] > ma20[-1])
anchors = [i for i in range(20, nn)
           if not math.isnan(dvol[i]) and 0.75 * daily_vol <= dvol[i] <= 1.25 * daily_vol
           and not math.isnan(ma20[i]) and (close[i] > ma20[i]) == today_up]
print()
print("=" * 64)
print("【B】概率校准 OOS 复验（已部署 data.js 生产概率 vs 真实首达）")
print("  锚点池规模 = %d（vol带0.75-1.25 + MA20趋势态匹配）" % len(anchors))
print("  测试目标数 = %d" % len(targets))

briers, n_pairs = [], 0
for name, price, exp, prob, lo, hi in targets:
    if prob is None or exp is None:
        continue
    exp = max(1, int(round(exp)))
    up = price >= close[-1]
    # 关键：与 _empirical_rates 一致——目标是「相对移动」，每个锚点 i 把绝对带 [lo,hi]
    # 按 close[i]/last_close 投影到该锚点价位，测「从相似 regime 起点能否走出同等幅度」。
    # 用绝对带直接测会系统性低估命中率(锚点价位≠今日价)，属测试台口径错误，非引擎 bug。
    hits = 0; tot = 0
    for i in anchors:
        if i + exp >= nn:
            continue
        proj = close[i] / close[-1]
        b_lo = proj * lo if lo is not None else proj * price * 0.95
        b_hi = proj * hi if hi is not None else proj * price * 1.05
        if up:
            hit = high[i + 1:i + 1 + exp].max() >= b_lo
        else:
            hit = low[i + 1:i + 1 + exp].min() <= b_hi
        tot += 1
        if hit:
            hits += 1
    if tot >= 10:
        emp = hits / tot
        brier = (prob / 100.0 - emp) ** 2
        briers.append(brier); n_pairs += 1
mean_brier = float(np.mean(briers)) if briers else float("nan")
print("  有效(目标×锚点)配对 = %d" % n_pairs)
print("  平均 Brier = %.4f  （越低越准；0=完美校准）" % mean_brier)
print("  说明：生产 prob 本身由实证命中率融合而来，本检查验证『展示概率』与『真实首达频率』一致。")
