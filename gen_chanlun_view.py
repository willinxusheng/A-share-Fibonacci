# -*- coding: utf-8 -*-
"""gen_chanlun_view.py — 生成 data/chanlun_view.js（window.CHANLUN_VIEW）。

把缠论流水线对 sh000001 的推演快照接入斐波那契看板，作为「缠论视角」对照面板。
与 build_data.py 解耦：独立文件、独立刷新，不会被 build_data 覆盖。

v2（每日自动刷新）：
  - 缠论分析模块 vendored 进 lib/chanlun/（与 lib/price-action 同惯例，CI 自包含、零第三方依赖）。
  - 输入复用 Fibonacci 管线已抓取的主力指数日线 data/sh000001.csv（单数据源、云端必成功，
    不再走 chanlun 原 fetch_data 的中国源，避免海外 runner 取数失败）。
  - daily.yml 在 build_data 之后、Commit&push 之前以 best-effort(continue-on-error) 调用本脚本，
    chanlun_view.js 随 data/ 一并提交；若缠论步骤失败，Fib 主看板照常部署，互不阻塞。

本地手动运行：
  python gen_chanlun_view.py                        # 默认读 data/sh000001.csv
  CL_KLINES_CSV=/path/to/sh000001.csv python gen_chanlun_view.py
  CL_LEGACY=1 python gen_chanlun_view.py            # 旧式：读 CHANLUN_DIR/data.json（脱离 Fib CSV 时）
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# vendored 缠论模块（CI 自包含，零第三方依赖）
CHANLUN_DIR = os.environ.get("CHANLUN_DIR", os.path.join(HERE, "lib", "chanlun"))
sys.path.insert(0, CHANLUN_DIR)

from chanlun import analyze, adaptive_horizon, forecast_confidence  # noqa: E402
from report import forecast_svg  # noqa: E402

SYM = "sh000001"
SYM_NAME = {"sh000001": "上证指数"}
WANT = [8, 15, 20, 30]  # 关键 horizon


def load_klines():
    """优先读 Fibonacci 已抓的主力日线 CSV（单数据源）；可选 legacy data.json 回退。"""
    csv_path = os.environ.get("CL_KLINES_CSV") or os.path.join(HERE, "data", "sh000001.csv")
    if os.path.exists(csv_path):
        kl = []
        with open(csv_path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                kl.append({
                    "date": row["date"],
                    "open": float(row["open"]),
                    "close": float(row["close"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "volume": float(row.get("volume") or 0.0),
                })
        if kl:
            return kl, SYM_NAME.get(SYM, SYM)
    # legacy 回退：指向含 data.json 的 chanlun 项目根目录
    legacy = os.path.join(CHANLUN_DIR, "data.json")
    if os.environ.get("CL_LEGACY") and os.path.exists(legacy):
        data = json.load(open(legacy, encoding="utf-8"))
        if SYM in data:
            kl = sorted(data[SYM]["klines"], key=lambda k: k["date"])
            return kl, data[SYM].get("name", SYM_NAME.get(SYM, SYM))
    raise SystemExit("无法加载 sh000001 日线：既无 %s，也无 legacy data.json" % csv_path)


def main():
    kl, name = load_klines()
    r = analyze(kl)
    cls = r.get("classify")
    horizon = adaptive_horizon(r["bis"], r["merged"])
    _svg, _note, _probs, _leg, fc = forecast_svg(kl, r, cls, 50.0, 0.0, SYM, horizon)
    conf = forecast_confidence(r, cls, {})
    proj = fc.get("proj", [])

    # 每个 tplus 取首条主路径（叙事主线），构建精简路径
    path = []
    by_t = {}
    for p in proj:
        tp = p.get("tplus")
        by_t.setdefault(tp, []).append(p)
    for tp in sorted(by_t.keys()):
        p0 = by_t[tp][0]  # 主线
        path.append({
            "t": tp,
            "main": round(p0["main"], 2),
            "med": round(p0["med"], 2),
            "lo": round(p0["f95l"], 2),
            "hi": round(p0["f95l"] + p0["f95h"], 2),
        })
    # 关键 horizon 抽取
    keys = []
    for tp in WANT:
        if tp in by_t:
            p0 = by_t[tp][0]
            keys.append({
                "t": tp,
                "main": round(p0["main"], 2),
                "med": round(p0["med"], 2),
                "lo": round(p0["f95l"], 2),
                "hi": round(p0["f95l"] + p0["f95h"], 2),
            })
    # 近端最低主路径（洗盘位）与末端主路径（恢复位）
    dip = min(path, key=lambda x: x["main"])
    tail = path[-1]
    view = {
        "symbol": SYM,
        "name": name,
        "lastDate": kl[-1]["date"],
        "lastClose": round(kl[-1]["close"], 2),
        "scenario": cls.get("scenario") if isinstance(cls, dict) else cls,
        "confidence": conf,
        "adaptiveHorizon": horizon,
        "keyProjection": keys,
        "dip": {"t": dip["t"], "main": dip["main"], "lo": dip["lo"], "hi": dip["hi"]},
        "tail": {"t": tail["t"], "main": tail["main"], "hi": tail["hi"], "lo": tail["lo"]},
        "generatedBy": "gen_chanlun_view.py v2 (CI daily refresh from data/sh000001.csv)",
        "klinesSource": "data/sh000001.csv",
    }
    out = "window.CHANLUN_VIEW = %s;\n" % json.dumps(view, ensure_ascii=False, indent=2)
    dest = os.path.join(HERE, "data", "chanlun_view.js")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(out)
    print("written", dest)
    print(json.dumps(view, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
