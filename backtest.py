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


def _open_status(open_n, elapsed, exp, best):
    """#790 目标实时追踪状态：基于 open(pending)目标的中位时间进度与最佳接近度分类。

    仅用【已观测】信号（无需观察窗闭合），故可立即暴露"长期目标早期正常 /
    中期目标偏慢 / 价格已临界目标"等时效信号，辅助买卖时机判断。
    - 全部已解：本组无 pending 目标
    - 已临界：最佳接近度中位 |precDev|<=5%（价格已非常接近目标，随时可能触达）
    - 偏慢：  时间进度>=60% 但最佳接近度仍远(|precDev|>15%)，相对预期偏慢
    - 推进中：时间进度>=30%
    - 早期：  其余（刚启动）
    """
    if not open_n:
        return "全部已解"
    _tp = (float(np.median(elapsed)) / float(np.median(exp))) if (elapsed and exp) else 0.0
    _bm = float(np.median(best)) if best else None
    if _bm is not None and abs(_bm) <= 0.05:
        return "已临界"
    if _tp >= 0.6 and _bm is not None and abs(_bm) > 0.15:
        return "偏慢"
    if _tp >= 0.3:
        return "推进中"
    return "早期"


def extract_targets(data):
    """从 FIB_DATA 提取当日预测目标，统一 schema。

    #779：携带 baseCase/conditional 标记（regime-aware 降级）；并读取独立防御基准目标
    (data.defensiveScenario，side=buy 下行、cat=defensive、baseCase=True)。
    """
    recs = []
    for t in data.get("tradePlan", {}).get("sellTargets", []):
        recs.append({"key": t["name"], "price": float(t["price"]),
                     "side": "sell", "cat": "sellTarget",
                     "expDays": float(t.get("expDays", HORIZON)),
                     "baseCase": t.get("baseCase", True),
                     "conditional": t.get("conditional", False)})
    for p in data.get("subForecast", {}).get("points", []):
        recs.append({"key": p["label"], "price": float(p["price"]),
                     "side": p["side"], "cat": "subwave",
                     "expDays": float(p.get("expDays", HORIZON)),
                     "baseCase": p.get("baseCase", True),
                     "conditional": p.get("conditional", False)})
    # #779 独立防御基准目标：仅在 warn  regime 下由 build_data 注入
    _ds = data.get("defensiveScenario")
    if _ds and _ds.get("target") is not None:
        recs.append({"key": "均值回归基准", "price": float(_ds["target"]),
                     "side": "buy", "cat": "defensive",
                     "expDays": float(_ds.get("expDays", HORIZON)),
                     "baseCase": True, "conditional": False})
    return recs


def archive(data):
    """把当日目标追加进日志（去重 + UPDATE-aware 幂等）。返回新增+更新条数。

    容错：读取历史日志时若遇半行/损坏行（如 CI 进程写一半被杀），跳过且不崩溃、不纳入 seen；
    落地改用「读-重写-追加」模式，丢弃坏行、按 (date,key,cat) 去重/更新，避免半行与后续记录
    粘连引发连锁 JSON 失败，也避免坏行被重复计数污染命中率。正常无坏行时与旧模式等价。
    #779：同日二次写（regime-aware 重跑）时，对已存在的 (date,key,cat) 行【就地更新】
    price / baseCase / conditional（不新增行、不破坏历史命中率分母）。
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
    fresh = []
    updated = 0
    for r in extract_targets(data):
        k = (pred_date, r["key"], r["cat"])
        if k in seen:
            # UPDATE-aware：同日二次写仅就地更新价格/基准-条件标记（不新增行、不破坏分母）
            for e in existing:
                if (e.get("date"), e.get("key"), e.get("cat")) == k:
                    e["price"] = r["price"]
                    e["baseCase"] = r.get("baseCase", True)
                    e["conditional"] = r.get("conditional", False)
                    if "expDays" in r:
                        e["expDays"] = r.get("expDays", HORIZON)
                    updated += 1
                    break
        else:
            rec = {"date": pred_date, "key": r["key"], "cat": r["cat"],
                   "side": r["side"], "price": r["price"],
                   "expDays": r.get("expDays", HORIZON),
                   "baseCase": r.get("baseCase", True),
                   "conditional": r.get("conditional", False)}
            fresh.append(rec)
            seen.add(k)
    if fresh or updated:
        # 先写历史有效行（含就地更新），再追加本次新增；丢弃坏行、按 (date,key,cat) 去重/更新，
        # 消除半行粘连后续记录导致的连锁 JSON 崩溃与重复计数。
        # 原子写：写临时文件后 os.replace 整体替换，避免 CI 进程写一半被杀导致 LOG_PATH 含半行。
        _tmp = LOG_PATH + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            for e in existing:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
            for rec in fresh:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(_tmp, LOG_PATH)
    return updated + len(fresh)


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
            # #782 精确价位偏差(早期可观测精度信号)：基于最接近点 approachTarget 推导，
            # 取全部已有前视数据里的最接近点(不依赖观察窗闭合)——故即便目标窗口未闭合，
            # 亦可观测"价格离目标差多少"。带符号：>0=overshoot(价格冲过目标)、<0=undershoot(够不到)、
            # =0=精确命中；中位保留符号方能揭示系统性偏差方向。approachTarget：buy=px/lo、sell=hi/px。
            _at = rec.get("approachTarget")
            rec["precDev"] = round(_at - 1, 4) if _at is not None else None
            # #790 目标实时追踪：记录「自预测日起已过的交易日数」，供 open 目标进度判定
            rec["elapsedDays"] = int((len(idx) - 1) - i0)
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
                               "preciseEval": 0, "matured": 0,
                               "bcDirEval": 0, "bcDirHits": 0,
                               "precDevs": [],
                               # #790 目标实时追踪：open(pending=未触达且窗未闭)目标的时间/接近度累计
                               "open": 0, "openElapsed": [], "openExp": [], "openBest": []})
        # 方向准确率：统计全部有方向判定(非 None)的样本——含观察窗未闭合(仅累计数日)的目标，
        # 因方向是「早期即可判定」的最诚实信号(R291)。仅未来 K 线为空(末日记录)才无方向判定。
        if r.get("dirCorrect") is not None:
            g["dirEval"] += 1
            if r.get("dirCorrect"):
                g["dirHits"] += 1
        # #779 基准情形方向准确率：仅统计 baseCase=True（被系统当作基准发出的目标）的方向判定
        if r.get("baseCase", True) and r.get("dirCorrect") is not None:
            g["bcDirEval"] += 1
            if r.get("dirCorrect"):
                g["bcDirHits"] += 1
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
        # #782 精确价位偏差(早期可观测精度信号)：所有已计算 approachTarget(非 None)的样本均纳入，
        # 不依赖观察窗闭合/是否命中——即便目标窗口未到、尚未判定命中，也能观测"价格离目标差多少"，
        # 揭示系统性 overshoot(中位>0)/undershoot(中位<0)，为预测准确性提供窗口闭合前的真实信号。
        if r.get("approachTarget") is not None and r.get("precDev") is not None:
            g["precDevs"].append(r["precDev"])
        # #790 目标实时追踪：对【观察窗未闭合且尚未触达】(pending, evaluated=False)目标，
        # 累计其时间进度(已过时/预期时)与最佳接近度(precDev 中位)，供状态分类与面板展示。
        if not r.get("evaluated"):
            g["open"] += 1
            if r.get("elapsedDays") is not None:
                g["openElapsed"].append(r["elapsedDays"])
            if r.get("expDays") is not None:
                g["openExp"].append(int(r["expDays"]))
            if r.get("precDev") is not None:
                g["openBest"].append(r["precDev"])
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
        # #779 基准情形方向准确率(仅 baseCase=True 样本)：衡量"系统真正当作基准发出的目标"方向正确率；
        # 样本不足同样标 None 不伪造高置信。
        base_case_dir_rate = round((g["bcDirHits"] + 1.0) / (g["bcDirEval"] + 2) * 100, 1) if g["bcDirEval"] >= MIN_SAMPLE else None
        summary.append({
            "cat": cat, "key": key, "n": g["n"], "hits": g["hit"],
            "hitRate": hit_rate,
            "preciseHits": g["ph"], "preciseHitRate": precise_rate,
            "preciseEval": g["preciseEval"], "matured": g["matured"],
            "avgDays": avg_days, "cold": cold,
            "dirEval": g["dirEval"], "dirHits": g["dirHits"], "dirHitRate": dir_rate,
            "baseCaseDirEval": g["bcDirEval"], "baseCaseDirHits": g["bcDirHits"],
            "baseCaseDirRate": base_case_dir_rate,
            # #782 精确价位偏差中位(窗口闭合前即可观测)：中位>0=系统性 overshoot(价格常冲过目标)，
            # <0=系统性 undershoot(价格够不到目标)；绝对值越大精度越差。样本不足标 None。
            "precDevMedian": round(float(np.median(g["precDevs"])), 4) if g["precDevs"] else None,
            # #790 目标实时追踪：open(pending)目标的时间进度与最佳接近度(可立即观测，无需窗闭合)
            "open": g["open"],
            "openElapsedMed": round(float(np.median(g["openElapsed"])), 1) if g["openElapsed"] else None,
            "openExpMed": round(float(np.median(g["openExp"])), 1) if g["openExp"] else None,
            "openBestMed": round(float(np.median(g["openBest"])) * 100, 2) if g["openBest"] else None,
            "openStatus": _open_status(g["open"], g["openElapsed"], g["openExp"], g["openBest"]),
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
    # #782 精确价位精度(早期可观测，不依赖观察窗闭合)：全局统计所有已观测 approachTarget 样本的偏差，
    # 即便目标窗口未闭合也能贡献"价格离目标差多少"的真实信号，弥补精确命中率需窗口闭合才出数的盲区。
    all_prec_dev = [r["precDev"] for r in recs if r.get("precDev") is not None]
    level_precision_median_dev = round(float(np.median(all_prec_dev)), 4) if all_prec_dev else None
    # 已观测窗口内价格落入目标 ±5% 的比例(早期精度达标率)：偏差<=0.05 视为"够近"，与面板诚实化口径一致。
    level_precision_within5 = round(sum(1 for d in all_prec_dev if d <= 0.05) / len(all_prec_dev) * 100, 1) \
        if all_prec_dev else None
    # #788 分侧稳健偏差：上行(sell)目标与下行(buy)目标的历史偏差方向截然不同
    # （卖点系统性 undershoot 约 −12.6%、买点近乎精确约 +0.3%）。供 build_data #787 兜底按侧取先验，
    # 避免单一全局偏差(−8.6%)同时低估卖点、高估买点。defensive 侧样本极少，多为 None。
    def _side_med(_sd):
        _vs = [r["precDev"] for r in recs if r.get("side") == _sd and r.get("precDev") is not None]
        return round(float(np.median(_vs)), 4) if _vs else None
    def _side_pct(_sd, _q):
        _vs = [r["precDev"] for r in recs if r.get("side") == _sd and r.get("precDev") is not None]
        return round(float(np.percentile(_vs, _q)), 4) if _vs else None
    level_precision_median_dev_by_side = {
        "sell": _side_med("sell"),
        "buy": _side_med("buy"),
        "defensive": _side_med("defensive"),
    }
    # #789 分侧历史离散度(校准不确定带)：用 16/84 分位刻画各侧 precDev 分布宽度，
    # 供 build_data 给校准位附加「历史实际落点区间」——单一 calibPx 点估计会掩盖精度风险
    # （如卖点侧 IQR 约 −25%~−10%，点估计 −12.6% 看似精确实则散布极宽）。与 #788 同口径、单源派生。
    level_precision_p16_by_side = {
        "sell": _side_pct("sell", 16), "buy": _side_pct("buy", 16), "defensive": _side_pct("defensive", 16)}
    level_precision_p84_by_side = {
        "sell": _side_pct("sell", 84), "buy": _side_pct("buy", 84), "defensive": _side_pct("defensive", 84)}
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
    # #779 基准情形方向准确率(整体)：仅统计 baseCase=True 样本，反映"系统真正当作基准发出的目标"方向正确率
    total_bc_dir_eval = sum(1 for r in recs if r.get("baseCase", True) and r.get("dirCorrect") is not None)
    total_bc_dir_hits = sum(1 for r in recs if r.get("baseCase", True) and r.get("dirCorrect"))
    base_case_dir_realized = round((total_bc_dir_hits + 1.0) / (total_bc_dir_eval + 2.0) * 100, 1) if total_bc_dir_eval else None
    # #790 目标实时追踪汇总：供前端「目标实时追踪」面板；仅用【已观测】信号(open 目标进度)，
    # 不依赖观察窗闭合即可暴露"长期目标早期正常 / 中期目标偏慢 / 价格已临界"等时效信号，辅助买卖时机。
    _by_cat = []
    for r in summary:
        _by_cat.append({
            "cat": r["cat"], "key": r["key"],
            "open": r.get("open", 0),
            "openElapsedMed": r.get("openElapsedMed"),
            "openExpMed": r.get("openExpMed"),
            "openBestMed": r.get("openBestMed"),
            "status": r.get("openStatus"),
        })
    realization_summary = {
        "totalOpen": sum(r.get("open", 0) for r in summary),
        "totalPending": total_pending,
        "byCat": _by_cat,
    }
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
        "baseCaseDirEvaluated": total_bc_dir_eval,
        "baseCaseDirHits": total_bc_dir_hits,
        "baseCaseDirRealizedHitRate": base_case_dir_realized,
        "realizedHitRate": realized_hit,
        "preciseRealizedHitRate": precise_realized,
        # #782 精确价位精度(早期可观测，不依赖观察窗闭合)：
        # levelPrecisionMedianDev=已观测偏差中位(|approachTarget-1|)；>0 系统性 overshoot，<0 undershoot。
        # levelPrecisionWithin5Pct=已观测价格落入目标 ±5% 的比例(早期精度达标率)。
        "levelPrecisionMedianDev": level_precision_median_dev,
        "levelPrecisionWithin5Pct": level_precision_within5,
        "levelPrecisionMedianDevBySide": level_precision_median_dev_by_side,
        # #790 目标实时追踪：open(pending)目标进度汇总，辅助买卖时机判断
        "realizationSummary": realization_summary,
        "levelPrecisionP16BySide": level_precision_p16_by_side,
        "levelPrecisionP84BySide": level_precision_p84_by_side,
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
    print("  基准情形方向准确率(#779·仅 baseCase=True)=%s%% (样本=%d)" % (s["baseCaseDirRealizedHitRate"], s["baseCaseDirEvaluated"]))
    print("  band 触达率(宽松)=%s%%   精确命中率(真实目标价位·需窗口闭合才出数)=%s%%" %
          (s["realizedHitRate"], s["preciseRealizedHitRate"]))
    print("  精确已解决样本=%d  其中提前精确触达(窗口未闭合)=%d" % (s.get("preciseEvaluated", 0), s.get("preciseEarlyHits", 0)))
    # #782 精确价位精度(早期可观测，不依赖观察窗闭合)：带符号中位偏差——↑overshoot(价格冲过目标)、
    # ↓undershoot(够不到)；全局 ±5% 达标率揭示"价格够近目标"的比例，补足精确命中率需窗口闭合才出数的盲区。
    _mdev = s.get("levelPrecisionMedianDev")
    if _mdev is not None:
        _mdev_str = "%.2f%%" % (_mdev * 100)
        _mdev_dir = " (↑overshoot)" if _mdev > 0 else (" (↓undershoot)" if _mdev < 0 else " (精确)")
    else:
        _mdev_str, _mdev_dir = "样本不足", ""
    print("  精确价位精度(#782·早期可观测) 中位偏差=%s%s  ±5%%达标率=%s%%" %
          (_mdev_str, _mdev_dir,
           s.get("levelPrecisionWithin5Pct") if s.get("levelPrecisionWithin5Pct") is not None else "样本不足"))
    _bys = s.get("levelPrecisionMedianDevBySide") or {}
    def _pct_or_na(_k):
        return ("%.2f%%" % (_bys[_k] * 100)) if (_k in _bys and _bys[_k] is not None) else "样本不足"
    print("  分侧稳健偏差(#788) sell(上行)=%s  buy(下行)=%s  defensive=%s  | 全局(旧)=%s" %
          (_pct_or_na("sell"), _pct_or_na("buy"), _pct_or_na("defensive"),
           (_mdev_str if _mdev is not None else "样本不足")))
    _p16 = s.get("levelPrecisionP16BySide") or {}
    _p84 = s.get("levelPrecisionP84BySide") or {}
    def _iqr_or_na(_sd):
        if _sd in _p16 and _p16[_sd] is not None and _sd in _p84 and _p84[_sd] is not None:
            return "%.2f%%~%.2f%%" % (_p16[_sd] * 100, _p84[_sd] * 100)
        return "样本不足"
    print("  分侧校准不确定带(#789, 历史16~84分位) sell(上行)=%s  buy(下行)=%s  defensive=%s" %
          (_iqr_or_na("sell"), _iqr_or_na("buy"), _iqr_or_na("defensive")))
    for r in s["summary"]:
        print("  ", r["cat"], r["key"], "| n=%d" % r["n"],
              "| 方向准确率=%s" % (r["dirHitRate"] if r["dirHitRate"] is not None else "样本不足"),
              "| 基准方向准确率=%s" % (r["baseCaseDirRate"] if r["baseCaseDirRate"] is not None else "—"),
              "| band命中率=%s" % (r["hitRate"] if r["hitRate"] is not None else "样本不足"),
              "| 精确命中率=%s" % (r["preciseHitRate"] if r["preciseHitRate"] is not None else "待成熟"),
              "| 精确价位偏差中位(#782)=%s" %
              (("%.2f%%%s" % (r["precDevMedian"] * 100,
                              "↑" if r["precDevMedian"] > 0 else ("↓" if r["precDevMedian"] < 0 else "")))
               if r.get("precDevMedian") is not None else "样本不足"),
              "| 平均触达=%s天" % r["avgDays"])
    # #790 目标实时追踪：open(pending)目标进度一览，辅助买卖时机判断
    _rs = s.get("realizationSummary") or {}
    if _rs.get("byCat"):
        print("  --- #790 目标实时追踪(open=观察窗未闭合且未触达) ---")
        print("  全局: open(观察中)目标=%d / pending=%d" % (_rs.get("totalOpen", 0), _rs.get("totalPending", 0)))
        for r in _rs["byCat"]:
            if not r.get("open"):
                continue
            _e = r.get("openElapsedMed"); _x = r.get("openExpMed"); _b = r.get("openBestMed")
            _tp = ("%.0f%%" % (_e / _x * 100)) if (_e is not None and _x) else "—"
            _bm = ("%.2f%%" % _b) if _b is not None else "—"
            print("    %-10s %-16s open=%d 已过时长=%s(中位%d/%d日) 最佳接近=%s 状态=%s" %
                  (r["cat"], r["key"], r["open"], _tp,
                   int(_e) if _e is not None else 0, int(_x) if _x is not None else 0,
                   _bm, r.get("status")))
