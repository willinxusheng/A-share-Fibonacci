# -*- coding: utf-8 -*-
"""gen_chanlun_view.py — 生成 data/chanlun_view.js（window.CHANLUN_VIEW）。

把本地 chanlun 项目对 sh000001 的推演快照接入斐波那契看板，作为「缠论视角」对照面板。
与 build_data.py 解耦：独立文件、独立刷新，不会被 build_data 覆盖；云端 daily.yml 若后续
接入 chanlun 步骤即可每日自动刷新（v2）。当前为本地手动生成快照。

用法：
  python gen_chanlun_view.py            # 读 CHANLUN_DIR/data.json → 写 data/chanlun_view.js
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# chanlun 项目路径（本机）；CI v2 接入时改为仓库内子模块/副本
CHANLUN_DIR = os.environ.get(
    "CHANLUN_DIR",
    r"C:/Users/Administrator/WorkBuddy/2026-08-16-16-37-17/chanlun",
)
sys.path.insert(0, CHANLUN_DIR)

from chanlun import analyze, adaptive_horizon, forecast_confidence  # noqa: E402
from report import forecast_svg  # noqa: E402

SYM = "sh000001"
WANT = [8, 15, 20, 30]  # 关键 horizon


def main():
    data = json.load(open(os.path.join(CHANLUN_DIR, "data.json"), encoding="utf-8"))
    if SYM not in data:
        raise SystemExit("data.json 缺少 %s" % SYM)
    kl = sorted(data[SYM]["klines"], key=lambda k: k["date"])
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
        "name": data[SYM].get("name", "上证指数"),
        "lastDate": kl[-1]["date"],
        "lastClose": round(kl[-1]["close"], 2),
        "scenario": cls.get("scenario") if isinstance(cls, dict) else cls,
        "confidence": conf,
        "adaptiveHorizon": horizon,
        "keyProjection": keys,
        "dip": {"t": dip["t"], "main": dip["main"], "lo": dip["lo"], "hi": dip["hi"]},
        "tail": {"t": tail["t"], "main": tail["main"], "hi": tail["hi"], "lo": tail["lo"]},
        "generatedBy": "gen_chanlun_view.py (local snapshot; v2=CI daily refresh)",
    }
    out = "window.CHANLUN_VIEW = %s;\n" % json.dumps(view, ensure_ascii=False, indent=2)
    dest = os.path.join(HERE, "data", "chanlun_view.js")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(out)
    print("written", dest)
    print(json.dumps(view, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
