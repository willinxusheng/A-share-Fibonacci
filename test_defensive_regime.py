# -*- coding: utf-8 -*-
"""#779 / #780 受控测试：衰竭顶·基准转防守（regime-aware 方向修正）。

覆盖：
  T1 _defensive_reversion_target 数学（合理性 / 单调性 / 夹紧 / 铁律⑦无关）
  T2 _apply_defensive_regime 降级标记：艾略特价位/概率一律不动(铁律⑦) + baseCase/conditional 正确
  T3 extract_targets 读取 baseCase/conditional + 独立防御基准目标(defensiveScenario)
  T4 archive UPDATE-aware 幂等：同日二次写就地更新、不新增行、不破坏分母
  T5 aggregate / run_backtest 的 baseCaseDirRate（仅 baseCase=True 样本的方向准确率）

运行：python test_defensive_regime.py   （退出码 0=全过，1=有失败）
"""
import json
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as bt
import build_data as bd


def _fail(msg):
    print("  ✗ " + msg)
    raise AssertionError(msg)


def test_math():
    print("[T1] _defensive_reversion_target 数学 ...")
    # 极端高位 + RSI 超买 -> 趋向浪④底(教科书"失败浪回踩浪④")，非崩盘式回撤
    tg, pct = bd._defensive_reversion_target(4493.0, 3741.11, 70.0, 20)
    if not (3.0 <= pct <= 60.0):
        _fail("回撤比例越界: %.1f%%" % pct)
    if not (tg < 4493.0 and tg >= 3741.11 - 1e-6):
        _fail("防御目标应介于当前价与浪④底之间: %.2f" % tg)
    # 单调性：RSI 越高 / 偏离越大 -> 回撤比例越大（更趋向浪④底）
    _, p1 = bd._defensive_reversion_target(4493.0, 3741.11, 55.0, 20)
    _, p2 = bd._defensive_reversion_target(4493.0, 3741.11, 70.0, 20)
    if not (p2 > p1):
        _fail("RSI 超买应增大回撤比例: %.1f%% -> %.1f%%" % (p1, p2))
    _, p3 = bd._defensive_reversion_target(4200.0, 3741.11, 70.0, 20)
    if not (p2 > p3):
        _fail("偏离更大应增大回撤比例")
    # 夹紧：即便极端输入，目标也绝不跌破浪④底
    tg4, p4 = bd._defensive_reversion_target(8000.0, 3741.11, 100.0, 20)
    if tg4 < 3741.11 - 1e-6:
        _fail("防御目标不应跌破浪④底: %.2f" % tg4)
    if not (p4 <= 60.0):
        _fail("回撤比例应夹紧 <=60%%: %.1f" % p4)
    # 铁律⑦无关：函数绝不返回艾略特卖点（只生成下行防御价，调用方不改卖点）
    print("  ✓ 数学合理 (例: 4493/RSI70 -> 目标 %.2f, 回撤 %.1f%%)" % (tg, pct))


def test_apply_regime_keeps_prices():
    print("[T2] _apply_defensive_regime 降级标记 + 铁律⑦ ...")
    tp = {"sellTargets": [
        {"name": "卖① 保守兑现", "price": 4372.0, "expDays": 20.0, "prob": 0.62},
        {"name": "卖② 标准兑现", "price": 4493.0, "expDays": 30.0, "prob": 0.48},
    ]}
    sub = {
        "points": [
            {"label": "浪⑤起", "price": 3741.11, "side": "buy"},
            {"label": "子浪ⅰ", "price": 4100.0, "side": "hold"},
            {"label": "子浪ⅲ", "price": 4300.0, "side": "sell"},
            {"label": "子浪ⅴ", "price": 4493.0, "side": "sell"},
        ],
        "rows": [
            {"wave": "子浪ⅰ", "target": 4100.0, "side": "hold"},
            {"wave": "子浪ⅲ", "target": 4300.0, "side": "sell"},
            {"wave": "子浪ⅱ", "target": 3900.0, "side": "buy"},
        ],
    }
    # 快照所有艾略特价位/概率（铁律⑦：必须一字未动）
    _snap = {}
    for _t in tp["sellTargets"]:
        _snap[("sell", _t["name"])] = (_t["price"], _t["prob"])
    for _p in sub["points"]:
        _snap[("pt", _p["label"])] = _p["price"]
    for _r in sub["rows"]:
        _snap[("row", _r["wave"])] = _r["target"]

    bd._apply_defensive_regime(tp, sub)

    # 铁律⑦：价位/概率不变
    for _t in tp["sellTargets"]:
        _p, _pr = _snap[("sell", _t["name"])]
        if _t["price"] != _p or _t["prob"] != _pr:
            _fail("铁律⑦违反：卖点价位/概率被改动 %s" % _t["name"])
    for _p in sub["points"]:
        if _p["price"] != _snap[("pt", _p["label"])]:
            _fail("铁律⑦违反：子浪点价位被改动 %s" % _p["label"])
    # 降级标记正确
    for _t in tp["sellTargets"]:
        if _t.get("baseCase") is not False or _t.get("conditional") is not True:
            _fail("卖点未降级为 conditional: %s" % _t["name"])
    for _p in sub["points"]:
        if _p["side"] in ("sell", "hold"):
            if _p.get("baseCase") is not False:
                _fail("上行子浪点未降级: %s" % _p["label"])
        else:  # buy 侧回踩买点保持基准
            if _p.get("baseCase") is False:
                _fail("买点(浪⑤起)被错误降级: %s" % _p["label"])
    for _r in sub["rows"]:
        if _r["side"] in ("sell", "hold"):
            if _r.get("baseCase") is not False:
                _fail("上行子浪 row 未降级: %s" % _r["wave"])
        else:
            if _r.get("baseCase") is False:
                _fail("买点 row 被错误降级: %s" % _r["wave"])
    print("  ✓ 艾略特卖点未动 + 上行目标降级 conditional + 买点保持基准")


def test_extract_targets():
    print("[T3] extract_targets 读取 baseCase/conditional + 防御基准 ...")
    data = {
        "updated": "2026-08-27",
        "tradePlan": {"sellTargets": [
            {"name": "卖① 保守兑现", "price": 4372.0, "expDays": 20.0,
             "baseCase": False, "conditional": True},
        ]},
        "subForecast": {"points": [
            {"label": "子浪ⅴ", "price": 4493.0, "side": "sell", "expDays": 25.0,
             "baseCase": False, "conditional": True},
            {"label": "浪⑤起", "price": 3741.11, "side": "buy", "expDays": 5.0},
        ]},
        "defensiveScenario": {"target": 3820.0, "retracePct": 15.0, "expDays": 18},
    }
    recs = bt.extract_targets(data)
    _by = {(r["cat"], r["key"]): r for r in recs}
    if _by[("sellTarget", "卖① 保守兑现")]["baseCase"] is not False:
        _fail("extract_targets 未携 baseCase")
    if _by[("sellTarget", "卖① 保守兑现")]["conditional"] is not True:
        _fail("extract_targets 未携 conditional")
    if _by[("subwave", "子浪ⅴ")]["baseCase"] is not False:
        _fail("子浪ⅴ baseCase 未读取")
    if ("subwave", "浪⑤起") in _by and _by[("subwave", "浪⑤起")].get("baseCase") is False:
        _fail("买点子浪被误标 conditional")
    _d = _by.get(("defensive", "均值回归基准"))
    if not _d:
        _fail("未提取防御基准目标")
    if _d["side"] != "buy" or _d["baseCase"] is not True or _d["conditional"] is not False:
        _fail("防御基准目标 schema 错误: %s" % _d)
    if abs(_d["price"] - 3820.0) > 1e-6:
        _fail("防御基准目标价位错误")
    # 无 defensiveScenario 时不应产出防御目标
    data2 = {k: v for k, v in data.items() if k != "defensiveScenario"}
    recs2 = bt.extract_targets(data2)
    if any(r["cat"] == "defensive" for r in recs2):
        _fail("无 defensiveScenario 时不应产防御目标")
    print("  ✓ extract_targets 携标记 + 防御基准(target=3820) + 缺失时跳过")


def test_archive_update_aware():
    print("[T4] archive UPDATE-aware 幂等 ...")
    tmp = tempfile.mktemp(suffix=".jsonl")
    try:
        data = {
            "updated": "2026-08-27",
            "tradePlan": {"sellTargets": [
                {"name": "卖① 保守兑现", "price": 4372.0, "expDays": 20.0},
            ]},
            "subForecast": {"points": [
                {"label": "子浪ⅴ", "price": 4493.0, "side": "sell", "expDays": 25.0},
            ]},
            "defensiveScenario": None,
        }
        with mock.patch.object(bt, "LOG_PATH", tmp):
            bt.archive(data)                      # 首次：写 2 行 baseCase=True(默认)
            # 模拟 warn 后：价格变动 + 降级标记 + 新增防御目标
            data["tradePlan"]["sellTargets"][0]["price"] = 4390.0
            data["tradePlan"]["sellTargets"][0]["baseCase"] = False
            data["tradePlan"]["sellTargets"][0]["conditional"] = True
            data["subForecast"]["points"][0]["baseCase"] = False
            data["subForecast"]["points"][0]["conditional"] = True
            data["defensiveScenario"] = {"target": 3820.0, "retracePct": 15.0, "expDays": 18}
            bt.archive(data)                      # 二次：就地更新 + 新增防御 1 行
        with open(tmp, encoding="utf-8") as f:
            lines = [json.loads(l) for l in f if l.strip()]
        if len(lines) != 3:
            _fail("UPDATE-aware 失败：应有 3 行，实际 %d" % len(lines))
        _m = {(e["date"], e["key"], e["cat"]): e for e in lines}
        _s1 = _m[("2026-08-27", "卖① 保守兑现", "sellTarget")]
        if abs(_s1["price"] - 4390.0) > 1e-6:
            _fail("价格未就地更新: %s" % _s1["price"])
        if _s1.get("baseCase") is not False:
            _fail("baseCase 未就地更新")
        _def = _m.get(("2026-08-27", "均值回归基准", "defensive"))
        if not _def or abs(_def["price"] - 3820.0) > 1e-6:
            _fail("防御目标未新增或价位错")
        print("  ✓ 同日二次写就地更新(价/标记) + 防御新增，无重复行(共 3 行)")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def test_aggregate_base_case_dir():
    print("[T5] aggregate / run_backtest baseCaseDirRate ...")
    recs = [
        # 基准上行目标(5 个全对方向)
        {"cat": "sellTarget", "key": "卖① 保守兑现", "baseCase": True, "dirCorrect": True,
         "matured": True, "evaluated": True, "hit": True, "preciseHit": True, "days_to_hit": 5},
        {"cat": "sellTarget", "key": "卖① 保守兑现", "baseCase": True, "dirCorrect": True,
         "matured": True, "evaluated": True, "hit": True, "preciseHit": True, "days_to_hit": 5},
        {"cat": "sellTarget", "key": "卖① 保守兑现", "baseCase": True, "dirCorrect": True,
         "matured": True, "evaluated": True, "hit": True, "preciseHit": True, "days_to_hit": 5},
        {"cat": "sellTarget", "key": "卖① 保守兑现", "baseCase": True, "dirCorrect": True,
         "matured": True, "evaluated": True, "hit": True, "preciseHit": True, "days_to_hit": 5},
        {"cat": "sellTarget", "key": "卖① 保守兑现", "baseCase": True, "dirCorrect": True,
         "matured": True, "evaluated": True, "hit": True, "preciseHit": True, "days_to_hit": 5},
        # 防御基准(5 个全对方向)
        {"cat": "defensive", "key": "均值回归基准", "baseCase": True, "dirCorrect": True,
         "matured": True, "evaluated": True, "hit": True, "preciseHit": True, "days_to_hit": 5},
        {"cat": "defensive", "key": "均值回归基准", "baseCase": True, "dirCorrect": True,
         "matured": True, "evaluated": True, "hit": True, "preciseHit": True, "days_to_hit": 5},
        {"cat": "defensive", "key": "均值回归基准", "baseCase": True, "dirCorrect": True,
         "matured": True, "evaluated": True, "hit": True, "preciseHit": True, "days_to_hit": 5},
        {"cat": "defensive", "key": "均值回归基准", "baseCase": True, "dirCorrect": True,
         "matured": True, "evaluated": True, "hit": True, "preciseHit": True, "days_to_hit": 5},
        {"cat": "defensive", "key": "均值回归基准", "baseCase": True, "dirCorrect": True,
         "matured": True, "evaluated": True, "hit": True, "preciseHit": True, "days_to_hit": 5},
        # 条件推演(降级)目标：方向错，但不计入 baseCase
        {"cat": "sellTarget", "key": "卖② 标准兑现", "baseCase": False, "dirCorrect": False,
         "matured": True, "evaluated": True, "hit": False, "preciseHit": False, "days_to_hit": None},
    ]
    summ = bt.aggregate(recs)
    _s1 = next(s for s in summ if s["cat"] == "sellTarget" and s["key"] == "卖① 保守兑现")
    _exp = round((5 + 1) / (5 + 2) * 100, 1)
    if _s1["baseCaseDirRate"] != _exp:
        _fail("分组 baseCaseDirRate 错误: %s != %s" % (_s1["baseCaseDirRate"], _exp))
    _c2 = next(s for s in summ if s["key"] == "卖② 标准兑现")
    if _c2["baseCaseDirRate"] is not None:
        _fail("条件目标不应有 baseCaseDirRate")
    # run_backtest 整体：mock evaluate 返回固定 recs，验证整体统计
    tmp = tempfile.mktemp(suffix=".jsonl")
    tmpout = tempfile.mktemp(suffix=".json")
    try:
        data = {"updated": "2026-08-27", "tradePlan": {"sellTargets": []},
                "subForecast": {"points": []}}
        with mock.patch.object(bt, "evaluate", lambda df: recs), \
             mock.patch.object(bt, "LOG_PATH", tmp), \
             mock.patch.object(bt, "OUT_PATH", tmpout):
            stats = bt.run_backtest(data, None)
        if stats["baseCaseDirEvaluated"] != 10:
            _fail("整体 baseCaseDirEvaluated 错误: %s" % stats["baseCaseDirEvaluated"])
        _ovexp = round((10 + 1) / (10 + 2) * 100, 1)
        if stats["baseCaseDirRealizedHitRate"] != _ovexp:
            _fail("整体 baseCaseDirRealizedHitRate 错误: %s != %s"
                  % (stats["baseCaseDirRealizedHitRate"], _ovexp))
        print("  ✓ 分组/整体 baseCaseDirRate 正确 (整体=%s%%)" % stats["baseCaseDirRealizedHitRate"])
    finally:
        for _p in (tmp, tmpout):
            if os.path.exists(_p):
                os.remove(_p)


def main():
    tests = [test_math, test_apply_regime_keeps_prices, test_extract_targets,
             test_archive_update_aware, test_aggregate_base_case_dir]
    _ok = 0
    for t in tests:
        try:
            t()
            _ok += 1
        except AssertionError as e:
            print("  [FAILED] %s: %s" % (t.__name__, e))
            return 1
        except Exception as e:
            print("  [ERROR] %s: %s" % (t.__name__, e))
            import traceback
            traceback.print_exc()
            return 1
    print("\nALL %d TESTS PASSED ✓" % _ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
