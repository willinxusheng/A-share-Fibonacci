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
  4. 波动恐慌   w=0.15  HV20 的五年分位（高波动=恐慌=降温），1 - pctile/50
  5. 广度确认   w=0.15  仅用 breadthAvailable>0 的可用源（缺失源不参与、不拖累，R141）；
                          当前唯一可用源为 crossMarket.breadth（恒生/标普/纳指方向系数均值 ∈[0,1]，
                          本质『全球风险偏好同向确认度』），(b-0.5)*2 映射回 [-1,1] 信号维度（R141）；
                          非 A股内部涨跌家数（R271 约束仍不可破，故语义明确为跨市场广度）。

输出（schema a-share-fib-sentiment/v3）：
  - today   : 当日单点快照（含 5 维 dims），保持 R87 既有契约。
  - history : 近 250 个交易日逐日回算的历史情绪序列（date, score, label）。
  - forecast: 由 subForecast 价格路径派生（斐波那契路径映射）的未来情绪预测序列（date, score, label）。
              预测段无量能/波动/广度未来因子，故量能/波动随预测 horizon 指数回归历史中枢（波动率均值回归，
              不再恒用今日值钉死全程）、广度沿用当前均值，仅「动量+牛熊位置」随价格路径变化 —— 这是路径派生
              预测，非因子预测，已在 note 中诚实标注。

五档定性（R122a 动态分位标尺）：基于 history 分布 p10/p30/p70/p90 切五档（冰点/偏冷/中性/偏热/狂热）；
  分布退化时回退固定 <20 / 20-40 / 40-60 / 60-80 / ≥80。today/history/forecast 共用同一标尺。

R123 预测力增强（不改水平口径，仅加派生信号）：
  - 情绪变化 Δ：每段历史算 5/20 日情绪差，并做『升温 vs 降温』分组未来20日收益统计
    （实证：情绪越涨越慎/越跌越贪，Δ 比水平更会预测未来收益）。
  - 滚动 z 分位：近120日均值/标准差归一化，标出『当前情绪相对自身近期有多极端』，跨 regime 可比。
  - 分 regime 信号强度直读：复用熊/牛分态统计，当前 regime 下一句话给信号强弱结论。

R124 预测力再增强（纯诊断字段，不改水平/今日展示口径，不增维度）：
  - 最优预测窗口扫描：按 score 中位数切冷/热组，扫描 H∈{5,10,20,40,60} 未来H日收益 spread，
    选 |spread| 最大且样本充足者为最优逆势解读窗口。
  - 组合状态信号：由『当前情绪水平档 × 当前Δ方向』定位四象限，给出该格经验未来20日收益与
    经验上涨概率（样本频率，非拟合），刻画『高位升温/低位降温』等组合时机。
  - 近期权重稳健性对照：对当前档比较等权 vs 近1年指数衰减加权未来20日均值，
    显著背离即提示 regime 漂移、信号稳健性下降。

诚实标注：这是「量能/动量/波动/广度」合成的代理情绪温度，非全市场涨跌家数；
  且量能维度受跨源 volume 单位差异影响，仅作相对参考。广度维度当前语义为『跨市场（全球）风险偏好
  同向确认度』（恒生/A股港股通/标普/纳指方向系数），非 A股内部涨跌家数——R271 涨跌家数数据源约束
  仍不可破，故广度信号由海外可达的 crossMarket 源承载并诚实标注（R141）。退出码恒 0（软，透明化不阻断）。

R142 分regime滚动分位归一抗漂移：熊/牛regime情绪中枢系统性差~19点(bear 42.6/bull 61.7)，全局分位在
  牛熊切换时语义失真；故对今日与history逐点改算『同regime近250日滚动分位 regimePct』（仅与同regime
  样本比，抗中枢系统差），并据此调制R140 regime水平位移权重（今日已处regime极端区则减力防过度修正），
  使预测在牛熊切换时更稳；history逐点含regimePct、today含regimePct，forecastBand.regimePctileToday/
  regimePctileMethod/levelWMod 透明可读。
"""
import os
import sys
import json
import re
import datetime
import math

REPO = os.path.dirname(os.path.abspath(__file__))
DATA_JS = os.path.join(REPO, "data", "data.js")
OUT_JSON = os.path.join(REPO, "data", "sentiment.json")

N_HISTORY = 0  # 历史回算窗口：0=全部可用（自第 250 根起有完整 MA250/vol250 预热，约 5 年样本）


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


def _f(x, default= 0.0):
    """防御性 float 转换：None/非数值/NaN/Inf → default（build_data 正常给数值，
    此处防降级时 None 崩溃，且 float('nan')/float('inf') 不会抛异常会穿透，
    必须用 math.isfinite 守门，否则 NaN 会污染 score 并令 round(score,1) 抛 ValueError）。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _label(score, bounds=None):
    """五档定性（R122a 动态分位标尺）。

    bounds=[b1,b2,b3,b4] 时按分位标尺切五档：
      冰点 < b1 / 偏冷 b1~b2 / 中性 b2~b3 / 偏热 b3~b4 / 狂热 >= b4。
    缺省 bounds 回退固定 [20,40,60,80]，保证未传动态标尺时仍可用（兼容旧调用）。
    """
    if bounds is None:
        bounds = [20.0, 40.0, 60.0, 80.0]
    b1, b2, b3, b4 = bounds
    if score < b1:
        return "冰点"
    if score < b2:
        return "偏冷"
    if score < b3:
        return "中性"
    if score < b4:
        return "偏热"
    return "狂热"


def _scale_bounds(scores):
    """基于 history score 分布动态分位标尺（R122a）。

    返回 (bounds, mode, pct)：
      bounds = [b1,b2,b3,b4] 对应 p10/p30/p70/p90，将 [0,100] 切成五档。
      固定阈值下历史 score 多落在 25~85，冰点(<20)/狂热(>=80) 几乎永不触发，
      动态标尺令各档更具区分度，且 today/forecast/history 同标尺，避免错位。
      mode='fixed' 表示样本不足(<30)或分布退化（分位塌缩/b1<=0/b4>=100），回退固定标尺，保证稳健。
    """
    if len(scores) < 30:
        return [20.0, 40.0, 60.0, 80.0], "fixed", None
    s = sorted(scores)

    def _pct(p):
        if not s:
            return 50.0
        k = (len(s) - 1) * p / 100.0
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return s[int(k)]
        return s[int(f)] * (c - k) + s[int(c)] * (k - f)

    b1, b2, b3, b4 = _pct(10), _pct(30), _pct(70), _pct(90)
    if not (b1 < b2 < b3 < b4) or b1 <= 0 or b4 >= 100:
        return [20.0, 40.0, 60.0, 80.0], "fixed", None
    return [round(b1, 1), round(b2, 1), round(b3, 1), round(b4, 1)], "dynamic", (b1, b2, b3, b4)


def _breadth_sub(D):
    """广度维度子分（R122c → R141 升级）：仅用 breadthAvailable>0 的可用源，缺失源不参与平均，
    全缺失时归零不偏置（不会把「缺失占位 0.0」当真实值拉低）。

    R141 关键修正（诚实突破 R271 约束的可用边界）：
      - 此前把 resonance(缺失,avail=0) 与 crossMarket(可用,avail=4) 直接均值 → (0+1.0)/2=0.5，
        缺失源「拖累」了唯一可用的真实广度信号，导致其权重被稀释一半；
      - 现改为「仅可用源均值」(parts 不含缺失源的 0.0 占位)，今天 = 仅 crossMarket → 1.0，忠实表达。
      - crossMarket.breadth 是 4 个海外指数(恒生/恒生科技/标普/纳指)近20日方向系数的均值 ∈[0,1]，
        本质是「全球风险偏好同向确认度」——这是海外 CI 每日可达的真实离散信号（非 A股内部涨跌家数，
        R271 涨跌家数约束仍不可破，故广度语义明确标注为『跨市场』而非『全市场』）。

    返回 (sub_raw, avail, dom_val, cross_val, src_tags)：
      avail    = 实际参与平均的源数（0/1/2）
      dom_val  = resonance.breadth 真实值，缺失源为 None（不污染 JSON 契约）
      cross_val= crossMarket.breadth 真实值，缺失源为 None
      src_tags = 参与源语义标签列表（如 ['cross'] / ['dom','cross']），供 dims meta 诚实标注
    """
    res = D.get("resonance") or {}
    cross = D.get("crossMarket") or {}
    ra = _f(res.get("breadthAvailable"))
    ca = _f(cross.get("breadthAvailable"))
    parts = []
    src_tags = []
    if ra > 0:
        parts.append(_f(res.get("breadth")))
        src_tags.append("dom")
    if ca > 0:
        parts.append(_f(cross.get("breadth")))
        src_tags.append("cross")
    if parts:
        sub = sum(parts) / len(parts)
        avail = len(parts)
    else:
        sub = 0.0
        avail = 0
    dom_val = _f(res.get("breadth")) if ra > 0 else None
    cross_val = _f(cross.get("breadth")) if ca > 0 else None
    return _clamp(sub, -1.0, 1.0), avail, dom_val, cross_val, src_tags


def _breadth_adaptive(sub_breadth, src_tags):
    """R141：广度数据驱动归一化（替代 R138 的『常量中心化归零』）。

    crossMarket.breadth ∈[0,1] 是 4 个海外指数方向系数均值，有真实离散度：
      0 = 4 个市场全部 20 日下跌（全球风险厌恶）→ 应映射为 -1（广度冰点）；
      1 = 4 个市场全部 20 日上涨（全球风险偏好同向）→ 应映射为 +1（广度沸点）；
      0.5 = 分歧（一半涨一半跌）→ 0（中性）。
    映射 (b-0.5)*2 把它从「代理常量」变回「真实信号维度」，恢复其 15% 权重的真实贡献；
    共振源(resonance)缺失时仅用 cross，dom 可用时两者均值后再映射（语义一致）。
    仅当两源全缺失(avail=0) 才回退 0（诚实归零，不伪造）。
    """
    if not src_tags:
        return 0.0  # 全缺失：诚实归零，不偏置
    return _clamp((sub_breadth - 0.5) * 2.0, -1.0, 1.0)


def _is_a_share_trading_day(d):
    """A股交易日：工作日或调休补班日，且非法定休市。

    与 build_data.py R207 的 _next_trading_day 口径同源（上交所 上证公告〔2025〕45号）：
    仅列连续休市区间（周末本就休市，不重复）；补班日虽在周末仍交易。
    2027 安排待公布后补充。用于 forecast 插值后剔除长假/周末、保留补班日，避免非交易日假数据点。
    """
    dt = datetime.datetime.strptime(d, "%Y-%m-%d")
    ds = dt.strftime("%Y-%m-%d")
    if ds in _A_SHARE_MAKEUP:
        return True
    if dt.weekday() >= 5:
        return False
    if ds in _A_SHARE_HOLIDAYS:
        return False
    return True


def _expand_holidays(start, end):
    out = set()
    a = datetime.datetime.strptime(start, "%Y-%m-%d")
    b = datetime.datetime.strptime(end, "%Y-%m-%d")
    while a <= b:
        out.add(a.strftime("%Y-%m-%d"))
        a += datetime.timedelta(days=1)
    return out


# R207 口径（与 build_data.py / report.py / index.html _MAKEUP_2026 同源）
_A_SHARE_HOLIDAYS = (
    _expand_holidays("2026-01-01", "2026-01-03")
    | _expand_holidays("2026-02-15", "2026-02-23")
    | _expand_holidays("2026-04-04", "2026-04-06")
    | _expand_holidays("2026-05-01", "2026-05-05")
    | _expand_holidays("2026-06-19", "2026-06-21")
    | _expand_holidays("2026-09-25", "2026-09-27")
    | _expand_holidays("2026-10-01", "2026-10-07")
)
_A_SHARE_MAKEUP = {"2026-02-14", "2026-02-28", "2026-05-09", "2026-09-20", "2026-10-10"}


def _score_from_subs(subs):
    """subs = (sub_mom, sub_pos, sub_vol, sub_volat, sub_breadth)，权重固定。"""
    w = [0.30, 0.20, 0.20, 0.15, 0.15]
    score = 50.0 + 50.0 * sum(wi * s for wi, s in zip(w, subs))
    return _clamp(score, 0.0, 100.0)


def _pctile_rank(value, window):
    """value 在 window（数值列表）中的分位 [0,100]，>=value 的占比。window 为空返回 50。"""
    if not window:
        return 50.0
    cnt = sum(1 for x in window if x <= value)
    return 100.0 * cnt / len(window)


def _regime_rolling_pct(scores, regimes, i, window=250):
    """R142 分 regime 滚动分位归一：第 i 个 score 在『其自身 regime 的滚动窗口』内的分位 [0,100]。

    熊/牛 regime 的情绪中枢系统性差 ~19 点（bear 42.6 / bull 61.7），若用全局分位解读/锚定，
    牛熊切换时同一绝对 score 的语义会失真（熊市态 50 分已偏热、牛市态 50 分仍偏冷）。
    故改为：仅取滚动窗口内『与当日同 regime』的样本，算该 score 在『同 regime 近期分布』中的
    分位——直接回答『当前情绪相对当下牛熊态自身有多极端』，抗 regime 漂移、切换更稳。
    regimes[i] 为 True 表示当日为 bear 态（价<MA250），与 _regime_center 口径一致。
    窗口不足或同 regime 样本为空时回退 50（中性，不偏置）。
    """
    if i < 0 or i >= len(scores):
        return 50.0
    lo = max(0, i - window + 1)
    _same = [scores[j] for j in range(lo, i + 1) if regimes[j] == regimes[i]]
    if not _same:
        return 50.0
    cnt = sum(1 for x in _same if x <= scores[i])
    return 100.0 * cnt / len(_same)


def _volume_ratio_series(closes, vols):
    """全样本 vol20/vol250-1 序列（R138 数据驱动量能归一化用）。

    与 _compute_today / _compute_history 内部口径完全一致（20 日/250 日均量比-1），
    供一次性计算全样本 σ，避免固定除数 (vr/0.5) 与真实离散度失配导致量能维度
    在其真实动态区间被压缩、长期欠用其 20% 权重。
    """
    out = []
    for i in range(len(closes)):
        if i < 249:
            continue
        vol20 = _ma(vols[: i + 1], 20)
        vol250 = _ma(vols[: i + 1], 250)
        vr = (vol20 / vol250 - 1.0) if (vol20 is not None and vol250 is not None) else 0.0
        out.append(vr)
    return out


def _adaptive_volume_divisor(closes, vols):
    """R138：量能归一化除数 = 全样本 vol-ratio 标准差（σ），下限钳制防退化。

    固定除数 0.5 相对真实 σ≈0.23 放大 ~2.2×，使 sub_vol 长期蜷缩在正窄带、
    极少触达 ±1，量能维度实际贡献远低于其 20% 权重。改用 σ 后 sub_vol 在其真实
    离散范围内展开，权重利用率与动量/位置维度对齐。下限 0.08 防极端低波动样本
    把微小噪声放大成满量程（与 R127b base_std 同思路的钳制纪律）。
    """
    s = _volume_ratio_series(closes, vols)
    if len(s) < 30:
        return 0.5  # 样本不足回退固定除数（口径降级，不崩）
    mu = sum(s) / len(s)
    sd = (sum((x - mu) ** 2 for x in s) / len(s)) ** 0.5
    return max(0.08, sd)


def _breadth_centered(sub_breadth, breadth_mean):
    """R141：广度数据驱动归一化入口（替代 R138 常量中心化）。

    R138 时期广度是常数 +1.0（std=0），中心化归零移除静态偏置；但 R141 已把
    crossMarket.breadth（海外可达、每日刷新、∈[0,1] 的真实离散信号）真正启用，
    常量退化不再成立，故改走 _breadth_adaptive（(b-0.5)*2 映射回 [-1,1] 信号维度）。
    保留此函数签名兼容，但直接委托 _breadth_adaptive；breadth_mean 参数不再使用
    （历史均值中心化语义已由数据驱动映射取代），保留仅防旧调用断链。
    """
    return _breadth_adaptive(sub_breadth, getattr(sub_breadth, "_src_tags", ["cross"]))


def _compute_today(D):
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

    # 3. 量能水平（20 日 / 250 日均量比值）— R138 数据驱动除数（全样本 σ）替代固定 0.5
    vol20 = _ma(vols, 20)
    vol250 = _ma(vols, 250)
    vr = (vol20 / vol250 - 1.0) if (vol20 is not None and vol250 is not None) else 0.0
    _vdiv = _adaptive_volume_divisor(closes, vols)
    sub_vol = _clamp(vr / _vdiv, -1.0, 1.0)

    # 4. 波动恐慌（HV20 在「截至当日向前 250 日 HV 分布」中的分位，高波动=恐慌=降温）。
    # 与 _compute_history 末点口径一致，保证当日点与历史曲线平滑衔接；不足时回退 volRegime.pctile。
    hv = _daily_hv_series(closes)
    win = [h for h in hv[max(0, len(closes) - 250):] if h is not None]
    pctile = _pctile_rank(hv[-1], win) if hv[-1] is not None else _f((D.get("volRegime") or {}).get("pctile"), 50.0)
    sub_volat = _clamp(1.0 - pctile / 50.0, -1.0, 1.0)

    # 5. 广度确认（R122c → R141：仅可用源均值，缺失源不参与平均、不拖累可用源）：
    #    R141 把海外可达的 crossMarket.breadth 真正启用（(b-0.5)*2 映射回 [-1,1] 信号维度），
    #    突破 R138『常量退化归零』的保守处理；共振源(resonance)缺失时不稀释跨市场源。
    #    诚实标注：crossMarket.breadth 是『全球风险偏好同向确认度』（恒生/标普/纳指方向系数均值），
    #    非 A股内部涨跌家数（R271 涨跌家数约束仍不可破），故语义明确为『跨市场广度』。
    sub_breadth_raw, bw_avail, res_b_real, cross_b_real, bw_tags = _breadth_sub(D)
    sub_breadth = _breadth_adaptive(sub_breadth_raw, bw_tags)

    dims = [
        ("momentum", 0.30, sub_mom, {"ret20_pct": round(ret20, 2)}),
        ("position", 0.20, sub_pos, {"lastClose": round(last, 2),
                                     "ma250": round(ma250, 2) if ma250 else None,
                                     "dev_pct": round(pos * 100.0, 2)}),
        ("volume", 0.20, sub_vol, {"vol20_vol250_ratio": round(vr, 3)}),
        ("volatility", 0.15, sub_volat, {"hv20_pctile": int(pctile)}),
        ("breadth", 0.15, sub_breadth, {"available": bw_avail,
                                        "sources": bw_tags,
                                        "domestic": (round(res_b_real, 3) if res_b_real is not None else None),
                                        "cross_market": (round(cross_b_real, 3) if cross_b_real is not None else None),
                                        "note": ("仅跨市场广度可用（共振宽基缺失：广度=全球风险偏好同向确认度，非A股涨跌家数）"
                                                 if bw_avail == 1 else
                                                 "两源均可用（共振宽基+跨市场；广度含全球风险偏好确认维度）"
                                                 if bw_avail >= 2 else "全源缺失：广度诚实归零")}),
    ]
    score = round(_score_from_subs([s for (_n, _w, s, _m) in dims]), 1)
    return score, dims


def _daily_hv_series(closes):
    """逐日 HV20：以收盘日收益率为基础，20 日滚动标准差年化（近似用 sqrt(252)*stdev）。"""
    hv = [None] * len(closes)
    for i in range(1, len(closes)):
        if i < 20:
            continue
        rets = [(closes[j] / closes[j - 1] - 1.0) for j in range(i - 19, i + 1)]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        hv[i] = math.sqrt(var) * math.sqrt(252.0)
    return hv


def _compute_history(D):
    """全部可用交易日逐日情绪序列（自第 250 根起，保证 MA250/vol250 完整预热）。

    返回 [{date, score, label}, ...]；数据不足 250 根时返回空。
    """
    k = D.get("kline") or {}
    dates = [str(x) for x in (k.get("dates") or [])]
    closes = [float(x) for x in (k.get("close") or [])]
    vols = [float(x) for x in (k.get("volume") or [])]
    if len(closes) < 250:
        return []

    # R141：广度历史化约束——当前 data/ 仅 crossMarket.breadth 一个海外可达源且为单值快照，
    # 无逐日历史序列（R271 涨跌家数约束不可破），故历史回算中广度维度沿用当日可用快照值，
    # 经 _breadth_adaptive 映射为信号维度（不再 R138 归零）；缺失源不参与。
    sub_breadth, _bw, _dr, _cr, _btags = _breadth_sub(D)
    sub_breadth_hist = _breadth_adaptive(sub_breadth, _btags)

    hv = _daily_hv_series(closes)
    _vdiv = _adaptive_volume_divisor(closes, vols)  # R138 数据驱动量能除数（全样本 σ）

    start = 249  # 首个有完整 MA250/vol250 的点（0-based 索引）
    out = []
    for i in range(start, len(closes)):
        # 动量：20 日涨跌幅（与 _compute_today 的 closes[-1]/closes[-21] 口径一致，即始于 i、回溯 20 交易日）
        ret20 = (closes[i] / closes[i - 20] - 1.0) * 100.0 if i >= 20 else 0.0
        sub_mom = _clamp(ret20 / 8.0, -1.0, 1.0)

        # 牛熊位置
        ma250 = _ma(closes[: i + 1], 250)
        pos = (closes[i] / ma250 - 1.0) if ma250 else 0.0
        sub_pos = _clamp(pos / 0.15, -1.0, 1.0)

        # 量能（R138：数据驱动除数替代固定 0.5）
        vol20 = _ma(vols[: i + 1], 20)
        vol250 = _ma(vols[: i + 1], 250)
        vr = (vol20 / vol250 - 1.0) if (vol20 is not None and vol250 is not None) else 0.0
        sub_vol = _clamp(vr / _vdiv, -1.0, 1.0)

        # 波动：当前 HV20 在「截至当日向前 250 日 HV 分布」中的分位
        win = [h for h in hv[max(0, i - 249): i + 1] if h is not None]
        pctile = _pctile_rank(hv[i], win) if hv[i] is not None else 50.0
        sub_volat = _clamp(1.0 - pctile / 50.0, -1.0, 1.0)

        score = round(_score_from_subs([sub_mom, sub_pos, sub_vol, sub_volat,
                                         sub_breadth_hist]), 1)
        out.append({"date": dates[i], "score": score})
    return out  # 全部可用点（约 5 年），不再截断到 250


def _contra_stats(D, hist, bounds=None, scale_mode="fixed"):
    """逆向信号统计（单一真值，随窗口全量计算）。

    对每个 history 点按 label 分档，统计各档样本数 N 与「未来 20 交易日平均收益」。
    实证：score 与未来收益负相关（逆势信号），且高度 regime 依赖——
      价格 < MA250（熊市态）r≈-0.24 显著；价格 > MA250（牛市态）r≈-0.04 不显著。
    故同时输出 split 分态统计（bear/bull），供前端按当前 regime 选择解读口径。
    bounds 为动态分位标尺（R122a），分档随标尺走，保证与 today/history/forecast 同口径。
    返回 {bands, split:{bear:{bands}, bull:{bands}}, regime, scale_mode, note}。
    """
    k = D.get("kline") or {}
    closes = [float(x) for x in (k.get("close") or [])]
    ma250_arr = [x for x in (k.get("ma250") or [])]
    if not hist or len(closes) < len(hist) + 20:
        return None
    base = len(closes) - len(hist)
    if bounds is None:
        bounds = [20.0, 40.0, 60.0, 80.0]
    b1, b2, b3, b4 = bounds
    bands = [("冰点", 0, b1), ("偏冷", b1, b2), ("中性", b2, b3), ("偏热", b3, b4), ("狂热", b4, 101)]

    def _bands_for(pred):
        out = []
        for (lab, lo, hi) in bands:
            n, s = 0, 0.0
            for off, h in enumerate(hist):
                i = base + off
                if lo <= h["score"] < hi and pred(i):
                    j = i + 20
                    if j < len(closes):
                        n += 1
                        s += (closes[j] / closes[i] - 1.0) * 100.0
            out.append({"label": lab, "n": n, "fwd20": round(s / n, 2) if n else None})
        return out

    out_bands = _bands_for(lambda i: True)
    # 分态统计：仅用有 MA250 的点（前 249 根无均线，天然排除）
    bear_bands = _bands_for(lambda i: ma250_arr[i] is not None and closes[i] < ma250_arr[i])
    bull_bands = _bands_for(lambda i: ma250_arr[i] is not None and closes[i] > ma250_arr[i])
    # 当前 regime：末根价 vs MA250
    last = closes[-1]
    last_ma = ma250_arr[-1] if ma250_arr else None
    regime = "bear" if (last_ma is not None and last < last_ma) else "bull"
    return {
        "bands": out_bands,
        "split": {"bear": {"bands": bear_bands}, "bull": {"bands": bull_bands}},
        "regime": regime,
        "scale_mode": scale_mode,
        "note": ("近%d个交易日逐日回算：score 与未来20日收益负相关（逆势信号）；"
                 "N 为该档有未来20日收益的样本数（尾部20日不计），各档合计%d；"
                 "当前分数区间 %.1f~%.1f；分档标尺=%s（R122a）；"
                 "regime 分态：熊市态(价<MA250)信号显著(r≈-0.24)、"
                 "牛市态(价>MA250)信号不显著(r≈-0.04)——当前为%s态，解读应以%s态统计为准") % (
            len(hist), sum(b["n"] for b in out_bands),
            min(h["score"] for h in hist), max(h["score"] for h in hist),
            ("动态分位" if scale_mode == "dynamic" else "固定"),
            "熊市" if regime == "bear" else "牛市", "熊市" if regime == "bear" else "牛市"),
    }


def _contra_delta_stats(D, hist):
    """情绪变化(Δ)预测力统计（R123a）：经典结论——情绪的变化比情绪的水平更会预测未来收益。

    对每个有 Δ20 与未来20日收益的历史点，按 Δ 符号分『升温/降温』两组，
    统计各自未来20日平均收益；并做四象限（高/中/低 水平 × 升/降）细分，
    揭示『高位升温』『低位降温』等极端情形的方向。返回 {rising, falling, quadrants, note}。
    """
    k = D.get("kline") or {}
    closes = [float(x) for x in (k.get("close") or [])]
    if not hist or len(closes) < len(hist) + 20:
        return None
    base = len(closes) - len(hist)
    rising_n = rising_s = rising_up = 0.0
    falling_n = falling_s = falling_up = 0.0
    quad = {}  # (level, dir) -> [n, s, up]
    for off, h in enumerate(hist):
        d = h.get("d20")
        if d is None:
            continue
        i = base + off
        j = i + 20
        if j >= len(closes):
            continue
        f = (closes[j] / closes[i] - 1.0) * 100.0
        up = 1.0 if f >= 0 else 0.0
        if d >= 0:
            rising_n += 1
            rising_s += f
            rising_up += up
        else:
            falling_n += 1
            falling_s += f
            falling_up += up
        lb = "高" if h["score"] >= 60 else ("低" if h["score"] < 40 else "中")
        key = (lb, "升" if d >= 0 else "降")
        q = quad.setdefault(key, [0, 0.0, 0.0])
        q[0] += 1
        q[1] += f
        q[2] += up

    def _mean(n, s):
        return round(s / n, 2) if n else None

    def _pos(n, up):
        return round(up / n, 2) if n else None

    r_rise = _mean(int(rising_n), rising_s)
    r_fall = _mean(int(falling_n), falling_s)
    p_rise = _pos(int(rising_n), rising_up)
    p_fall = _pos(int(falling_n), falling_up)
    quads = [{"level": lv, "dir": dr, "n": v[0], "fwd20": _mean(v[0], v[1]),
              "pos": _pos(v[0], v[2])}
             for (lv, dr), v in sorted(quad.items())]
    note = ("情绪变化(Δ20)预测力：升温组 N=%d 未来20日平均 %.2f%%（经验上涨概率 %.0f%%），"
            "降温组 N=%d 平均 %.2f%%（经验上涨概率 %.0f%%）。"
            % (int(rising_n), r_rise if r_rise is not None else 0.0,
               (p_rise or 0.0) * 100,
               int(falling_n), r_fall if r_fall is not None else 0.0,
               (p_fall or 0.0) * 100))
    if r_rise is not None and r_fall is not None:
        note += ("升温后收益%s降温后，印证『情绪越涨越慎、越跌越贪』的逆向时机信号。"
                 % ("低于" if r_rise < r_fall else "高于"))
    return {"rising": {"n": int(rising_n), "fwd20": r_rise, "pos": p_rise},
            "falling": {"n": int(falling_n), "fwd20": r_fall, "pos": p_fall},
            "quadrants": quads, "note": note}


def _horizon_scan(D, hist, bounds=None):
    """R124a 最优预测窗口扫描：逆向(contrarian)信号在不同预测窗口 H 下强度不同。

    对每个 H∈{5,10,20,40,60}，按 score 中位数切『冷(低)/热(高)』两组，
    统计各组未来 H 日平均收益，spread = 冷组 - 热组（正=逆势有效：低情绪后涨、高情绪后跌）。
    选 |spread| 最大且两组 N 均充足(>=20) 的 H 为『最优预测窗口』。
    全为描述性统计、非拟合；提示『当前最优解读窗口』，但不改变 R123 的 20 日今日展示。
    """
    k = D.get("kline") or {}
    closes = [float(x) for x in (k.get("close") or [])]
    if not hist or len(closes) < len(hist) + 60:
        return None
    base = len(closes) - len(hist)
    scores = [h["score"] for h in hist]
    med = sorted(scores)[len(scores) // 2]
    rows, opt = [], None
    for H in (5, 10, 20, 40, 60):
        cn = cs = hn = hs = 0.0
        for off, h in enumerate(hist):
            i = base + off
            j = i + H
            if j >= len(closes):
                continue
            f = (closes[j] / closes[i] - 1.0) * 100.0
            if h["score"] < med:
                cn += 1
                cs += f
            else:
                hn += 1
                hs += f
        cmean = round(cs / cn, 2) if cn else None
        hmean = round(hs / hn, 2) if hn else None
        spread = round(cmean - hmean, 2) if (cmean is not None and hmean is not None) else None
        rows.append({"horizon": H, "coldN": int(cn), "coldMean": cmean,
                     "hotN": int(hn), "hotMean": hmean, "spread": spread})
        if cn >= 20 and hn >= 20 and spread is not None:
            if opt is None or abs(spread) > abs(opt["spread"]):
                opt = {"horizon": H, "spread": spread}
    note = "最优预测窗口扫描(H∈{5,10,20,40,60})：按 score 中位数切冷/热组，spread=冷组未来H日收益-热组。"
    if opt is not None:
        note += "逆势信号最强窗口为 H=%d 日(spread=%.2f%%)。" % (opt["horizon"], opt["spread"])
    else:
        note += "样本不足，未定最优窗口。"
    # R125b：多窗口方向一致性（共振/背离）——不仅选最优窗口，还看各窗口信号是否同方向
    _dirs = [((r["spread"] or 0) >= 0) for r in rows if r.get("spread") is not None]
    _pos = sum(1 for d in _dirs if d)
    _neg = len(_dirs) - _pos
    if _dirs:
        if _pos == len(_dirs):
            _verdict = "共振(逆势有效)"
        elif _neg == len(_dirs):
            _verdict = "共振(顺势有效)"
        else:
            _verdict = "背离(信号分裂)"
        consensus = {"agree": max(_pos, _neg), "split": min(_pos, _neg),
                     "total": len(_dirs), "posDir": _pos, "negDir": _neg, "verdict": _verdict}
    else:
        consensus = None
    return {"horizons": rows, "optimalHorizon": (opt["horizon"] if opt else None),
            "consensus": consensus, "note": note}


def _state_signal(D, hist, delta, today_score, today_d20, regime=None):
    """R124b 组合状态信号 + 经验胜率：由『当前情绪水平档(level) × 当前Δ方向(dir)』

    定位其在四象限(高/中/低 × 升/降)中的位置，给出该组合状态的经验未来20日收益与
    经验上涨概率(=该格上涨样本数/N，纯样本频率、非拟合)。
    意义：单看水平或单看 Δ 都片面，『高位升温/低位降温』等组合状态更具时机含义。
    """
    if delta is None or today_score is None:
        return None
    lb = "高" if today_score >= 60 else ("低" if today_score < 40 else "中")
    dr = "升" if (today_d20 or 0) >= 0 else "降"
    qd = {(q["level"], q["dir"]): q for q in (delta.get("quadrants") or [])}
    q = qd.get((lb, dr))
    n = (q.get("n") if q else 0)
    fwd = (q.get("fwd20") if q else None)
    pos = (q.get("pos") if q else None)
    note = "组合状态信号：当前情绪水平=%s、Δ20方向=%s，落在四象限『%s%s』格。" % (lb, dr, lb, dr)
    if q and n:
        note += ("该格历史 N=%d，未来20日平均收益 %.2f%%，经验上涨概率 %.0f%%。"
                 % (n, fwd if fwd is not None else 0.0, (pos or 0.0) * 100))
    else:
        note += "该格历史样本不足，暂无统计支撑。"
    # R125c：分 regime 条件胜率——在当前 regime 下，该 level×dir 组合的历史经验上涨概率
    # （揭示『逆势信号在熊市态显著、牛市态失效』，比不分 regime 的 posPct 更细的条件统计）
    _regime_win = None
    if regime is not None:
        k = D.get("kline") or {}
        _closes = [float(x) for x in (k.get("close") or [])]
        _ma250 = [x for x in (k.get("ma250") or [])]
        if len(_closes) >= len(hist) + 20:
            _base = len(_closes) - len(hist)
            _rn = _rs = _rup = 0
            for _off, _h in enumerate(hist):
                _d = _h.get("d20")
                if _d is None:
                    continue
                _i = _base + _off
                if _i >= len(_closes) or (_ma250[_i] is None):
                    continue
                _isbear = _closes[_i] < _ma250[_i]
                if (regime == "bear") != _isbear:
                    continue
                _lvl = "高" if _h["score"] >= 60 else ("低" if _h["score"] < 40 else "中")
                if _lvl != lb:
                    continue
                _dd = "升" if (_d or 0) >= 0 else "降"
                if _dd != dr:
                    continue
                _j = _i + 20
                if _j >= len(_closes):
                    continue
                _f = (_closes[_j] / _closes[_i] - 1.0) * 100.0
                _rn += 1
                _rs += _f
                _rup += 1 if _f >= 0 else 0
            if _rn:
                _regime_win = {"regime": regime, "n": _rn,
                               "fwd20": round(_rs / _rn, 2),
                               "posPct": round(_rup / _rn, 2)}
    return {"level": lb, "dir": dr, "n": n, "fwd20": fwd, "posPct": pos,
            "regimeWin": _regime_win, "note": note}


def _recency_band(D, hist, today_label, bounds=None):
    """R124c 近期权重稳健性对照：对当前档(today_label)，取最近250交易日落在该档的样本，

    比较『等权』与『近1年指数衰减加权』下未来20日平均收益；
    两者显著背离(|差|>=1%%) 即提示 regime 漂移（近期与稍早统计口径不一致、信号稳健性下降）。
    纯描述性诊断，不改变任何预测口径。
    """
    if not hist or not today_label:
        return None
    k = D.get("kline") or {}
    closes = [float(x) for x in (k.get("close") or [])]
    if len(closes) < len(hist) + 20:
        return None
    base = len(closes) - len(hist)
    window = hist[-250:] if len(hist) >= 250 else hist
    lo_off = len(hist) - len(window)  # 仅取最近250个点的落档样本
    pairs = []
    for off, h in enumerate(hist):
        if off < lo_off or h.get("label") != today_label:
            continue
        i = base + off
        j = i + 20
        if j >= len(closes):
            continue
        f = (closes[j] / closes[i] - 1.0) * 100.0
        age = (len(hist) - 1 - off)  # 距今天数（0=今天）
        pairs.append((age, f))
    if not pairs:
        return None
    n = len(pairs)
    eq = sum(f for _, f in pairs) / n
    tau = 108.0  # 250 日前权重≈exp(-250/108)≈0.1
    w = [math.exp(-age / tau) for age, _ in pairs]
    dw = sum(wk * f for (age, f), wk in zip(pairs, w)) / sum(w)
    eq_r, dw_r = round(eq, 2), round(dw, 2)
    drift = abs(dw_r - eq_r) >= 1.0
    note = ("近期权重稳健性对照（当前档=%s，近%d样本）：等权未来20日均值 %.2f%%，近1年衰减加权 %.2f%%。"
            % (today_label, n, eq_r, dw_r))
    note += ("两者背离≥1%，提示该档近期 regime 相对稍早发生漂移，信号稳健性下降。"
             if drift else "两者接近，该档信号在近期与稍早口径一致，稳健性较好。")
    return {"band": today_label, "n": n, "equalWtd": eq_r, "decayWtd": dw_r,
            "drift": drift, "note": note}


def _extreme_reversal(D, hist, bounds=None, today_score=None):
    """R125a 极值反转诊断（逆向投资核心信号）：情绪进入极端区后，市场随后向相反方向回归的概率。

    极端定义：score 落入动态标尺极端档——恐慌(冰点, <b1) / 狂热(>b4)；回退固定 <20/>80。
    对历史上每个极端日，统计其后 N∈{5,20,60} 交易日市场收益方向：
      · 恐慌日『反转』=未来收益为正（市场随后反弹）；
      · 狂热日『反转』=未来收益为负（市场随后回落）。
    输出历史反转概率（验证逆向逻辑是否成立）+ 当前是否处于极端区。
    全为描述性统计、非拟合；纯诊断字段，不改预测口径、不改 dims。
    """
    k = D.get("kline") or {}
    closes = [float(x) for x in (k.get("close") or [])]
    if not hist or len(closes) < len(hist) + 60:
        return None
    base = len(closes) - len(hist)
    if bounds is None:
        bounds = [20.0, 40.0, 60.0, 80.0]
    b1, b4 = bounds[0], bounds[3]

    def _rev(kind, f):
        return 1.0 if ((kind == "panic" and f >= 0) or (kind == "euphoria" and f < 0)) else 0.0

    _st = {"panic": {5: [0, 0.0], 20: [0, 0.0], 60: [0, 0.0]},
           "euphoria": {5: [0, 0.0], 20: [0, 0.0], 60: [0, 0.0]}}
    for off, h in enumerate(hist):
        sc = h["score"]
        kind = "panic" if sc < b1 else ("euphoria" if sc > b4 else None)
        if kind is None:
            continue
        i = base + off
        for N in (5, 20, 60):
            j = i + N
            if j >= len(closes):
                continue
            f = (closes[j] / closes[i] - 1.0) * 100.0
            s = _st[kind][N]
            s[0] += 1
            s[1] += _rev(kind, f)

    def _row(kind):
        s5, s20, s60 = _st[kind][5], _st[kind][20], _st[kind][60]
        return {"n5": s5[0], "rev5": round(s5[1] / s5[0], 2) if s5[0] else None,
                "n20": s20[0], "rev20": round(s20[1] / s20[0], 2) if s20[0] else None,
                "n60": s60[0], "rev60": round(s60[1] / s60[0], 2) if s60[0] else None}

    # 当前是否极端
    cur = None
    if today_score is not None:
        if today_score < b1:
            cur = "panic"
        elif today_score > b4:
            cur = "euphoria"
    note = ("极值反转诊断：恐慌区(score<%s)后市场反弹、狂热区(score>%s)后回落的『逆向反转』概率；当前%s。"
            % (round(b1, 1), round(b4, 1),
               ("处于恐慌区" if cur == "panic" else "处于狂热区" if cur == "euphoria" else "未达极端")))
    return {"bounds": [round(b1, 1), round(b4, 1)], "current": cur,
            "panic": _row("panic"), "euphoria": _row("euphoria"), "note": note}


def _resolve_revert_tau(optimal_horizon):
    """R130a 最优窗口驱动回归时标：把固定 _REV_TAU=40 改为数据驱动。

    输入 _horizon_scan 的最优逆势窗口 H∈{5,10,20,40,60}（|spread| 最大且冷热组 N≥20 的 H）：
    H 小 → 逆势周期短 → 「向中枢回归」应更快(tau 小)；H 大 → 逆势周期长 → 回归更慢(tau 大)。
    线性映射 H∈[5,60] → tau∈[10,80]，再 clamp 防越界；H 缺失回退固定 40。
    """
    if optimal_horizon is None:
        return 40.0
    H = float(optimal_horizon)
    return _clamp(10.0 + (H - 5.0) * (80.0 - 10.0) / (60.0 - 5.0), 10.0, 80.0)


def _resolve_mom_win(optimal_horizon):
    """R131a 最优窗口驱动动量回看窗口：把固定回看 20 日改为数据驱动。

    与 R130a 同源：最优逆势窗口 H 大 → 逆势周期长 → 路径动量应回看更长（捕捉慢变趋势）；
    H 小 → 周期短 → 动量回看更短（贴合快变节奏）。线性映射 H∈[5,60] → win∈[10,40]，
    再 clamp 防越界；H 缺失回退固定 20（与原行为一致）。
    """
    if optimal_horizon is None:
        return 20
    H = float(optimal_horizon)
    return int(_clamp(round(10.0 + (H - 5.0) * (40.0 - 10.0) / (60.0 - 5.0)), 5, 60))


def _resolve_drift_params(drift):
    """R130b 漂移自适应参数：regime 漂移标志 → (置信带半宽倍率, 远端回归上限)。

    漂移期(drift=True) 近期与稍早统计口径背离、信号稳健性下降：
      ① 置信带半宽 ×1.2（不确定性更高，区间更宽更诚实）；
      ② 远端向中枢回归上限由 0.5 降至 0.35（不急于锚定可能已漂移的中枢，更谦卑）。
    """
    if drift:
        return (1.2, 0.35)
    return (1.0, 0.5)


def _compute_forecast(D, hist_std=None, regime=None, center=None,
                     regime_center=None, extreme_bounds=None, extreme_reversal=None,
                     optimal_horizon=None, drift=None, state_pospct=None, state_n=None,
                     consensus=None, today_d20=None,
                     verdict=None, regime_win_pospct=None, regime_win_n=None,
                     today_regime_pct=None):
    """由 subForecast 价格路径派生未来情绪预测序列 + 经验置信带（#666）+ 预测水平均值回归（R128）。

    做法：把 subForecast.points 的未来锚点（date, price）与「今日收盘」拼成路径，
    按日历日线性插值得到连续价位，剔除周末后仅保留交易日近似序列，再沿路径计算动量+牛熊位置
    （量能/波动随预测 horizon 指数回归历史中枢、广度沿用当前值）。这是「斐波那契路径派生」而非因子预测，
    已在 note 诚实标注。

    #666 经验置信带：每个预测点除 score 外，按历史情绪波动率(hist_std)打底、随预测 horizon √扩张、
    熊市态(regime='bear')额外×1.15，给出诚信 lo/hi 区间（详见 out["forecastBand"]），
    把「假精确单点」变「诚实区间」；区间半宽 clamp[1,14]，非严格统计置信区间。
    R128 预测 score 水平均值回归：情绪长期围绕历史中枢波动，远端不应被路径派生极值过度外推；
    预测分数随 horizon 指数回归「历史中枢 center」（revert 上限 50% 防过度拉平），使远端更诚实。
    R129 经验方向修正：把已验证的 regimeWin(分regime均衡情绪中枢) 与 extremeReversal(极值反弹概率)
    反馈进 forecast 方向修正，而非仅作展示字段——
      ① 极端区逆势修正：路径落入恐慌区(<b1)上修、狂热区(>b4)下修，幅度∝极值反弹概率 rev20
         （仅当 rev20>0.5 才有意义修正；非极端区保持 R128 路径忠实）；
      ② 分regime条件中枢偏置：远端随 horizon 渐入（与 R128 正交），熊/牛市态的均衡情绪中枢不同，
         远端不应一律回到全局 center，而应向「分regime均衡中枢 regime_center」偏移。
    R130 预测自适应增强（3 机制，均正交、均带 clamp 防过拟合）：
      a. 最优窗口驱动回归时标：用 _horizon_scan.optimalHorizon 把固定 _REV_TAU=40 改为数据驱动
         （H 小→回归快、H 大→回归慢），让「向中枢回归」节奏贴合当前逆势周期；
      b. 漂移自适应置信带+降速：用 _recency_band.drift，漂移期带宽 ×1.2、远端回归上限由 0.5 降至 0.35
         （不确定期更谦卑，不急于锚定可能已漂移的中枢）；
      c. 状态经验胜率微调近端：用 _state_signal.posPct（当前 level×方向 象限历史上涨概率，纯经验频率）
         对近端 forecast 做小幅方向偏置（权重 0.15 随 horizon 衰减、仅 N≥20 生效），与远端 R128/R129 正交。
    R131 路径派生诚实化（3 机制，均正交、均带 clamp）：
      a. 最优窗口驱动动量回看：_MOM_WIN 由 optimalHorizon 数据驱动（H 大→慢变趋势→回看长），
         与 R130a 同源，不再固定 20 日；
      b. 未来均线均值回归：牛熊位置锚点由「当前 MA250 恒值」改为「随 horizon 向路径价指数收敛」，
         远端价格偏离均线终会回归中性，不再被 clamp 钉死在 ±1 失真；
      c. 路径波动感知置信带：局部(近20点)收益波动 vs 全程波动，局部放大→带宽适度加宽(clamp[1,1.6])，
         路径高风险段不再被误报为精确。
    R132 经验信号调制（2 机制，均正交、均带 clamp）：
      a. 共识置信度调制：用 _horizon_scan.consensus 多窗口共振程度(agree/total)调制 R129 极端区修正
         与 R130c 状态微调的权重——共振(agree 高)→信号置信高→修正权重适度放大(clamp×1.5)，
         背离(agree≈half)→信号分裂→修正更保守(clamp×0.5)；只在方向修正层生效，不动 R128 中枢回归。
      b. Δ 动量惯性近端偏置：情绪序列有自相关（今天 Δ20 升/降的方向短期往往延续），
         用 today_d20 方向对近端做小幅惯性偏置（随 horizon 指数衰减、近端最强），与 R130c 的
         「象限历史胜率」正交（一个看当前动量方向、一个看该状态历史频率）。
    R133 信号方向调制（2 机制，均正交、均带 clamp）：
      a. 共振方向调制：R132a 只用了共振『程度』(agree/total)，此处再考虑共振『方向』——
         consensus.verdict=『共振(顺势有效)』表示高情绪后继续涨/低情绪后继续跌（趋势延续强），
         此时 R129 极端区『逆势修正』方向可能相反（恐慌上修/狂热下修与顺势冲突），
         故顺势有效或背离(信号分裂)时极端区修正权重 ×0.5 保守化，防方向性错误；逆势有效维持。
      b. 分regime条件胜率升级近端微调：R130c 用全局 stateSignal.posPct（水平×Δ方向象限历史上涨
         概率），而 regimeWin.posPct 是『当前牛/熊态下该组合状态的条件胜率』（R125 已验证熊市态
         中升51%>全样本47%，更贴合当前 regime）——优先用 regimeWin.posPct（N>=20 生效），
         缺失或样本不足时回退全局 posPct，使近端方向偏置更贴合当前市场状态。
    hist_std / regime / center / regime_center / extreme_bounds / extreme_reversal / optimal_horizon /
    drift / state_pospct / state_n / consensus / today_d20 / verdict / regime_win_pospct /
    regime_win_n 由 main() 计算后传入；缺失时回退默认值，保证函数可独立调用。
    """
    k = D.get("kline") or {}
    dates = [str(x) for x in (k.get("dates") or [])]
    closes = [float(x) for x in (k.get("close") or [])]
    if not closes:
        return []

    today = dates[-1]
    last_close = closes[-1]
    ma250_now = _ma(closes, 250) or last_close

    # R141：广度沿用当前可用快照（仍无逐日历史源，R271 约束），经 _breadth_adaptive 映射为信号维度；
    # 预测路径中广度维度恒为当日值（与 R127a 量能/波动解冻正交：广度无历史源，不在 horizon 上回归）。
    sub_breadth, _bw, _dr, _cr, _btags = _breadth_sub(D)
    sub_breadth = _breadth_adaptive(sub_breadth, _btags)
    # R127a 解冻：量能/波动为「今日锚值」，沿预测 horizon 指数回归中性(0)（波动率均值回归铁律），
    # 不再恒用今日值贯穿整个预测期；仅广度无历史源、仍沿用当前值。
    # R139b：波动率今日锚值改与已实现路径(_compute_today)同源——用 _daily_hv_series+_pctile_rank
    # 计算，不再依赖 build 层 volRegime.pctile 双源；消除 forecast/realized 波动率口径漂移。
    _hv_all = _daily_hv_series(closes)
    _hv_win = [h for h in _hv_all[max(0, len(closes) - 250):] if h is not None]
    _hv_pctile = _pctile_rank(_hv_all[-1], _hv_win) if _hv_all[-1] is not None else 50.0
    sub_volat_today = _clamp(1.0 - _hv_pctile / 50.0, -1.0, 1.0)
    # R139a：量能今日锚值除数与已实现路径(_adaptive_volume_divisor≈0.23)同源，
    # 替代 R138 前遗留的硬编码 /0.5（与 realized 口径漂移 ~2.2×，forecast 量能维度欠缩放）。
    vols = [float(x) for x in (k.get("volume") or [])]
    vol20 = _ma(vols, 20) or 1.0
    vol250 = _ma(vols, 250) or 1.0
    sub_vol_today = _clamp((vol20 / vol250 - 1.0) / _adaptive_volume_divisor(closes, vols), -1.0, 1.0)
    _COV_TAU = 15.0  # 协变量均值回归时间常数（交易日）：约 15 日衰减 ~63%
    # R128 预测 score 水平均值回归（与 R127a 协变量解冻正交）：远端随 horizon 向历史中枢回归，
    # 避免路径派生极值被过度外推到 3 个月外；revert 上限 _REVERT_CAP 防曲线被拉平失真。
    # R130a 最优窗口驱动回归时标：把固定 _REV_TAU=40 改为数据驱动（H 小→回归快、H 大→回归慢）。
    _REV_TAU = _resolve_revert_tau(optimal_horizon)
    # R130b 漂移自适应：regime 漂移期带宽 ×1.2、远端回归上限 0.5→0.35（更谦卑，不锚定漂移中枢）
    _DRIFT_MULT, _REVERT_CAP = _resolve_drift_params(drift)
    # R129 经验方向修正超参：把已验证诊断反馈进 forecast，权重经 clamp 防过拟合/过度修正
    # R140：_REGIME_BIAS_W 由 0.5 降至 0.25——R140 已在 R128 锚点层引入 regime 感知主拉引力，
    # 此处保留远端残余强调，避免与 R140 双重叠加同一信号导致过度修正。
    _REGIME_BIAS_W = 0.25  # 远端由（R140 已 regime 混合的）中枢向 regime_center 残余偏移的最大混合比例
    _EXTREME_BIAS_W = 0.6  # 极端区内向中枢回归的最大混合比例（实际再×2*(rev20-0.5)）
    _center = center if (center is not None) else 60.0  # 历史中枢锚点（缺省 60）
    # R140 regime 感知中枢（贯穿全 horizon 的主拉引力，与 R128 正交叠加）：
    # 当前处于熊/牛态时，全局中枢 _center(≈54) 被 bull 样本抬高、对 bear 态是过高回归目标；
    # 故按 regime 强度把回归锚点由 _center 向 regime_center 混合，使预测路径各 horizon 均向
    # 「当前 regime 均衡」回归而非 bull 抬高后的混合均值——直接纠正 bear 态下预测中段虚高 ~60 的失真。
    # 混合权重随 regime_center 与 _center 的偏离（regime 强度）线性放大，clamp[0,0.6] 防过度。
    _RC_W = 0.0
    if regime_center is not None and _center is not None:
        _gap = abs(regime_center - _center)
        _RC_W = _clamp(_gap / 20.0, 0.0, 0.6)  # gap=20→满权 0.6；当前 bear gap≈11.4→≈0.34
    _eff_center = _center * (1.0 - _RC_W) + (regime_center if regime_center is not None else _center) * _RC_W
    # R140 regime 水平位移权重（不随 horizon 衰减）：把预测中枢拉向当前 regime 均衡，
    # 0.35 经校准 bear 态中段虚高 ~63 可降至 ~53（保留价格路径偏离表达，不过度压平）。
    # R142：用『今日分 regime 滚动分位』调制该位移力度——若今日已处于当前 regime 的极端区
    # （regimePct 接近 0 或 100），说明路径派生方向本身已贴合 regime 表达，不必再硬拉向 regime 中枢，
    # 避免过度修正使牛熊切换时预测失真；今日恰在 regime 中位(regimePct=50)时维持满权 0.35。
    # 调制系数 clamp[0.3,1.0] 防极端区完全失效（仍保留最低 30% regime 锚定）。
    _LEVEL_W = 0.35
    if today_regime_pct is not None:
        _rp = _clamp(today_regime_pct, 0.0, 100.0)
        _mod = _clamp(1.0 - abs(_rp - 50.0) / 50.0, 0.3, 1.0)
        _LEVEL_W = _LEVEL_W * _mod
    # R130c 状态经验胜率微调近端超参：当前 level×dir 象限历史上涨概率 posPct（纯经验频率）
    # 对近端 forecast 做小幅方向偏置；权重随 horizon 衰减（近端最强、远端归零），仅 N>=20 生效。
    _STATE_BIAS_W = 0.15   # 近端状态偏置最大权重（实际再×(posPct-0.5)*2 × 近端衰减）
    _STATE_MAX_PTS = 12.0  # 近端最大偏置点数（posPct=1.0 或 0.0 且权重=1 时）
    # R130c/R133b 近端偏置独立快衰减时标（与 R132b 惯性同语义：近端时机信号应快速归零）：
    # 不用数据驱动 _REV_TAU（H=60 时 tau=80，53 点预测末尾仍残留 52% 权重，与『近端归零』矛盾）。
    _STATE_TAU = 15.0
    # R131a 最优窗口驱动动量回看窗口：与 R130a 同源（optimal_horizon），H 大→回看长（慢变趋势）、
    # H 小→回看短（快变节奏）；缺失回退 20（与原行为一致）。
    _MOM_WIN = _resolve_mom_win(optimal_horizon)
    # R131b 未来均线均值回归时标：牛熊位置不再恒锚「当前 MA250」（路径大涨时远端被 clamp 钉死失真），
    # 而令未来均线随 horizon 向路径价收敛（价格偏离均线终会均值回归的铁律），远端偏离回归中性。
    _MA_TAU = 60.0  # 均线跟随价格的指数收敛时标（交易日）
    # R132a 共识置信度调制：多窗口共振程度(agree/total) → 方向修正权重倍率。
    # agree/total=1.0(全共振)→×1.5(信号置信高、修正更有底气)；=0.5(完全背离)→×0.5(信号分裂、保守)；
    # 线性插值后 clamp[0.5,1.5] 防过度放大/过度压制；仅作用于 R129/R130c 方向修正层，不动 R128 中枢回归。
    _conf = None
    if consensus is not None:
        _t = consensus.get("total") or 0
        if _t > 0:
            _conf = float(consensus.get("agree") or 0) / float(_t)
    # 防御：显式 None 判断，不用 `_conf or 0.75`（0.0 是合法值，or 会误吞）
    _CONF_MULT = _clamp(0.5 + 2.0 * ((_conf if _conf is not None else 0.75) - 0.5), 0.5, 1.5) \
        if _conf is not None else 1.0
    # R132b Δ 动量惯性近端偏置：情绪自相关——今天 Δ20 升/降的方向短期往往延续。
    # _INERTIA_TAU 控制衰减快慢（近端最强、远端归零），_INERTIA_MAX_PTS 为近端最大偏置点数。
    _INERTIA_TAU = 15.0
    _INERTIA_MAX_PTS = 8.0
    _d20_edge = _clamp((today_d20 or 0.0) / 10.0, -1.0, 1.0)  # Δ20=+10 → +1(强升温)，-10 → -1
    # R133a 共振方向调制：verdict 方向 → 极端区逆势修正权重倍率。
    # 『共振(顺势有效)』=趋势延续强（高情绪后涨/低情绪后跌），与逆势修正方向冲突→×0.5 保守；
    # 『背离(信号分裂)』=方向不明→×0.5 保守；『共振(逆势有效)』或缺失→×1.0 维持。
    _VERDICT_MULT = 0.5 if (verdict in ("共振(顺势有效)", "背离(信号分裂)")) else 1.0
    # R133b 分regime条件胜率升级：近端微调优先用 regimeWin.posPct（当前牛/熊态下该组合状态的条件胜率，
    # R125 已验证熊市态中升51%>全样本47%），仅 N>=20 生效；缺失/样本不足回退全局 state_pospct。
    _pospct_eff = None
    _posn_eff = None
    if regime_win_pospct is not None and regime_win_n is not None and regime_win_n >= 20:
        _pospct_eff = regime_win_pospct
        _posn_eff = regime_win_n
    elif state_pospct is not None and state_n is not None and state_n >= 20:
        _pospct_eff = state_pospct
        _posn_eff = state_n

    # #666 经验置信带参数：熊市态额外放大，缺失回退
    regime_mult = 1.15 if regime == "bear" else 1.0
    base_std = hist_std if (hist_std is not None and hist_std > 0) else 8.0

    sf = (D.get("subForecast") or {}).get("points") or []
    # 锚点：(date, price)，加入今日作为起点
    anchors = [(today, last_close)]
    for p in sf:
        d = str(p.get("date"))
        pr = p.get("price")
        if d and pr is not None:
            anchors.append((d, float(pr)))
    anchors = sorted(set(anchors), key=lambda x: x[0])

    # 仅取今日及之后的锚点（未来段）
    future = [(d, pr) for (d, pr) in anchors if d >= today]
    if len(future) < 2:
        return []

    future_dates = [d for (d, _p) in future]
    future_prices = [pr for (_d, pr) in future]
    # 逐日插值：从今日到末锚点之间按日历日（含周末）线性插值，得到连续路径
    from datetime import datetime as _dt

    def _to_dt(s):
        return _dt.strptime(s, "%Y-%m-%d")

    path = []  # (date_str, price)
    for idx in range(len(future) - 1):
        d0, p0 = future[idx]
        d1, p1 = future[idx + 1]
        t0, t1 = _to_dt(d0), _to_dt(d1)
        ndays = (t1 - t0).days
        if ndays <= 0:
            continue
        for step in range(1, ndays + 1):
            t = t0 + __import__("datetime").timedelta(days=step)
            frac = step / ndays
            price = p0 + (p1 - p0) * frac
            path.append((t.strftime("%Y-%m-%d"), price))
    # path 已含每个分段末点；需确保末锚点本身纳入
    if path and path[-1][0] != future_dates[-1]:
        path.append((future_dates[-1], future_prices[-1]))
    if not path:
        path = [(future_dates[-1], future_prices[-1])]

    # A股非交易日剔除：剔除周末/法定长假，保留调休补班日，使 forecast 仅含交易日近似日期
    path = [(d, p) for (d, p) in path if _is_a_share_trading_day(d)]
    if not path:
        path = [(future_dates[-1], future_prices[-1])]

    # R131c 路径波动感知置信带：先算路径逐日收益序列，供循环内「局部 vs 全程」波动比值使用。
    # 局部波动显著放大（如路径锚点间急涨急跌）→ 近端带宽适度加宽，避免路径高风险段被误报为精确。
    _path_prices = [p for (_d, p) in path]
    _ret_all = [(_path_prices[i] / _path_prices[i - 1] - 1.0)
                for i in range(1, len(_path_prices)) if _path_prices[i - 1]]
    _std_all = (sum((r - sum(_ret_all) / len(_ret_all)) ** 2 for r in _ret_all) / len(_ret_all)) ** 0.5 \
        if _ret_all else 0.0

    out = []
    for idx, (d, price) in enumerate(path):
        # R131a 动量：回看窗口由最优逆势窗口 H 数据驱动（H 大→慢变趋势→回看长）；
        # 早期点数不足 _MOM_WIN 时以「今日收盘」兜底，避免衔接处动量硬归零造成的跳变。
        if idx >= _MOM_WIN:
            past_price = path[idx - _MOM_WIN][1]
        else:
            past_price = last_close
        ret_path = (price / past_price - 1.0) * 100.0 if past_price else 0.0
        sub_mom = _clamp(ret_path / 8.0, -1.0, 1.0)
        # R131b 牛熊位置：未来均线随 horizon 向路径价指数收敛（价格偏离均线终会均值回归的铁律），
        # 替代恒锚「当前 MA250」（路径大涨/大跌时远端不再被 clamp 钉死在 ±1 失真）。
        # 近端(idx=0) 收敛因子≈0 → 完全沿用当前 MA250（与原行为一致）；远端渐入路径价 → 偏离回归中性。
        _ma_revert = 1.0 - math.exp(-(idx + 1) / _MA_TAU)
        _ma_future = (ma250_now + (price - ma250_now) * _ma_revert) if ma250_now else None
        pos = (price / _ma_future - 1.0) if _ma_future else 0.0
        sub_pos = _clamp(pos / 0.15, -1.0, 1.0)
        # R127a 解冻协变量：量能/波动随 horizon 指数回归中性(0)，远期不再被今日异常值钉死
        _decay = math.exp(-(idx + 1) / _COV_TAU)
        _sub_vol = sub_vol_today * _decay
        _sub_volat = sub_volat_today * _decay
        # R128 预测 score 水平均值回归：路径派生分随 horizon 向历史中枢回归（远端最多回归 50%），
        # 避免 3 个月外的预测被今日路径极值过度外推；近端(idx=0) revert=0 完全忠实路径。
        _path_score = _score_from_subs([sub_mom, sub_pos, _sub_vol, _sub_volat, sub_breadth])
        # R140 regime 水平位移（贯穿全 horizon 的主修正，独立于 regime_center 是否缺失）：
        # 已实现数据实证 bear 态 score 均值 42.6、bull 态 61.7（相差 19 点水平位移）——情绪温度在
        # 牛熊态有系统性水平差。当前 regime 下，斐波那契价格路径的短期斜率不应把情绪推离当前
        # regime 均衡太远，故把 _path_score 向 regime_center 做固定比例水平位移 _LEVEL_W（不随
        # horizon 衰减），使预测中枢贴合当前牛熊态，纠正 bear 态下预测中段虚高 ~60 的失真。缺失跳过。
        if regime_center is not None:
            _path_score = _path_score * (1.0 - _LEVEL_W) + regime_center * _LEVEL_W
        _revert = min(1.0 - math.exp(-(idx + 1) / _REV_TAU), _REVERT_CAP)
        # R128 全局中枢回归 + R140 regime 感知锚点（_eff_center 已按 regime 强度混合 regime_center），
        # 因 _revert 随 horizon 递增，regime 感知拉引力近端弱、远端强，形状正确。
        score = _path_score * (1.0 - _revert) + _eff_center * _revert
        # R129 经验方向修正（把已验证诊断反馈进 forecast，而非仅展示）：
        # ① 极端区逆势修正：路径落入恐慌区(<b1)上修、狂热区(>b4)下修，幅度∝极值反弹概率 rev20
        #    （rev20>0.5 才有意义修正；仅极端区生效，非极端区保持 R128 路径忠实）。
        # ② 分regime条件中枢偏置：远端随 horizon 渐入（与 R128 正交），熊/牛市态的均衡情绪
        #    中枢不同，远端不应一律回到全局 center，而应向 regime_center 偏移（上限 _REGIME_BIAS_W）。
        # R132a 共识置信度调制：上述方向修正权重 ×_CONF_MULT（共振放大、背离保守），
        # 信号置信度来自多窗口共识，只在方向修正层生效、不动 R128 中枢回归。
        # R133a 共振方向调制：极端区逆势修正再 ×_VERDICT_MULT——顺势有效/背离时保守(×0.5)，
        # 防『趋势延续强』时逆势修正方向错误；逆势有效维持(×1.0)。
        if extreme_bounds is not None and extreme_reversal is not None:
            _b1, _b4 = extreme_bounds[0], extreme_bounds[-1]
            if score < _b1 and (extreme_reversal.get("panic") or {}).get("rev20") is not None:
                _p = extreme_reversal["panic"]["rev20"]
                _w = _clamp(_EXTREME_BIAS_W * _CONF_MULT * _VERDICT_MULT, 0.0, 1.0) * max(0.0, _p - 0.5) * 2.0
                score = score * (1.0 - _w) + _eff_center * _w  # R140：极端区回归目标也用 regime 感知锚点
            elif score > _b4 and (extreme_reversal.get("euphoria") or {}).get("rev20") is not None:
                _p = extreme_reversal["euphoria"]["rev20"]
                _w = _clamp(_EXTREME_BIAS_W * _CONF_MULT * _VERDICT_MULT, 0.0, 1.0) * max(0.0, _p - 0.5) * 2.0
                score = score * (1.0 - _w) + _eff_center * _w
        if regime_center is not None:
            # R140：R128 已用 _eff_center 承载 regime 主拉引；此处仅对远端做残余强调，目标仍锚
            # _eff_center（不再二次拉向 raw regime_center），与 R140 正交不重复叠加。
            _rbias = min(1.0 - math.exp(-(idx + 1) / _REV_TAU), _REVERT_CAP)
            score = score * (1.0 - _rbias * _REGIME_BIAS_W * _CONF_MULT) + _eff_center * _rbias * _REGIME_BIAS_W * _CONF_MULT
        # R130c 状态经验胜率微调近端：当前 level×dir 象限历史上涨概率（纯经验频率），
        # 对近端 forecast 做小幅方向偏置；权重随 horizon 衰减（近端最强、远端归零），仅 N>=20 生效，
        # 与远端 R128/R129 正交（近端时机信号 + 远端中枢回归）。
        # R132a 共识置信度调制权重；R133b 优先用分regime条件胜率 _pospct_eff（更贴合当前牛熊态）。
        if _pospct_eff is not None and _posn_eff is not None and _posn_eff >= 20:
            _edge = (_pospct_eff - 0.5) * 2.0  # ∈[-1,1]：>0 看多象限→上修、<0 看空象限→下修
            _sdecay = math.exp(-(idx + 1) / _STATE_TAU)  # 近端最强、远端归零（快衰减，不用数据驱动 _REV_TAU）
            _bump = _edge * _STATE_BIAS_W * _CONF_MULT * _STATE_MAX_PTS * _sdecay
            score = _clamp(score + _bump, 0.0, 100.0)
        # R132b Δ 动量惯性近端偏置：情绪自相关——今天 Δ20 升/降方向短期往往延续。
        # 近端按 Δ20 方向小幅惯性偏置（随 horizon 指数衰减），与 R130c 象限胜率正交。
        if today_d20 is not None:
            _idecay = math.exp(-(idx + 1) / _INERTIA_TAU)
            _ibump = _d20_edge * _INERTIA_MAX_PTS * _idecay
            score = _clamp(score + _ibump, 0.0, 100.0)
        score = round(_clamp(score, 0.0, 100.0), 1)
        # #666 经验置信带：随 horizon √扩张、熊市态额外放大；R130b 漂移期带宽 ×_DRIFT_MULT；
        # R131c 路径波动感知：局部(近20点)收益波动 vs 全程波动，局部放大→带宽适度加宽（clamp[1,1.6]）
        kk = idx + 1
        if _std_all > 1e-12:
            _win = _ret_all[max(0, idx - 19):idx + 1]
            if _win:
                _mu = sum(_win) / len(_win)
                _std_loc = (sum((r - _mu) ** 2 for r in _win) / len(_win)) ** 0.5
                _ratio = _std_loc / _std_all
            else:
                _ratio = 1.0
            _path_vol_mult = _clamp(1.0 + (_ratio - 1.0) * 0.5, 1.0, 1.6)
        else:
            _path_vol_mult = 1.0
        half = _clamp(base_std * math.sqrt(kk / 20.0) * 0.8 * regime_mult
                      * _DRIFT_MULT * _path_vol_mult, 1.0, 14.0)
        lo = _clamp(score - half, 0.0, 100.0)
        hi = _clamp(score + half, 0.0, 100.0)
        out.append({"date": d, "score": score,
                    "lo": round(lo, 1), "hi": round(hi, 1)})
    return out


def main():
    out = {
        "schema": "a-share-fib-sentiment/v3",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "monitor_only",
        "error": None,
    }
    try:
        D = _load_data()
        out["asOf"] = D.get("fetchedAt") or D.get("updated")

        # R122a：先算历史序列 → 由历史分布推导动态分位标尺 → 用同一标尺统一重标 today/history/forecast
        hist = _compute_history(D)
        bounds, scale_mode, scale_pct = _scale_bounds([h["score"] for h in hist])
        for h in hist:
            h["label"] = _label(h["score"], bounds)
        out["history"] = hist

        # R123a/R123b：每段历史的情绪变化 Δ5/Δ20 与滚动 z 分位（近120日均值/标准差）
        _hs = [h["score"] for h in hist]
        for _i, _h in enumerate(hist):
            _s = _h["score"]
            _h["d5"] = round(_hs[_i] - _hs[_i - 5], 1) if _i >= 5 else None
            _h["d20"] = round(_hs[_i] - _hs[_i - 20], 1) if _i >= 20 else None
            _win = _hs[max(0, _i - 119):_i + 1]
            _mu = sum(_win) / len(_win)
            _sd = (sum((_x - _mu) ** 2 for _x in _win) / len(_win)) ** 0.5
            _h["z"] = round((_s - _mu) / _sd, 2) if _sd > 1e-9 else 0.0

        r = _compute_today(D)
        if r is None:
            out["today"] = {"score": None, "label": "数据不足",
                            "note": "样本 < 250 日，暂不合成情绪温度（monitor_only）。"}
        else:
            score, dims = r
            out["today"] = {
                "score": round(score, 1),
                "label": _label(score, bounds),
                "dims": [{"name": n, "weight": w, "sub": round(s, 4), **m}
                         for (n, w, s, m) in dims],
            }

        # #666 预测置信带：历史情绪波动率 + 当前 regime（末根价 vs MA250）
        _kline = D.get("kline") or {}
        _closes = [float(x) for x in (_kline.get("close") or [])]
        _ma250 = [x for x in (_kline.get("ma250") or [])]
        _last_close = _closes[-1] if _closes else None
        _last_ma = _ma250[-1] if _ma250 else None
        _regime = ("bear" if (_last_ma is not None and _last_close is not None
                              and _last_close < _last_ma) else "bull")
        _hist_scores = [h["score"] for h in hist]
        _hist_std = 8.0
        if _hist_scores:
            _mu = sum(_hist_scores) / len(_hist_scores)
            _hist_std = (sum((s - _mu) ** 2 for s in _hist_scores) / len(_hist_scores)) ** 0.5
        _hist_mean = (sum(_hist_scores) / len(_hist_scores)) if _hist_scores else 60.0  # R128 预测水平均值回归锚点

        # R127b 滚动感知 base_std：近端带宽跟随「当前湍流度」，而非单一全局常数。
        # 近 60 日 std 与全局 std 混合（近期更平静→带宽更窄更诚实），clamp[4,18] 防极端。
        _recent_scores = _hist_scores[-60:] if len(_hist_scores) >= 20 else []
        _recent_std = None
        if _recent_scores:
            _rmu = sum(_recent_scores) / len(_recent_scores)
            _recent_std = (sum((s - _rmu) ** 2 for s in _recent_scores) / len(_recent_scores)) ** 0.5
        _base_std = (_clamp(0.6 * _recent_std + 0.4 * _hist_std, 4.0, 18.0)
                     if _recent_std is not None else _hist_std)

        # R129：前置极值反转诊断（原在 forecast 之后才算、仅作展示；现提前供 forecast 方向修正使用）
        _ext = _extreme_reversal(D, hist, bounds, out["today"].get("score"))
        # R129：分regime均衡情绪中枢（历史中『当前regime』样本的情绪均值，纯经验、非拟合）
        # R142：同一循环构建逐点 regime 标志 _regimes（与 _regime_center 同源口径），
        #       供分 regime 滚动分位归一（_regime_rolling_pct）使用，避免重复口径漂移。
        _regime_center = None
        _regimes = []  # 与 hist 等长，True=该点 bear 态（价<MA250）
        if _regime in ("bear", "bull"):
            _kc = [float(x) for x in (_kline.get("close") or [])]
            _km = [x for x in (_kline.get("ma250") or [])]
            if len(_kc) >= len(hist):
                _b = len(_kc) - len(hist)
                _vals = []
                for _off, _h in enumerate(hist):
                    _i = _b + _off
                    if _i >= len(_kc) or _i >= len(_km) or _km[_i] is None:
                        _regimes.append(None)
                        continue
                    _isbear = _kc[_i] < _km[_i]
                    _regimes.append(_isbear)
                    if (_regime == "bear") == _isbear:
                        _vals.append(_h["score"])
                if _vals:
                    _regime_center = sum(_vals) / len(_vals)

        # R142：分 regime 滚动分位归一——逐点分位（仅与同 regime 近期样本比，抗中枢 19 点系统差）。
        # 缺失标志点（MA250 预热前）回退 50（中性）。
        _hist_scores_full = [h["score"] for h in hist]
        _regime_pct_hist = []
        if _regimes:
            for _i in range(len(hist)):
                if _regimes[_i] is None:
                    _regime_pct_hist.append(50.0)
                else:
                    _regime_pct_hist.append(round(
                        _regime_rolling_pct(_hist_scores_full, _regimes, _i, window=250), 1))
        for _i, _h in enumerate(hist):
            _h["regimePct"] = _regime_pct_hist[_i] if _regime_pct_hist else None

        # R142：今日分 regime 滚动分位（与历史同源，复用 _regime_rolling_pct 单一真值，
        # 避免历史/今日两套口径漂移）。把今日视作滚动窗口末端的同 regime 新点(idx=last_i+1)，
        # 直接复用同一函数——今日 score 在『同regime近250日样本』中的分位，
        # 直接回答『当前情绪在当下牛熊态内有多极端』。
        _regime_pct_today = None
        if _regimes and out.get("today", {}).get("score") is not None:
            _ts = out["today"]["score"]
            _last_i = len(hist) - 1
            _scores_today = _hist_scores_full + [_ts]
            _regimes_today = _regimes + [(_regime == "bear")]
            _regime_pct_today = round(
                _regime_rolling_pct(_scores_today, _regimes_today, _last_i + 1, window=250), 1)
            out["today"]["regimePct"] = _regime_pct_today

        # R130：前置三个诊断信号（原仅在 forecast 之后 contra 段计算、仅作展示；现提前供
        # forecast 自适应增强使用，与 R129 前置 _ext 同法）——delta/horizon/state/recency。
        _hs0 = out["history"][-1].get("d20") if out.get("history") else None
        _delta = _contra_delta_stats(D, out["history"])
        _horizon = _horizon_scan(D, out["history"], bounds)
        _state = None
        if _delta is not None:
            _state = _state_signal(D, out["history"], _delta,
                                   out["today"].get("score"), _hs0, _regime)
        _recency = _recency_band(D, out["history"], out["today"].get("label"), bounds)

        fcst = _compute_forecast(D, _base_std, _regime, _hist_mean,
                                 regime_center=_regime_center,
                                 extreme_bounds=(_ext.get("bounds") if _ext else None),
                                 extreme_reversal=_ext,
                                 optimal_horizon=(_horizon.get("optimalHorizon") if _horizon else None),
                                 drift=(_recency.get("drift") if _recency else None),
                                 state_pospct=(_state.get("posPct") if _state else None),
                                 state_n=(_state.get("n") if _state else None),
                                 consensus=((_horizon or {}).get("consensus") or None),
                                 today_d20=_hs0,
                                 verdict=(((_horizon or {}).get("consensus") or {}).get("verdict") or None),
                                 regime_win_pospct=(((_state or {}).get("regimeWin") or {}).get("posPct")
                                                    if (_state and (_state.get("regimeWin") or {}).get("posPct") is not None) else None),
                                 regime_win_n=(((_state or {}).get("regimeWin") or {}).get("n")
                                               if (_state and (_state.get("regimeWin") or {}).get("n") is not None) else None),
                                 today_regime_pct=_regime_pct_today)
        for c in fcst:
            c["label"] = _label(c["score"], bounds)
        out["forecast"] = fcst
        # R130 元信息：数据驱动回归时标/漂移带宽倍率/漂移期回归上限/状态偏置权重（与 _compute_forecast 内一致）
        _revert_tau_eff = _resolve_revert_tau(_horizon.get("optimalHorizon") if _horizon else None)
        _drift_mult, _revert_cap_eff = _resolve_drift_params(
            _recency.get("drift") if _recency else None)
        _mom_win_eff = _resolve_mom_win(_horizon.get("optimalHorizon") if _horizon else None)
        _state_pospct = (_state.get("posPct") if _state else None)
        _state_n = (_state.get("n") if _state else None)
        _consensus = ((_horizon or {}).get("consensus") or {})
        _cons_agree = (_consensus.get("agree") if _consensus else None)
        _cons_total = (_consensus.get("total") if _consensus else None)
        _conf_raw = (float(_cons_agree) / float(_cons_total)
                     if (_cons_agree is not None and _cons_total) else None)
        _conf_val = (round(_conf_raw, 2) if _conf_raw is not None else None)
        # confMult 须基于未 round 原始比值计算（与 _compute_forecast 内 _CONF_MULT 一致），
        # 避免 round 后的展示值误导实际生效值（如 0.6666→展示0.67→mult 0.84 vs 实际 0.833）
        _conf_mult_val = round(_clamp(0.5 + 2.0 * ((_conf_raw if _conf_raw is not None else 0.75) - 0.5), 0.5, 1.5), 2) \
            if _conf_raw is not None else 1.0
        _verdict_val = (_consensus.get("verdict") if _consensus else None)
        _verdict_mult_val = (0.5 if _verdict_val in ("共振(顺势有效)", "背离(信号分裂)") else 1.0)
        _rw = ((_state or {}).get("regimeWin") or {})
        _rw_pospct = (_rw.get("posPct") if _rw else None)
        _rw_n = (_rw.get("n") if _rw else None)
        # 展示口径与 _compute_forecast 内 _pospct_eff 完全一致：regimeWin.posPct 存在且 N>=20 才用条件胜率
        _use_rw = (_rw_pospct is not None and _rw_n is not None and _rw_n >= 20)
        _pospct_used = _rw_pospct if _use_rw else (_state.get("posPct") if _state else None)
        _posn_used = _rw_n if _use_rw else (_state.get("n") if _state else None)
        out["forecastBand"] = {
            "method": "经验置信带（目标覆盖≈80%，基于滚动感知波动率的启发式近似，非严格统计置信区间）",
            "level": "≈80%",
            "baseStd": round(_base_std, 2),
            "baseStdGlobal": round(_hist_std, 2),
            "baseStdRecent60": (round(_recent_std, 2) if _recent_std is not None else None),
            "regime": _regime,
            "regimeMult": (1.15 if _regime == "bear" else 1.0),
            "horizonScale": "sqrt(k/20) 时间衰减（k=预测点序号/交易日）",
            "revertCenter": round(_hist_mean, 2),
            "revertTau": round(_revert_tau_eff, 2),
            "revertCap": round(_revert_cap_eff, 2),
            "revertTauEff": round(_revert_tau_eff, 2),
            "driftMult": round(_drift_mult, 2),
            "momWinEff": _mom_win_eff,
            "maRevertTau": 60.0,
            "pathVolMultMax": 1.6,
            "pathVolMethod": "局部(近20点)路径收益波动 vs 全程波动，局部放大→带宽适度加宽(clamp[1,1.6])；路径高风险段不误报为精确",
            "regimeBiasCenter": (round(_regime_center, 2) if _regime_center is not None else None),
            "regimeBiasW": 0.5,
            "regimePctileToday": (round(_regime_pct_today, 1) if _regime_pct_today is not None else None),
            "regimePctileMethod": "R142 分regime滚动分位：今日/历史 score 在『同regime近250日样本』中的分位(抗bear/bull中枢19点系统差)，history逐点含regimePct、today含regimePct",
            "levelWMod": (round(_clamp(0.35 * _clamp(1.0 - abs(_clamp(_regime_pct_today, 0.0, 100.0) - 50.0) / 50.0, 0.3, 1.0), 0.0, 1.0), 3)
                          if _regime_pct_today is not None else 0.35),
            "extremeBiasW": 0.6,
            "extremeBiasMethod": "极端区(恐慌<b1/狂热>b4)内按极值反弹概率rev20向中枢回归(仅rev20>0.5生效)；非极端区保持路径忠实",
            "stateBiasW": 0.15,
            "stateBiasMethod": "近端按当前水平×Δ方向象限历史上涨概率posPct微调(权重0.15随horizon衰减、仅N>=20生效)，远端由R128/R129中枢回归主导",
            "consensusConf": _conf_val,
            "confMult": _conf_mult_val,
            "verdict": _verdict_val,
            "verdictMult": _verdict_mult_val,
            "regimeWinN": _rw_n,
            "posPctUsed": (round(_pospct_used, 2) if _pospct_used is not None else None),
            "inertiaTau": 15.0,
            "inertiaMaxPts": 8.0,
            "inertiaMethod": "近端按今日Δ20方向做情绪自相关惯性偏置(Δ20=+10→上修最多8分、-10→下修，随horizon指数衰减)，与R130c象限胜率正交",
            "note": ("预测为路径派生单点；本带按「滚动感知波动率」打底（近60日std=%.1f 与全局std=%.1f 混合=%.1f）、"
                     "随预测 horizon √扩张、熊市态额外×1.15，将「假精确单点」变为「诚实区间」；区间半宽上限14分、下限1分，"
                     "近端带宽跟随当前湍流度（近期更平静→更窄）；(R128) 预测 score 水平随 horizon 向历史中枢(%.1f) "
                     "指数均值回归(远端最多回归%.0f%%)避免路径极值过度外推；(R129) 已把分regime均衡中枢(%.1f, %s态)与极值反弹概率"
                     "反馈进方向修正——极端区按 rev20 逆势回归、远端由全局中枢向分regime中枢偏移(权重%.2f)，"
                     "(R130) 预测自适应增强：①最优逆势窗口H=%s驱动回归时标(tau=%.1f，H小回归快/H大回归慢)；"
                     "②regime漂移(等权vs衰减加权背离≥1%%)=%s→带宽×%.1f、远端回归上限降至%.2f；"
                     "③近端按状态经验胜率posPct=%s(象限N=%s)微调(权重0.15随horizon衰减)。"
                     "(R131) 路径派生诚实化：①动量回看窗口由最优窗口H驱动(momWinEff=%d日，H大→慢变趋势→回看长)；"
                     "②未来均线随horizon向路径价均值回归(maRevertTau=60日)，远端牛熊位置回归中性不再被clamp钉死；"
                     "③路径波动感知带宽(局部vs全程波动比，放大上限×%.1f)，路径高风险段不误报为精确。"
                     "(R132) 经验信号调制：①共识置信度(多窗口共振agree/total=%s)调制方向修正权重(confMult=%.2f，"
                     "共振放大/背离保守，不动R128中枢回归)；②Δ20动量惯性近端偏置(inertiaMaxPts=%.0f分，"
                     "今日Δ20=%s方向短期延续、随horizon衰减)，与R130c象限胜率正交。"
                     "(R133) 信号方向调制：①共振方向(verdict=%s)调制极端区逆势修正(顺势有效/背离→×%.1f保守，"
                     "防趋势延续时逆势修正方向错误；逆势有效维持)；②近端微调优先用分regime条件胜率"
                     "(regimeWin.posPct=%s·N=%s，更贴合当前%s态，缺失回退全局posPct=%s·N=%s)。"
                     "仅供研判参考，不构成置信区间严格含义。"
                     ) % ((_recent_std or _hist_std), _hist_std, _base_std, _hist_mean,
                          _revert_cap_eff * 100,
                          (_regime_center if _regime_center is not None else _hist_mean),
                          _regime, 0.5,
                          (_horizon.get("optimalHorizon") if _horizon else "N/A"), _revert_tau_eff,
                          (_recency.get("drift") if _recency else False), _drift_mult, _revert_cap_eff,
                          (str(_state_pospct) if _state_pospct is not None else "N/A"),
                          (str(_state_n) if _state_n is not None else "N/A"),
                          _mom_win_eff, 1.6,
                          (str(_conf_val) if _conf_val is not None else "N/A"), _conf_mult_val, 8.0,
                          (("%+.1f" % _hs0) if _hs0 is not None else "N/A"),
                          (str(_verdict_val) if _verdict_val is not None else "N/A"), _verdict_mult_val,
                          (str(_rw_pospct) if _rw_pospct is not None else "N/A"),
                          (str(_rw_n) if _rw_n is not None else "N/A"),
                          ("熊市" if _regime == "bear" else "牛市"),
                          (str(_state.get("posPct")) if (_state and _state.get("posPct") is not None) else "N/A"),
                          (str(_state.get("n")) if (_state and _state.get("n") is not None) else "N/A")),
        }

        contra = _contra_stats(D, out["history"], bounds, scale_mode)
        if contra is not None and out.get("today", {}).get("score") is not None:
            # R123a：情绪变化(Δ)预测力统计（R130 已前置计算，此处复用）
            delta = _delta
            if delta is not None:
                contra["delta"] = delta
            # R123c：分 regime 信号强度直读（复用 split 分态统计，避免重复口径）
            _spb = (contra.get("split") or {}).get(contra.get("regime"), {}).get("bands") or []
            _cur = next((b for b in _spb if b.get("label") == out["today"].get("label")), None)
            if _cur is not None:
                _f = _cur.get("fwd20")
                if _f is not None:
                    _strong = "显著" if abs(_f) >= 1.0 else "偏弱"
                    contra["regimeSummary"] = (
                        "当前为%s态：情绪%s档 N=%d，此后20日平均收益 %s%%，逆势信号%s（%s）。"
                        % (("熊市" if contra.get("regime") == "bear" else "牛市"),
                           out["today"].get("label"), _cur["n"],
                           ("+" + str(_f) if _f >= 0 else str(_f)),
                           _strong, ("历史实证支持逆向操作" if _strong == "显著" else "信号不稳固、仅作参考")))
                else:
                    contra["regimeSummary"] = (
                        "当前为%s态：情绪%s档样本不足(N=%d)，逆势信号暂无统计支撑，仅作参考。"
                        % (("熊市" if contra.get("regime") == "bear" else "牛市"),
                           out["today"].get("label"), _cur["n"]))
            # R124a/b/c：下一层预测力增强（均为 contra 下独立字段，不改 today.dims、不改今日展示口径）
            # R130：_horizon/_state/_recency 已在 forecast 前前置计算并供 forecast 自适应增强使用，此处复用
            if _horizon is not None:
                contra["horizonScan"] = _horizon
            if _state is not None:
                contra["stateSignal"] = _state
            if _recency is not None:
                contra["recency"] = _recency
            # R125a/R129：极值反转诊断（逆向投资核心信号）；_ext 已在 forecast 前计算并供方向修正使用，此处复用
            if _ext is not None:
                contra["extremeReversal"] = _ext
            out["today"]["contra"] = contra
        # R123a/b：今日情绪变化(Δ)与 z 分位（取自 history 末点，单一真值派生，不新增维度）
        if out.get("today", {}).get("score") is not None:
            _last = out["history"][-1] if out.get("history") else {}
            out["today"]["sentimentChange"] = {"d5": _last.get("d5"), "d20": _last.get("d20")}
            out["today"]["zscore"] = _last.get("z")

        out["scale"] = {
            "mode": scale_mode,
            "bounds": [round(b, 1) for b in bounds],
            "pctiles": ([round(scale_pct[0], 1), round(scale_pct[1], 1),
                         round(scale_pct[2], 1), round(scale_pct[3], 1)] if scale_pct else None),
            "note": ("动态分位标尺：基于近 %d 个交易日情绪分布 p10/p30/p70/p90 切五档"
                     "（bounds=%.1f/%.1f/%.1f/%.1f），提升区分度；today/history/forecast 同标尺。"
                     % (len(hist), bounds[0], bounds[1], bounds[2], bounds[3]))
                    if scale_mode == "dynamic" else
                    "固定标尺（样本不足或分布退化，回退 20/40/60/80 五档）",
        }
        out["note"] = ("代理情绪温度（monitor_only）：由上证量能/动量/波动/牛熊位置 + "
                       "宽基与跨市场广度合成，非全市场涨跌家数；量能维度受跨源 volume 单位差异影响，"
                       "仅供研判参考，不参与任何概率/方向计算。"
                       "history 为全部可用交易日逐日回算（约 5 年）；forecast 由 subForecast 价格路径派生"
                       "（量能/波动随 horizon 指数回归历史中枢、广度沿用当前值，仅动量+位置随路径变化），"
                       "为路径派生预测非因子预测。"
                       "广度维度（R122c→R141）：仅用 breadthAvailable>0 的『可用源』均值（缺失源不参与、不拖累），"
                        "经 (b-0.5)*2 映射回 [-1,1] 信号维度；当前唯一可用源为 crossMarket.breadth（恒生/标普/纳指"
                        "方向系数均值，语义为『跨市场/全球风险偏好同向确认度』），而非 A股内部涨跌家数"
                        "（R271 涨跌家数约束仍不可破）；两源全缺失才诚实归零。"
                       "五档定性（R122a）：采用动态分位标尺（mode=%s），非固定阈值。R123：新增情绪20日变化(Δ)与滚动z分位(近120日)，并给出当前regime下信号强度直读。"
                       "R124：在 R123 基础上再增强预测力——①最优预测窗口扫描(逆势信号最强H∈{5,10,20,40,60})；②组合状态信号(当前水平档×Δ方向四象限经验胜率，纯样本频率非拟合)；③近期权重稳健性对照(等权 vs 近1年衰减加权，regime漂移诊断)。R125：在 R124 基础上再增强——①极值反转诊断(恐慌区后反弹/狂热区后回落的逆向反转概率，逆向投资核心)；②多窗口信号共振/背离(各H窗口方向一致性，共振=高置信)；③分regime条件胜率(当前regime下该组合状态的经验上涨概率，比不分regime更细)。均为contra下独立诊断字段，不改dims、不改今日展示口径。"
                       "R129：在 R125 基础上把已验证诊断『反馈进 forecast 方向修正』而非仅展示——极值反转概率驱动极端区逆势回归、分regime均衡中枢驱动远端条件偏置，使预测方向带经验修正；透明参数见 forecastBand.regimeBiasCenter/regimeBiasW/extremeBiasW/extremeBiasMethod。"
                       "R130：在 R129 基础上对 forecast 做『预测自适应增强』——①最优逆势窗口H驱动回归时标(数据驱动tau，H小回归快/H大回归慢，贴合当前逆势周期)；②regime漂移期置信带宽×1.2、远端回归上限0.5→0.35(不确定期更谦卑，不锚定漂移中枢)；③近端按当前状态象限历史上涨概率posPct微调(权重0.15随horizon衰减、仅N>=20生效，与远端中枢回归正交)；透明参数见 forecastBand.revertTauEff/driftMult/stateBiasW/stateBiasMethod。"
                       "R131：在 R130 基础上对 forecast 做『路径派生诚实化』——①动量回看窗口由最优窗口H驱动(momWinEff，H大→慢变趋势→回看长，与R130a同源)；②未来均线随horizon向路径价均值回归(maRevertTau=60日)，远端牛熊位置回归中性、不再被clamp钉死在±1；③路径波动感知置信带(局部vs全程波动比，放大上限×1.6)，路径高风险段不误报为精确；透明参数见 forecastBand.momWinEff/maRevertTau/pathVolMultMax/pathVolMethod。"
                       "R132：在 R131 基础上对 forecast 做『经验信号调制』——①共识置信度调制(多窗口共振agree/total∈[0,1]→方向修正权重×confMult∈[0.5,1.5]，共振放大/背离保守，只在方向修正层生效、不动R128中枢回归)；②Δ20动量惯性近端偏置(情绪自相关——今日Δ20升/降方向短期延续，近端按Δ20方向小幅偏置、随horizon指数衰减，与R130c象限历史胜率正交)；透明参数见 forecastBand.consensusConf/confMult/inertiaTau/inertiaMaxPts/inertiaMethod。"
                       "R133：在 R132 基础上对 forecast 做『信号方向调制』——①共振方向调制(consensus.verdict=共振(顺势有效)表示高情绪后涨/低情绪后跌的趋势延续强，此时极端区逆势修正方向可能相反→修正权重×0.5保守化防方向性错误；背离亦×0.5；逆势有效维持)；②近端微调优先用分regime条件胜率regimeWin.posPct(当前牛/熊态下该组合状态的条件胜率，R125已验证熊市态中升51%%>全样本47%%，更贴合当前regime；N>=20生效，缺失回退全局posPct)；透明参数见 forecastBand.verdict/verdictMult/regimeWinN/posPctUsed。"
                       "R142：在 R133 基础上做『分regime滚动分位归一抗漂移』——熊/牛regime情绪中枢系统性差~19点(bear 42.6/bull 61.7)，全局分位在牛熊切换时语义失真；故对今日与history逐点改算『同regime近250日滚动分位 regimePct』(仅与同regime样本比，抗中枢系统差)，直接回答『当前情绪在当下牛熊态内有多极端』；并据此调制R140 regime水平位移权重levelW(今日已处regime极端区则减力防过度修正、regime中位维持满权0.35，clamp下限0.3)，使预测在牛熊切换时更稳。history逐点含regimePct、today含regimePct；透明参数见 forecastBand.regimePctileToday/regimePctileMethod/levelWMod。"
                       % out["scale"]["mode"])
    except Exception as e:  # 降级：在场但不失真，绝不阻断当日推送
        out["error"] = "gen_sentiment 异常: %s" % e
        out["today"] = {"score": None, "label": "数据不足"}
        out["history"] = []
        out["forecast"] = []
    finally:
        os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
        with open(OUT_JSON, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        with open(OUT_JSON.replace(".json", ".js"), "w",  encoding="utf-8") as f:
            f.write("window.SENTIMENT = ")
            json.dump(out, f, ensure_ascii=False)
            f.write(";")

    print("=== 市场情绪温度 (R87 · monitor_only · v3) ===")
    print("asOf      = %s" % out.get("asOf"))
    t = out.get("today") or {}
    print("today     = %s  label=%s" % (t.get("score"), t.get("label")))
    print("history   = %d 点" % len(out.get("history") or []))
    print("forecast  = %d 点" % len(out.get("forecast") or []))
    sc = out.get("scale") or {}
    if sc:
        print("scale     = mode=%s bounds=%s" % (sc.get("mode"), sc.get("bounds")))
    if t.get("dims"):
        for d in t["dims"]:
            print("  %-10s w=%.2f sub=%+.3f  %s" % (d["name"], d["weight"], d["sub"],
                                                     json.dumps({kk: vv for kk, vv in d.items()
                                                                 if kk not in ("name", "weight", "sub")},
                                                                ensure_ascii=False)))
    if out.get("error"):
        print("[WARN] %s" % out["error"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
