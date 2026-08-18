# -*- coding: utf-8 -*-
"""上证指数 5年 艾略特波浪 + 斐波那契分析脚本。
输入: data/sh000001_raw.md (eastmoney HTTP kline 接口输出的 markdown 表格)
输出: data/sh000001.csv, data/structures.json
"""
import json
import os
import sys
from datetime import datetime as _dtmod

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))


def _locate_skill_scripts():
    """定位 wb-finance-skill 的 price-action 脚本目录（含 elliott_wave.py）。

    原代码硬编码 D:\\WorkBuddy——一旦换机或重装到其它盘符即 ImportError 崩溃，
    导致 analyze.py 无法运行、看板刷新失败（破坏"每日自动更新"承诺）。
    改为优先环境变量 WORKBUDDY_HOME，再探测常见安装根（含本机 D 盘兜底）。

    R214：WorkBuddy 新版把内置技能从 resources/app.asar.unpacked/resources/builtin-skills
    迁到了两处新位置，旧路径已不存在（实测 2026-08-14 本机 ModuleNotFoundError）：
      ① 应用内  resources/app.asar.unpacked/resources/plugins/workbuddy-builtin/skills/wb-finance-skill
      ② 用户插件缓存  ~/.workbuddy/plugins/cache/workbuddy-builtin/skill-wb-finance-skill/<版本号>
    两者都加入探测；插件缓存按版本号倒序取最新。

    RCI（云端 CI 自包含）：把 price-action 副本随仓库纳入 lib/price-action/，
    使 GitHub Actions 等云端 runner 在没有任何 WorkBuddy 安装时也能导入
    elliott_wave.py。仓库内副本优先于一切本机路径探测——保证 CI 与本地行为一致、
    且不受重装/换机影响。"""
    import glob as _glob
    # 仓库内自带副本：最高优先级（CI 与本地统一用版本受控的副本）
    repo_local = os.path.join(BASE, "lib", "price-action")
    if os.path.isdir(repo_local):
        return repo_local
    env = os.environ.get("WORKBUDDY_HOME")
    candidates = [
        env,
        os.path.expanduser("~/WorkBuddy"),
        r"C:\Users\Administrator\WorkBuddy",
        r"D:\WorkBuddy",
        r"C:\Program Files\WorkBuddy",
        r"/Applications/WorkBuddy.app/Contents/Resources",
    ]
    for base in candidates:
        if not base:
            continue
        # 新版应用内布局（plugins/workbuddy-builtin/skills）
        p_new = os.path.join(base, "resources", "app.asar.unpacked", "resources",
                             "plugins", "workbuddy-builtin", "skills",
                             "wb-finance-skill", "scripts", "price-action")
        if os.path.isdir(p_new):
            return p_new
        # 旧版布局（builtin-skills，保留兼容）
        p_old = os.path.join(base, "resources", "app.asar.unpacked", "resources",
                             "builtin-skills", "wb-finance-skill", "scripts", "price-action")
        if os.path.isdir(p_old):
            return p_old
    # 用户插件缓存布局（带版本号，取最新版）
    cache_hits = sorted(_glob.glob(os.path.join(
        os.path.expanduser("~"), ".workbuddy", "plugins", "cache", "workbuddy-builtin",
        "skill-wb-finance-skill", "*", "scripts", "price-action")), reverse=True)
    for p in cache_hits:
        if os.path.isdir(p):
            return p
    # 兜底：所有探测均失败 → 明确报错，避免静默指向不存在的 D:\ 路径导致后续 ImportError 难以排查
    raise FileNotFoundError(
        "未找到 wb-finance-skill/price-action 脚本目录。已探测: "
        + ", ".join(c for c in candidates if c)
        + "；以及插件缓存 ~/.workbuddy/plugins/cache/workbuddy-builtin/skill-wb-finance-skill/*/scripts/price-action。"
        + "请确认 WorkBuddy 已安装 wb-finance-skill，或设置环境变量 WORKBUDDY_HOME 指向安装根。")


SKILL_SCRIPTS = _locate_skill_scripts()
sys.path.insert(0, SKILL_SCRIPTS)

from elliott_wave import SignalEngine  # noqa: E402


def read_kline_md(path):
    """表头感知解析跨数据源(westock/eastmoney) markdown K线表。

    不依赖列的绝对位置（westock 列为 date/open/last/high/low/volume/amount…，
    “收盘”列名为 last 而非 close），改为按表头列名建立映射，并对 last/close
    等别名做容错。列序变化（如标准 OHLC）也不会错位。
    """
    def col(parts, *names):
        # 闭包提到循环外，避免每行重复重建函数对象（性能）
        for n in names:
            if n in header:
                idx = header[n]
                # 防御(R170)：单行列数少于表头(rawn.md 偶发残缺行)时，parts[header[n]]
                # 会 IndexError 拖垮整条无人值守管线。改为越界即返回 None，
                # 交由下方 d/c is None 守卫降级跳过该行（与 R148 容错哲学一致）。
                if idx < len(parts):
                    return parts[idx]
                return None
        return None

    def _num(x):
        # 容错解析：去千分位逗号；非数字(如 "-"/""/缺失)返回 None，由调用处降级或跳过该行，
        # 避免 float() 抛未捕获异常拖垮每日自动化（无值守管线健壮性）
        if x is None:
            return None
        try:
            return float(str(x).replace(",", "").strip())
        except (ValueError, TypeError):
            return None

    def _valid_date(s):
        # 纵深防御(R148/R170)：date 列与 open/high/low 数字列同等关键——非法日期字符串
        # ("--"/""/"NOTADATE" 等)若被 ack 进 rows，会在 load_data 的 pd.to_datetime 抛未捕获
        # 异常拖垮整条无人值守管线；空串 "" 则静默产生 NaT 污染 DataFrame 与 CSV 输出(下游
        # sort_values/iloc[-1] 取末值错位)。此处与 _num 同一哲学：非法即跳过该行，不依赖
        # preflight 兜底(手动跑 analyze 或 preflight 漏查时仍能自保)。
        if not s:
            return False
        s = str(s).strip()
        for _f in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                _dtmod.strptime(s, _f)
                return True
            except ValueError:
                continue
        return False

    rows, header = [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("|") or "---" in line:
                continue
            parts = [p.strip() for p in line.strip("|").split("|")]
            if header is None:                      # 首行为表头，建列名->索引映射
                header = {h.lower(): i for i, h in enumerate(parts)}
                continue
            # 按列名映射(而非列数)判定有效性：westock 当前对所有指数返回完整 8 列
            # (date/open/last/high/low/volume/amount/exchange，含 low/volume)，
            # 但历史上曾出现列集不全/变动的情况，故不依赖列数硬跳过(旧式 len<6 会整表丢弃
            # → 上证无法进入跨指数共振、且主管线 load_data 因 0 行崩)。改为仅要求含必要列
            # (date+收盘)，缺失列由下方 d/c is None 守卫降级兜底(low→close、volume→0)，
            # 列数不再卡死；该兜底仅为极端容错，正常取数不触发(R170 实测 raw.md 含 full OHLC)。
            if len(parts) < 2:
                continue

            d = col(parts, "date")
            o = col(parts, "open")
            c = col(parts, "last", "close")         # westock 收盘列名为 last
            h = col(parts, "high")
            lo = col(parts, "low")
            v = col(parts, "volume")
            if not _valid_date(d) or c is None:     # date 非法(含 None/空/乱码)整行跳过，防 to_datetime 崩/NaT 污染
                continue
            _c = _num(c)
            if _c is None:                          # 收盘缺失/非数字 → 该行无效，跳过而非崩溃
                continue
            _o = _num(o); _h = _num(h); _lo = _num(lo); _v = _num(v)
            rows.append({
                "date": d,
                "open": _o if _o is not None else _c,
                "close": _c,
                "high": _h if _h is not None else _c,
                "low": _lo if _lo is not None else _c,
                "volume": _v if _v is not None else 0.0,
            })
    return rows


def load_data():
    raw_path = os.path.join(BASE, "data", "sh000001_raw.md")
    rows = read_kline_md(raw_path)
    # 纵深防御（R148）：westock 取数失败时 read_kline_md 可能返回极少/0 行。
    # 若此处不拦截，下方 df["date"] 会因空表 KeyError 崩，或写出空 CSV 污染下游。
    # preflight.py 已在 analyze 之前做格式预检；此断言为手动运行时的兜底硬闸。
    if len(rows) < 600:
        raise RuntimeError(
            "K线行数过少(%d<600)：westock 取数可能失败/截断。请先运行 preflight.py 排查 raw.md 格式。"
            % len(rows))
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    # ---------- 交易日缺口补全（R-新，防今日涨幅等静默错算）----------
    # 取数源(eastmoney/yahoo/stooq)偶发缺失某交易日(如 T+1 延迟)：主源返回的序列有缺口但不算
    # 失败，_ensure_raw 的"整源失败才回退"不会触发，缺口静默流入 csv/kline，导致前端"今日涨幅"
    # 把倒数第二根(非昨收)当基准、zigzag/MA 等连续性指标错算。此处用多渠道历史**真实**数据
    # 补全缺失交易日——绝不编造，仅补确有数据的缺失日：
    #   ① .idx_cache/<同名>.md（上次成功 fetch 的本地缓存，含最新历史）
    #   ② git HEAD 版 sh000001.csv（历史真实收盘）
    _cols = ["date", "open", "close", "high", "low", "volume"]
    df = df[[c for c in _cols if c in df.columns]]
    # 纵深防御：read_kline_md 若因取数源格式变化返回非标准列名(如 'last' 而非 'close')，
    # 上一步过滤会静默丢弃核心列，写出缺列 csv → build_data 读时 KeyError（静默失败）。
    # 在此硬失败，而非静默丢列。
    for _must in ("date", "open", "close", "high", "low"):
        if _must not in df.columns:
            raise RuntimeError(
                "K线核心列缺失 '%s'：read_kline_md 返回列名=%s，取数源格式可能变化。"
                "请检查 fetch_indices.py / datafeed.py。" % (_must, list(df.columns)))
    _gap_sources = []
    _cache_p = os.path.join(BASE, "data", ".idx_cache", os.path.basename(raw_path))
    if os.path.exists(_cache_p):
        try:
            _gap_sources.append(pd.DataFrame(read_kline_md(_cache_p)))
        except Exception:
            pass
    _hist_csv = os.path.join(BASE, "data", "sh000001.csv")
    if os.path.exists(_hist_csv):
        try:
            _gap_sources.append(pd.read_csv(_hist_csv))
        except Exception:
            pass
    if _gap_sources:
        try:
            _bd = pd.bdate_range(df["date"].min(), df["date"].max())
            _have = set(df["date"])
            _miss = [d for d in _bd if d not in _have]
            if _miss:
                _fills = []
                for _s in _gap_sources:
                    _s2 = _s.copy()
                    if "date" in _s2.columns:
                        _s2["date"] = pd.to_datetime(_s2["date"], errors="coerce")
                        _s2 = _s2[[c for c in _cols if c in _s2.columns]]
                        _fills.append(_s2[_s2["date"].isin(_miss)])
                if _fills:
                    _fill = pd.concat(_fills, ignore_index=True).drop_duplicates("date")
                    if len(_fill):
                        df = pd.concat([df, _fill], ignore_index=True).drop_duplicates("date")
                        print("  [gap-fill] 补全 %d 个缺失交易日: %s"
                              % (len(_fill), [str(d.date()) for d in _fill["date"]]))
        except Exception as e:
            print("  [warn] 缺口补全失败(跳过): %s" % e)
    df = df.sort_values("date").set_index("date")
    df.to_csv(os.path.join(BASE, "data", "sh000001.csv"))
    return df


def zigzag_pct(df, pct=0.08):
    """百分比反转 Zigzag，用于大浪级标注。"""
    highs = df["high"].values
    lows = df["low"].values
    idx = df.index
    pivots = []
    # 初始化趋势；同时跟踪运行高低点，使首 pivot 落在真正极值处
    trend = None
    last_low, last_low_idx = lows[0], 0          # 运行最低价及其位置
    last_high, last_high_idx = highs[0], 0       # 运行最高价及其位置
    extreme_idx = 0
    extreme_price = highs[0]
    for i in range(1, len(df)):
        if trend is None:
            # 持续跟踪运行高低点（不只跟踪低点），避免首 pivot 错落在起点价
            if lows[i] < last_low:
                last_low, last_low_idx = lows[i], i
            if highs[i] > last_high:
                last_high, last_high_idx = highs[i], i
            if highs[i] >= last_low * (1 + pct):
                # 自运行低点向上突破 → 首 pivot 为 L（落在运行最低处，而非死板用起点价）
                trend = "up"
                pivots.append({"index": idx[last_low_idx], "price": float(last_low), "type": "L"})
                extreme_idx = i; extreme_price = highs[i]
            elif lows[i] <= last_high * (1 - pct):
                # 自运行高点向下突破 → 首 pivot 为 H（落在运行最高处；旧写法误用运行低点为基准）
                trend = "down"
                pivots.append({"index": idx[last_high_idx], "price": float(last_high), "type": "H"})
                extreme_idx = i; extreme_price = lows[i]
        elif trend == "up":
            if highs[i] > extreme_price:
                extreme_price = highs[i]; extreme_idx = i
            elif lows[i] <= extreme_price * (1 - pct):
                pivots.append({"index": idx[extreme_idx], "price": float(extreme_price), "type": "H"})
                trend = "down"
                extreme_idx = i; extreme_price = lows[i]
        else:
            if lows[i] < extreme_price:
                extreme_price = lows[i]; extreme_idx = i
            elif highs[i] >= extreme_price * (1 + pct):
                pivots.append({"index": idx[extreme_idx], "price": float(extreme_price), "type": "L"})
                trend = "up"
                extreme_idx = i; extreme_price = highs[i]
    # 末尾未确认极值也纳入（标注为未确认）；仅当趋势已确认，避免 trend=None 时
    # 把起点最高价 highs[0] 误标为“低点 L”造成标注方向错配
    if trend is not None:
        pivots.append({"index": idx[extreme_idx], "price": float(extreme_price),
                       "type": "H" if trend == "up" else "L", "unconfirmed": True})
    return pivots


def fmt_pt(p):
    return {"date": p["index"].strftime("%Y-%m-%d"), "price": round(p["price"], 2), "type": p["type"]}


def main():
    df = load_data()
    print(f"数据: {len(df)} 根K线, {df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d}")
    print(f"区间最高: {df['high'].max():.2f} ({df['high'].idxmax():%Y-%m-%d})")
    print(f"区间最低: {df['low'].min():.2f} ({df['low'].idxmin():%Y-%m-%d})")
    print(f"最新收盘: {df['close'].iloc[-1]:.2f}")

    # 1) 引擎信号
    engine = SignalEngine(swing_window=10)
    signals = engine.generate({"sh000001": df})["sh000001"]
    sig_list = [{"date": d.strftime("%Y-%m-%d"), "signal": int(s)}
                for d, s in signals.items() if s != 0]
    print(f"\n引擎信号 {len(sig_list)} 个:")
    for s in sig_list[-10:]:
        print("  ", s)

    # 2) 中级别浪型结构（impulses/abcs）已移除：该识别输出从未被 build_data.py 或
    #    前端消费（看板只用 D.zigzag 大级别 8% Zigzag 与 D.signals），且依赖引擎私有
    #    方法 _find_swings，保留徒增脆弱面，故删除该段计算。

    # 3) 大级别 zigzag (8%)
    zz = zigzag_pct(df, 0.08)
    print(f"\n大级别摆动点 ({len(zz)} 个):")
    for p in zz:
        mark = " (未确认)" if p.get("unconfirmed") else ""
        print(f"  {p['index']:%Y-%m-%d} {p['type']} {p['price']:.2f}{mark}")

    # 输出原始结果供下一步使用
    out = {
        "signals": sig_list,
        "zigzag": [{**fmt_pt(p), "unconfirmed": bool(p.get("unconfirmed", False))} for p in zz],
    }
    with open(os.path.join(BASE, "data", "structures.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n已保存 data/sh000001.csv 与 data/structures.json")


if __name__ == "__main__":
    main()
