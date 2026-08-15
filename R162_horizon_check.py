# -*- coding: utf-8 -*-
"""R162：用唯一真实完成的同浪级样本(浪③)实测子浪触达时间估计(_horizon_for)的准确度。
前四轮只验了价格触达概率(Brier)，从没验"哪天到"这个维度。本脚本复现 build_data 的
_hist_legs 构建 + _horizon_for 逻辑(相对任意起点价，公平测方法本身)，对浪③ 4 段真实
子浪对比"模型估计交易日数 vs 真实 bdate_range 天数"，量化时间估计误差(MAPE)。
不改动引擎；仅出证据。
"""
import os, math, json
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))

# 1) 同源 zigzag（build_data 用的正是 structures.json["zigzag"]）
with open(os.path.join(BASE, "data", "structures.json"), encoding="utf-8") as f:
    st = json.load(f)

# 2) 复现 _hist_legs 构建（build_data.py 175-188）
_hist_legs = []
for i in range(len(st.get("zigzag", [])) - 1):
    p0, p1 = st["zigzag"][i], st["zigzag"][i + 1]
    try:
        ld = max(1, len(pd.bdate_range(pd.Timestamp(p0["date"]), pd.Timestamp(p1["date"]))) - 1)
    except Exception:
        continue
    if ld < 10:
        continue
    try:
        lr = math.log(float(p1["price"]) / float(p0["price"]))
    except Exception:
        continue
    _hist_legs.append((lr, ld))

print("hist_legs 数量:", len(_hist_legs))
_lr_up = [lr for lr, _ in _hist_legs if lr > 0]
_lr_dn = [lr for lr, _ in _hist_legs if lr < 0]
print("  上行腿 %d 条，幅度区间 [%.1f%%, %.1f%%]" % (len(_lr_up), min(_lr_up)*100, max(_lr_up)*100))
print("  下行腿 %d 条，幅度区间 [%.1f%%, %.1f%%]" % (len(_lr_dn), min(_lr_dn)*100, max(_lr_dn)*100))


# 3) 复现 _horizon_for 逻辑（相对任意起点 base，公平测方法本身）
def horizon_for(price, base):
    _a = math.log(price / base)
    _dir = 1 if _a >= 0 else -1
    _at = abs(_a)
    _ttr = sorted(ld * (_at / abs(lr)) for lr, ld in _hist_legs
                  if _dir * lr > 0 and abs(lr) >= _at and abs(lr) > 0)
    if len(_ttr) >= 4:
        _exp = _ttr[len(_ttr) // 2]
    else:
        _near = sorted([(abs(lr), ld) for lr, ld in _hist_legs if _dir * lr > 0],
                       key=lambda x: abs(x[0] - _at))[:5]
        if _near:
            _exps = [ld * (_at / abs(lr)) for lr, ld in _near if abs(lr) > 0]
            _exp = sum(_exps) / len(_exps)
        else:
            _exp = 330
    return max(10, min(330, int(round(_exp))))


# 4) 浪③真实子浪段（build_data sub_wave_points）
sw = [
    ("浪③起→子浪ⅰ",   "2025-04-07", 3040.69, "2025-11-14", 4034.08),
    ("子浪ⅰ→子浪ⅱ",   "2025-11-14", 4034.08, "2025-12-16", 3815.84),
    ("子浪ⅱ→子浪ⅲ顶", "2025-12-16", 3815.84, "2026-05-14", 4258.86),
    ("子浪ⅲ顶→浪④?",  "2026-05-14", 4258.86, "2026-07-20", 3741.11),
]

print("\n%-16s %8s %8s %8s %8s" % ("段", "幅度%", "真实天", "模型估", "误差%"))
errs = []
for name, d0, p0, d1, p1 in sw:
    real = len(pd.bdate_range(pd.Timestamp(d0), pd.Timestamp(d1))) - 1
    est = horizon_for(p1, p0)
    err = (est - real) / real * 100
    errs.append(abs(err))
    print("%-16s %8.2f %8d %8d %+8.1f" % (name, (p1 / p0 - 1) * 100, real, est, err))

print("\n时间估计 MAPE = %.1f%%  (误差绝对值均值)" % (sum(errs) / len(errs)))
print("判定：MAPE<30%% 视为方法可靠；>50%% 视为时间估计系统性失真，需修。")
