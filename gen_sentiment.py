# -*- coding: utf-8 -*-
"""市场情绪温度引擎（R87 借鉴缠论 sentiment/ 模块，做 A股斐波那契适配版）。

定位：monitor_only —— 情绪温度只用于「研判提示 + 面板展示」，绝不并入概率/方向预测主模型。

为什么不照搬缠论（缠论用「涨跌家数原始 txt + 成交额/换手率」）：
  - 涨跌家数 / 涨停跌停家数只有 eastmoney 中国本地源，GitHub 海外 runner 不可达（R271 根因），
    且 yahoo/stooq 无对应回退 → 照搬会在 CI 上「每天失败、靠兜底显示旧值」，是假绿/噪音。
  - 本仓 datafeed 抓的是指数日线 OHLCV，无成交额(amount)/换手率(to)，无法复刻缠论量能/换手指标。
  - 因此改用「本仓已有单一真源 data/data.js」透明推导一个代理情绪温度（见下方五维），
    不新增第三方接口、不破坏 sole-writer、CI 每天稳定可算。这是对 R85 反假绿纪律的坚守。

五维合成（各维度先映射到 [-1,+1] 子分，加权求和 → 0~100）：
  1. 趋势动量   w=0.30  上证 ret20（20 日涨跌幅），clamp(ret20/8)
  2. 牛熊位置   w=0.20  (lastClose - MA250)/MA250，clamp(dev/0.15)
  3. 量能水平   w=0.20  上证 vol20/vol250 - 1（放量=活跃，缩量=冷清），clamp(ratio/0.5)
  4. 波动恐慌   w=0.15  volRegime.pctile（HV20 五年分位；高波动=恐慌=降温），1 - pctile/50
  5. 广度确认   w=0.15  (宽基 resonance.breadth + 跨市场 crossMarket.breadth)/2

五档定性：<20 冰点 / 20-40 偏冷 / 40-60 中性 / 60-80 偏热 / ≥80 狂热。

诚实标注：这是「量能/动量/波动/广度」合成的代理情绪温度，非全市场涨跌家数；
  且量能维度受跨源 volume 单位差异影响，仅作相对参考。退出码恒 0（软，透明化不阻断）。
"""
import os
import sys
import json
import re
import datetime

REPO = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(REPO, "data", "data.js")
OUT_JSON = os.path.join(REPO, "data", "sentiment.json")


def _load_data():
    with open(DATA_JS, encoding="utf-8") as f:
        src = f.read()
    return json.loads(re.search(r"window\.FIB_DATA\s*=\s*(\{.*\})\s*;?\s*$", src, re.S).group(1))


def _ma(arr, w):
    if len(arr) < w:
        return None
    return sum(arr[-w:]) / w


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _f(x, default=0.0):
    """防御性 float 转换：None/非数值 → default（build_data 正常给数值，此处防降级时 None 崩溃）。"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _label(score):
    if score < 20:
        return "冰点"
    if score < 40:
        return "偏冷"
    if score < 60:
        return "中性"
    if score < 80:
        return "偏热"
    return "狂热"


def _compute(D):
    """返回 (score, label, dims_list) 或 None（数据不足）。dims_list 元素 = (name, weight, sub, meta)。"""
    k = D.get("kline") or {}
    closes = [float(x) for x in (k.get("close") or [])]
    vols = [float(x) for x in (k.get("volume") or [])]
    if len(closes) < 250:
        return None

    last = closes[-1]
    ma250 = _ma(closes, 250)

    # 1. 趋势动量（上证 20 日涨跌幅）
    ret20 = (closes[-1] / closes[-21] - 1.0) * 100.0 if len(closes) >= 21 else 0.0
    sub_mom = _clamp(ret20 / 8.0, -1.0, 1.0)

    # 2. 牛熊位置（相对 250 日均线偏离）
    pos = (last / ma250 - 1.0) if ma250 else 0.0
    sub_pos = _clamp(pos / 0.15, -1.0, 1.0)

    # 3. 量能水平（20 日 / 250 日均量比值）
    vol20 = _ma(vols, 20)
    vol250 = _ma(vols, 250)
    vr = (vol20 / vol250 - 1.0) if (vol20 and vol250) else 0.0
    sub_vol = _clamp(vr / 0.5, -1.0, 1.0)

    # 4. 波动恐慌（HV20 五年分位，高波动=恐慌=降温）
    pctile = _f((D.get("volRegime") or {}).get("pctile"), 50.0)
    sub_volat = _clamp(1.0 - pctile / 50.0, -1.0, 1.0)

    # 5. 广度确认（宽基共振 + 跨市场共振 平均）
    res_b = _f((D.get("resonance") or {}).get("breadth"), 0.0)
    cross_b = _f((D.get("crossMarket") or {}).get("breadth"), 0.0)
    sub_breadth = _clamp((res_b + cross_b) / 2.0, -1.0, 1.0)

    dims = [
        ("momentum", 0.30, sub_mom, {"ret20_pct": round(ret20, 2)}),
        ("position", 0.20, sub_pos, {"lastClose": round(last, 2),
                                     "ma250": round(ma250, 2) if ma250 else None,
                                     "dev_pct": round(pos * 100.0, 2)}),
        ("volume", 0.20, sub_vol, {"vol20_vol250_ratio": round(vr, 3)}),
        ("volatility", 0.15, sub_volat, {"hv20_pctile": int(pctile)}),
        ("breadth", 0.15, sub_breadth, {"domestic": round(res_b, 3),
                                        "cross_market": round(cross_b, 3)}),
    ]
    score = 50.0 + 50.0 * sum(w * s for (_n, w, s, _meta) in dims)
    score = _clamp(score, 0.0, 100.0)
    return score, _label(score), dims


def main():
    out = {
        "schema": "a-share-fib-sentiment/v1",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "monitor_only",
        "error": None,
    }
    try:
        D = _load_data()
        out["asOf"] = D.get("fetchedAt") or D.get("updated")
        r = _compute(D)
        if r is None:
            out["score"] = None
            out["label"] = "数据不足"
            out["note"] = "样本 < 250 日，暂不合成情绪温度（monitor_only）。"
        else:
            score, label, dims = r
            out["score"] = round(score, 1)
            out["label"] = label
            out["dims"] = [
                {"name": n, "weight": w, "sub": round(s, 4), **m}
                for (n, w, s, m) in dims
            ]
            out["note"] = ("代理情绪温度（monitor_only）：由上证量能/动量/波动/牛熊位置 + "
                           "宽基与跨市场广度合成，非全市场涨跌家数；量能维度受跨源 volume 单位差异影响，"
                           "仅供研判参考，不参与任何概率/方向计算。")
    except Exception as e:  # 降级：在场但不失真，绝不阻断当日推送
        out["error"] = "gen_sentiment 异常: %s" % e
        out["score"] = None
        out["label"] = "数据不足"
    finally:
        os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        with open(OUT_JSON.replace(".json", ".js"), "w", encoding="utf-8") as f:
            f.write("window.SENTIMENT = ")
            json.dump(out, f, ensure_ascii=False)
            f.write(";")

    print("=== 市场情绪温度 (R87 · monitor_only) ===")
    print("asOf      = %s" % out.get("asOf"))
    print("score     = %s  label=%s" % (out.get("score"), out.get("label")))
    if out.get("dims"):
        for d in out["dims"]:
            print("  %-10s w=%.2f sub=%+.3f  %s" % (d["name"], d["weight"], d["sub"],
                                                     json.dumps({kk: vv for kk, vv in d.items()
                                                                 if kk not in ("name", "weight", "sub")},
                                                                ensure_ascii=False)))
    if out.get("error"):
        print("⚠️ %s" % out["error"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
