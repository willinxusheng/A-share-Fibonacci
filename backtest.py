# -*- coding: utf-8 -*-
"""预测回测闭环（提升预测数据准确性的地基）。

设计：
- archive(): 每次 build_data 生成后，把当日预测目标（卖①②③ + 子浪ⅰ~ⅴ）存档到
  data/predictions_log.jsonl。按 (预测日期, 目标, 类别) 去重，重跑不重复计数。
- evaluate(): 对每条历史记录，扫描其后 HORIZON 个交易日的 K 线，判定是否触及
  （卖/持有类看 high>=price 的上行目标；买类看 low<=price 的下行目标），
  记录触达日与接近度（approach>=1 表示触及）。
- aggregate(): 按 (cat,key) 聚合命中率、平均触达天数，样本不足时标 cold 不伪造置信度。
- run_backtest(data, df): 编排上述三步，写 data/backtest.json 并返回注入 FIB_DATA 的统计。

命中率只统计"已到观察期"的样本；冷启动阶段显式标注、不展示比率，避免误导。
"""
import json
import os
import math

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
HORIZON = 30          # 观察窗口（交易日）：艾略特目标通常数日到数周触达
MIN_SAMPLE = 3        # 低于此样本量视为冷启动，不展示命中率
LOG_PATH = os.path.join(BASE, "data", "predictions_log.jsonl")
OUT_PATH = os.path.join(BASE, "data", "backtest.json")


def _safe_idx(dates, ts):
    """ts 在交易日字符串列表中的下标；非交易日取其后者最近，避免崩溃。"""
    try:
        return dates.index(ts)
    except ValueError:
        cand = [i for i, d in enumerate(dates) if d >= ts]
        return cand[0] if cand else len(dates) - 1


def extract_targets(data):
    """从 FIB_DATA 提取当日预测目标，统一 schema。"""
    recs = []
    for t in data.get("tradePlan", {}).get("sellTargets", []):
        recs.append({"key": t["name"], "price": float(t["price"]),
                     "side": "sell", "cat": "sellTarget",
                     "expDays": float(t.get("expDays", HORIZON))})
    for p in data.get("subForecast", {}).get("points", []):
        recs.append({"key": p["label"], "price": float(p["price"]),
                     "side": p["side"], "cat": "subwave",
                     "expDays": float(p.get("expDays", HORIZON))})
    return recs


def archive(data):
    """把当日目标追加进日志（去重）。返回新增条数。

    容错：读取历史日志时若遇半行/损坏行（如 CI 进程写一半被杀），
    跳过且不崩溃、不纳入 seen；落地改用「读-重写-追加」模式，丢弃坏行、
    按 (date,key,cat) 去重，避免半行与后续记录粘连引发连锁 JSON 失败，
    也避免坏行被重复计数污染命中率。正常无坏行时与旧 "a" 追加模式等价。
    """
    pred_date = data.get("updated")
    if not pred_date:
        return 0
    existing = []
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    existing.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    # 容忍半行/损坏行，不崩溃；后续重写时丢弃。
                    continue
    seen = {(e.get("date"), e.get("key"), e.get("cat")) for e in existing
            if e.get("date") and e.get("key") and e.get("cat")}
    new = 0
    fresh = []
    for r in extract_targets(data):
        k = (pred_date, r["key"], r["cat"])
        if k in seen:
            continue
        rec = {"date": pred_date, "key": r["key"], "cat": r["cat"],
               "side": r["side"], "price": r["price"],
               "expDays": r.get("expDays", HORIZON)}
        fresh.append(rec)
        seen.add(k)
        new += 1
    if new:
        # 先写历史有效行，再追加本次新增；丢弃坏行、按 (date,key,cat) 去重，
        # 消除半行粘连后续记录导致的连锁 JSON 崩溃与重复计数。
        # 原子写：写临时文件后 os.replace 整体替换，避免 CI 进程写一半被杀导致
        # LOG_PATH 含半行/不完整内容（下次 archive 虽容忍坏行但会丢失该次原子性）。
        _tmp = LOG_PATH + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            for e in existing:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
            for rec in fresh:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(_tmp, LOG_PATH)
    return new


def evaluate(df):
    """重评日志全部记录，返回带 outcome 的记录列表。"""
    idx = list(df.index)
    dates = [d.strftime("%Y-%m-%d") for d in idx]
    # R91：band-edge 命中定义所需的 vol regime 工具（与 calibrate._vol_scale_at / build_data._vol_for 同源）。
    # 取【记录自身_date】的滚动 vol，使前段 archive 的"触达 band"与部署当日所用 vol regime 一致
    # （walk-forward 自洽：部署在日 i 用日 i 的 vol，校准才验证"线上实际用的"定义）。
    _ret = np.log(df["close"] / df["close"].shift(1))
    _hv20 = _ret.rolling(20).std() * np.sqrt(244) * 100
    _VW = [20, 60, 120, 250]
    _vol_by_w = {w: _ret.rolling(w).std().values for w in _VW}

    def _vol_scale_at(i):
        if i < 0 or i >= len(_hv20) or pd.isna(_hv20.iloc[i]):
            return 1.0
        # R123 修复前视泄漏：原 (hv20.dropna() < hv20.iloc[i]) 用全序列(含 i 之后未来时点)
        # 算 vol 分位，等于回测日 i 偷看未来 vol 分布来定 band scale，违背 R85 忠实 OOS。
        # 改为截至 i 的历史窗口 iloc[:i+1]：与生产在末日(iloc[:end+1]=全序列)语义一致，
        # 且回测/校准不再含未来信息。i 已受 pd.isna 守卫(rolling 填满后才有意义，样本>=20)。
        _pct = float((_hv20.iloc[:i + 1].dropna() < _hv20.iloc[i]).mean()) * 100
        return 1.15 if _pct >= 66 else (1.0 if _pct >= 33 else 0.88)

    def _vol_for(exp, i):
        w = min(_VW[-1], max(_VW[0], float(exp)))
        if w <= _VW[0]:
            return _vol_by_w[_VW[0]][i]
        if w >= _VW[-1]:
            return _vol_by_w[_VW[-1]][i]
        for a, b in zip(_VW, _VW[1:]):
            if a <= w <= b:
                t = (w - a) / (b - a)
                return _vol_by_w[a][i] * (1 - t) + _vol_by_w[b][i] * t
        return _vol_by_w[_VW[-1]][i]

    if not os.path.exists(LOG_PATH):
        return []
    recs = []
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if not rec.get("date"):
                continue
            i0 = _safe_idx(dates, rec["date"])
            # 观察窗口自适应：中长期目标(卖②③/子浪ⅲⅴ 等)真实触达需数月，
            # 固定 HORIZON=30 会让其观察期一到即被永久记 miss→命中率系统性失真。
            # 改用 max(HORIZON, 目标真实触达时间估计expDays)，与 _enrich 时间锚定同源。
            _hz = max(HORIZON, int(rec.get("expDays") or HORIZON))
            # R291 成熟门禁：观察窗是否闭合(已有足够未来交易日覆盖 max(HORIZON,expDays))。
            # 闭合(matured)的目标方计入精确命中率分母；未闭合保持 unevaluated，不记精确 miss——
            # 否则需数月/年的浪⑤目标在窗未闭合时会被记为永久精确 miss，污染精确命中率。
            matured = (i0 + _hz) <= (len(idx) - 1)
            # R291：方向准确率(最诚实的早期信号，与成熟门禁独立)——
            # 预测价位相对【预测时刻指数位置 close0】的方向是否正确：
            # 上行目标需未来 high 触及预测时 close 之上；下行目标需未来 low 跌破预测时 close。
            # 方向在第 1 个未来交易日即可判定，无需等观察窗闭合，故作为早期主指标暴露给证书/面板。
            _c0_raw = df["close"].iloc[i0]
            close0 = float(_c0_raw) if (not pd.isna(_c0_raw) and _c0_raw is not None) else None
            px = rec["price"]
            _exp = rec.get("expDays") or HORIZON
            # R91：触达定义对齐模型 band-edge（与 build_data._enrich / calibrate 同源）。
            # 用记录自身_date 的 vol regime 取 _frac，忠实复现"部署在日 i 用日 i 的 vol"。
            # 远期目标 _frac 触 0.235 上限（与 _enrich 同）。
            _v = _vol_for(_exp, i0)
            _frac = 0.0
            if _v is not None and not math.isnan(_v) and _v > 0:
                _frac = min(_v * math.sqrt(_exp) * _vol_scale_at(i0), 0.235)
            # 提前命中(early-hit)：观察窗未闭合 ≠ 不能判定命中。
            # 若目标已在【全部已有前视数据】里触及 band 边缘，即为命中——
            # 命中可立即判定，仅"未命中"需等观察窗完整闭合才下结论。
            # 旧逻辑(见下)在 i0+_hz>末日 时整条标 unevaluated 并 continue，
            # 跳过触达检查→30~280 日长窗目标即便第 3 天就被触及也要白等 ~1 年，
            # 回测长期 totalEvaluated=0（R91 设计漏洞）。现用 df.iloc[i0+1:len(idx)]
            # （不被 _hz 截断）检测触达，命中当天即计入。
            fut = df.iloc[i0 + 1: len(idx)]
            if fut.empty:
                rec.update({"evaluated": False, "hit": False, "hit_date": None,
                            "days_to_hit": None, "approach": None,
                            "preciseHit": None, "approachTarget": None,
                            "dirCorrect": None, "matured": False})
            elif rec["side"] == "buy":          # 下行目标：未来最低是否触及 band 上缘 px*(1+_frac)
                lo = float(fut["low"].min())
                hit = lo <= px * (1.0 + _frac)
                approach = px / lo if lo else None
                # 精确命中：未来最低是否真正触及【目标价位 px 本身】(非宽松 band 边)——诚实预测力指标
                preciseHit = (lo <= px)
                approachTarget = approach
                best = lo
                # 方向准确率：下行目标需未来 low 跌破预测时刻 close（方向向下，判对）
                dirCorrect = (close0 is not None and lo <= close0)
                if hit:
                    first_hit = fut["low"].le(px * (1.0 + _frac)).idxmax()
                    hd = first_hit.strftime("%Y-%m-%d")
                    rec.update({"evaluated": True, "hit": True, "hit_date": hd,
                                "days_to_hit": int(dates.index(hd) - i0),
                                "approach": round(approach, 4),
                                # R291 三态精确命中：已精确触达(hi>=px)=True；
                                # 仅触达 band 但未精确且窗口未闭合=None(待观察,非 miss)；
                                # 窗口已闭合仍未精确=False(真 miss)。避免把"未闭合窗口"误记精确 miss。
                                "preciseHit": (True if preciseHit else (False if matured else None)),
                                "approachTarget": round(approachTarget, 4) if approachTarget else None,
                                "dirCorrect": bool(dirCorrect), "matured": matured})
                elif i0 + _hz > len(idx) - 1:    # 未命中且观察窗未闭合→保持 unevaluated(不判 miss)
                    rec.update({"evaluated": False, "hit": False, "hit_date": None,
                                "days_to_hit": None,
                                "approach": round(approach, 4) if approach else None,
                                # R291：未闭合窗口精确命中未知→标 None(非 False)，
                                # 避免被误读为"精确 miss"污染精确命中率分母。
                                "preciseHit": None,
                                "approachTarget": round(approachTarget, 4) if approachTarget else None,
                                "dirCorrect": bool(dirCorrect), "matured": matured})
                else:                            # 观察窗已闭合且未命中→判 miss
                    rec.update({"evaluated": True, "hit": False, "hit_date": None,
                                "days_to_hit": None,
                                "approach": round(approach, 4) if approach else None,
                                "best": round(best, 2),
                                "preciseHit": False,
                                "approachTarget": round(approachTarget, 4) if approachTarget else None,
                                "dirCorrect": bool(dirCorrect), "matured": matured})
            else:                              # 上行目标：未来最高是否触及 band 下缘 px*(1-_frac)
                hi = float(fut["high"].max())
                hit = hi >= px * (1.0 - _frac)
                approach = hi / px if px else None
                # 精确命中：未来最高是否真正触及【目标价位 px 本身】(非宽松 band 边)
                preciseHit = (hi >= px)
                approachTarget = approach
                best = hi
                # 方向准确率：上行目标需未来 high 高于预测时刻 close（方向向上，判对）
                dirCorrect = (close0 is not None and hi >= close0)
                if hit:
                    first_hit = fut["high"].ge(px * (1.0 - _frac)).idxmax()
                    hd = first_hit.strftime("%Y-%m-%d")
                    rec.update({"evaluated": True, "hit": True, "hit_date": hd,
                                "days_to_hit": int(dates.index(hd) - i0),
                                "approach": round(approach, 4),
                                # R291 三态精确命中：已精确触达(hi>=px)=True；
                                # 仅触达 band 但未精确且窗口未闭合=None(待观察,非 miss)；
                                # 窗口已闭合仍未精确=False(真 miss)。避免把"未闭合窗口"误记精确 miss。
                                "preciseHit": (True if preciseHit else (False if matured else None)),
                                "approachTarget": round(approachTarget, 4) if approachTarget else None,
                                "dirCorrect": bool(dirCorrect), "matured": matured})
                elif i0 + _hz > len(idx) - 1:    # 未命中且观察窗未闭合→保持 unevaluated(不判 miss)
                    rec.update({"evaluated": False, "hit": False, "hit_date": None,
                                "days_to_hit": None,
                                "approach": round(approach, 4) if approach else None,
                                # R291：未闭合窗口精确命中未知→标 None(非 False)
                                "preciseHit": None,
                                "approachTarget": round(approachTarget, 4) if approachTarget else None,
                                "dirCorrect": bool(dirCorrect), "matured": matured})
                else:                            # 观察窗已闭合且未命中→判 miss
                    rec.update({"evaluated": True, "hit": False, "hit_date": None,
                                "days_to_hit": None,
                                "approach": round(approach, 4) if approach else None,
                                "best": round(best, 2),
                                "preciseHit": False,
                                "approachTarget": round(approachTarget, 4) if approachTarget else None,
                                "dirCorrect": bool(dirCorrect), "matured": matured})
            recs.append(rec)
    return recs


def aggregate(recs):
    """按 (cat,key) 聚合命中率，样本不足标 cold。

    R291 拆分两条诚实口径：
    - 方向准确率(dirHitRate)：统计【全部有方向判定】的样本(含观察窗未闭合、仅累计数日者)，
      因方向是「第 1 个未来交易日即可判定」的最诚实早期信号；仅未来 K 线为空(末日记录)才无判定。
    - 精确命中率(preciseHitRate)：仅统计【已成熟/已解决】(evaluated)样本，观察窗未闭合目标
      保持 unevaluated、不计入分母(成熟门禁)，避免把需数月/年的浪⑤目标在窗未闭合时记成精确 miss。
    """
    groups = {}
    for r in recs:
        g = groups.setdefault((r["cat"], r["key"]),
                              {"cat": r["cat"], "key": r["key"],
                               "n": 0, "hit": 0, "ph": 0, "days": [],
                               "dirEval": 0, "dirHits": 0,
                               "preciseEval": 0, "matured": 0})
        # 方向准确率：统计全部有方向判定(非 None)的样本——含观察窗未闭合(仅累计数日)的目标，
        # 因方向是「早期即可判定」的最诚实信号(R291)。仅未来 K 线为空(末日记录)才无方向判定。
        if r.get("dirCorrect") is not None:
            g["dirEval"] += 1
            if r.get("dirCorrect"):
                g["dirHits"] += 1
        # 窗口已闭合(成熟)计数：仅这些目标的精确命中率分母才是无偏的(命中与 miss 均已解出)。
        if r.get("matured"):
            g["matured"] += 1
        # band 触达：仅统计【已解决】(evaluated=早触达或窗口闭合)样本。
        if r.get("evaluated"):
            g["n"] += 1
            if r.get("hit"):
                g["hit"] += 1
                if r.get("days_to_hit"):
                    g["days"].append(r["days_to_hit"])
        # 精确命中(真实目标价位 px，非宽松 band 边)：三态(命中/真miss/待观察)。
        # 仅统计【已解决精确】(preciseHit 非 None：提前精确触达 或 窗口闭合后的真 miss)，
        # 观察窗未闭合且尚未精确触达者保持 None(待观察)不计入分母(R291 成熟门禁)。
        if r.get("preciseHit") is not None:
            g["preciseEval"] += 1
            if r.get("preciseHit"):
                g["ph"] += 1
    summary = []
    for (cat, key), g in groups.items():
        avg_days = round(sum(g["days"]) / len(g["days"]), 1) if g["days"] else None
        cold = g["n"] < MIN_SAMPLE
        if cold:
            hit_rate = None
        else:
            # 贝叶斯收缩：小样本命中率跳动极大(0/3=0%, 3/3=100%)，向中性先验 0.5 收缩。
            # 用 Laplace 规则 (hits+1)/(n+2)：n→0 时收敛到 0.5(中性先验)，n 大时逼近真实比率。
            # 【R58 修复】旧式 (hits+0.5)/(n+2) 的 n→0 极限是 0.25(偏悲观、与"向0.5收缩"注释矛盾)，
            # 现改为标准 Laplace，使冷启动实证命中率围绕 0.5 收缩、口径与注释及下游融合先验一致。
            hit_rate = round((g["hit"] + 1.0) / (g["n"] + 2) * 100, 1)
        # 精确命中率：R291 反假绿铁律——仅在【足够窗口已闭合】时才展示数值。
        # 否则"仅命中可早解、miss 须等窗口闭合"会造成严重向上偏差(假绿)：当前 144 目标
        # 0 个窗口闭合，若对"已提前精确触达"样本直接算率会虚高到 ~97%，误导决策。
        # 故要求 本组精确已解决样本>=MIN_SAMPLE 且 本组成熟(窗口闭合)样本>=MIN_SAMPLE 方出数；
        # 未达则标 None(待成熟)，诚实标注"精确价位命中率暂不可判定"。
        precise_rate = round((g["ph"] + 1.0) / (g["preciseEval"] + 2) * 100, 1) \
            if (g["preciseEval"] >= MIN_SAMPLE and g["matured"] >= MIN_SAMPLE) else None
        # 方向准确率(早期信号)用同一 Laplace 收缩；样本不足(含仅 1~2 未来日)也标 None 不伪造高置信。
        dir_rate = round((g["dirHits"] + 1.0) / (g["dirEval"] + 2) * 100, 1) if g["dirEval"] >= MIN_SAMPLE else None
        summary.append({
            "cat": cat, "key": key, "n": g["n"], "hits": g["hit"],
            "hitRate": hit_rate,
            "preciseHits": g["ph"], "preciseHitRate": precise_rate,
            "preciseEval": g["preciseEval"], "matured": g["matured"],
            "avgDays": avg_days, "cold": cold,
            "dirEval": g["dirEval"], "dirHits": g["dirHits"], "dirHitRate": dir_rate,
        })
    summary.sort(key=lambda x: (x["cat"], x["key"]))
    return summary


def run_backtest(data, df):
    """编排：存档当日 → 重评全部 → 聚合写盘 → 返回注入统计。"""
    archive(data)
    recs = evaluate(df)
    summary = aggregate(recs)
    total_eval = sum(1 for r in recs if r.get("evaluated"))
    total_pending = sum(1 for r in recs if not r.get("evaluated"))
    total_hits = sum(1 for r in recs if r.get("evaluated") and r.get("hit"))
    # R291 精确命中(真实目标价位)三态统计：
    # preciseEval=已解决精确(preciseHit 非 None：提前精确触达 或 窗口闭合后的真 miss)；
    # total_phits=其中真命中；precise_early=提前精确触达但窗口仍未闭合(鼓舞性但非最终结论)。
    total_phits = sum(1 for r in recs if r.get("preciseHit") is True)
    precise_eval = sum(1 for r in recs if r.get("preciseHit") is not None)
    precise_early = sum(1 for r in recs if r.get("preciseHit") is True and not r.get("matured"))
    # R291 方向准确率(最诚实早期信号)：统计【全部有方向判定】样本，含观察窗未闭合但已有未来 K 线的目标，
    # 因方向在第 1 个未来交易日即可判定；仅未来 K 线为空(末日记录)无方向判定。不局限于已成熟样本。
    total_dir_eval = sum(1 for r in recs if r.get("dirCorrect") is not None)
    total_dir_hits = sum(1 for r in recs if r.get("dirCorrect"))
    # R291 成熟门禁计数：观察窗已闭合(本机数据已有足够未来交易日覆盖)的目标方能计入精确命中率分母。
    matured_count = sum(1 for r in recs if r.get("matured"))
    # 已实现(已平仓/已命中)命中率【band 触达，宽松】：用 Laplace (hits+1)/(eval+2) 收缩，口径与 aggregate 一致。
    # 注意：仅统计【已解决】样本(命中 或 观察窗已闭合的 miss)；观察窗未闭合目标(totalPending)
    # 不计入分母——故早期该值偏乐观(未平仓的 miss 尚未计入)，随窗口闭合逐步收敛到真实命中率。
    realized_hit = round((total_hits + 1.0) / (total_eval + 2.0) * 100, 1) if total_eval else None
    # 精确命中率(真实目标价位，非宽松 band 边)：R291 反假绿铁律——仅在【足够窗口已闭合】时才展示数值。
    # 否则"仅命中可早解、miss 须等窗口闭合"会造成严重向上偏差(假绿)：当前 144 目标 0 个窗口闭合，
    # 若直接对"已提前精确触达"样本算率会虚高到 ~97%，误导决策。故要求
    # 全局 精确已解决样本>=MIN_SAMPLE 且 成熟(窗口闭合)样本>=MIN_SAMPLE 方出数；未达则 None(待成熟)。
    precise_realized = round((total_phits + 1.0) / (precise_eval + 2.0) * 100, 1) \
        if (precise_eval >= MIN_SAMPLE and matured_count >= MIN_SAMPLE) else None
    # 方向准确率(整体，Laplace 收缩)：早期最诚实信号，独立于成熟门禁。
    dir_realized = round((total_dir_hits + 1.0) / (total_dir_eval + 2.0) * 100, 1) if total_dir_eval else None
    stats = {
        "asOf": data.get("updated"),
        "horizon": HORIZON,
        "minSample": MIN_SAMPLE,
        "totalLogged": len(recs),
        "totalEvaluated": total_eval,
        "totalPending": total_pending,
        "maturedCount": matured_count,
        "preciseEvaluated": precise_eval,
        "preciseEarlyHits": precise_early,
        "totalDirEvaluated": total_dir_eval,
        "totalDirHits": total_dir_hits,
        "dirRealizedHitRate": dir_realized,
        "realizedHitRate": realized_hit,
        "preciseRealizedHitRate": precise_realized,
        "coldStart": total_eval < MIN_SAMPLE,
        "summary": summary,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    return stats


if __name__ == "__main__":
    import re
    d = json.loads(re.search(r"window\.FIB_DATA\s*=\s*(\{.*\})\s*;?\s*$",
                             open(os.path.join(BASE, "data", "data.js"), encoding="utf-8").read(),
                             re.S).group(1))
    _df = pd.read_csv(os.path.join(BASE, "data", "sh000001.csv"), parse_dates=["date"]).set_index("date")
    s = run_backtest(d, _df)
    print("回测:", s["totalLogged"], "条存档 /", s["totalEvaluated"], "条已评估(band) /",
          s["maturedCount"], "条窗口已闭合(成熟) /", s["totalPending"], "条观察窗未闭合 / cold=", s["coldStart"])
    print("  方向准确率(早期信号·主指标)=%s%%" % s["dirRealizedHitRate"])
    print("  band 触达率(宽松)=%s%%   精确命中率(真实目标价位·需窗口闭合才出数)=%s%%" %
          (s["realizedHitRate"], s["preciseRealizedHitRate"]))
    print("  精确已解决样本=%d  其中提前精确触达(窗口未闭合)=%d" % (s.get("preciseEvaluated", 0), s.get("preciseEarlyHits", 0)))
    for r in s["summary"]:
        print("  ", r["cat"], r["key"], "| n=%d" % r["n"],
              "| 方向准确率=%s" % (r["dirHitRate"] if r["dirHitRate"] is not None else "样本不足"),
              "| band命中率=%s" % (r["hitRate"] if r["hitRate"] is not None else "样本不足"),
              "| 精确命中率=%s" % (r["preciseHitRate"] if r["preciseHitRate"] is not None else "待成熟"),
              "| 平均触达=%s天" % r["avgDays"])
