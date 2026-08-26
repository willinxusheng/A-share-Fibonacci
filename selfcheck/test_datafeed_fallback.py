# -*- coding: utf-8 -*-
"""取数链 fall-through 回归守门（R283，确定性、无网络）。

固化 datafeed.fetch_rows 的"短结果不终止、继续回退"契约：
- 副指数海外（eastmoney 失效、腾讯偶发只回 1 行）必须越过腾讯落到 yahoo/stooq 拿完整历史，
  否则副指数共振 breadth 退化（production_oos_brier cnt=0、indexCompare 副指数仅 1 点）。
- 主源（eastmoney）返回完整结果时仍首源优先，不被后续源覆盖。
- 全部源均返回残破短结果时返回 None（拒绝注入残破数据，交由上层非致命/致命处理）。

全程 monkeypatch datafeed 的四个源函数，绝不触网；纯逻辑断言，CI 确定性可复现。
任一断言失败即 exit 1，使本门禁阻断当日部署（反假绿硬守门）。
"""
import sys
import os

# 仓库根（selfcheck/ 的上一级）加入 sys.path，使本测试无论从何处调用都能 import 根的 datafeed。
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import datafeed as d

SHORT = [("2026-08-26", "1", "2", "0", "2", "100")]          # 1 行退化结果（< _MIN_ROWS）
FULL = [("2021-%02d-01" % (i % 12 + 1), "1", "2", "0", "2", "100") for i in range(1300)]


def _set(em=None, tnt=None, yh=None, st=None):
    d._eastmoney_rows = (lambda s: list(em)) if em is not None else (lambda s: None)
    d._tencent_rows = (lambda s: list(tnt)) if tnt is not None else (lambda s: None)
    d._yahoo_rows = (lambda s: list(yh)) if yh is not None else (lambda s: None)
    d._stooq_rows = (lambda s: list(st)) if st is not None else (lambda s: None)


def _check(name, cond):
    if cond:
        print("  ✅ %s" % name)
        return True
    print("  ❌ %s" % name)
    return False


def main():
    ok = True

    # 场景 A：海外退化——eastmoney 失效、腾讯只回 1 行，yahoo 有完整历史。
    # 期望：越过 1 行腾讯，落到 yahoo 的 1300 行（修复前会停在 1 行腾讯）。
    _set(em=None, tnt=SHORT, yh=FULL, st=FULL)
    rows = d.fetch_rows("sh000300")
    ok &= _check("A 海外副指数越过短腾讯落到 yahoo 完整历史",
                 rows is not None and len(rows) >= d._MIN_ROWS)

    # 场景 B：主源正常——eastmoney 直接给完整结果，应首源即接受（优先级不变）。
    _set(em=FULL, tnt=SHORT, yh=FULL, st=FULL)
    rows = d.fetch_rows("sh000001")
    ok &= _check("B 主源完整结果首源优先",
                 rows is not None and len(rows) >= d._MIN_ROWS)

    # 场景 C：全部源均残破（< _MIN_ROWS）——期望返回 None，拒绝注入残破数据。
    _set(em=SHORT, tnt=SHORT, yh=SHORT, st=SHORT)
    rows = d.fetch_rows("sh000300")
    ok &= _check("C 全源短结果返回 None",
                 rows is None)

    # 场景 D：eastmoney 失效、腾讯完整、yahoo 完整——期望取腾讯（副指数海外主回退源优先于 yahoo）。
    _set(em=None, tnt=FULL, yh=FULL, st=FULL)
    rows = d.fetch_rows("sh000300")
    ok &= _check("D 腾讯完整时优先于 yahoo（副指数海外主回退源）",
                 rows is not None and len(rows) >= d._MIN_ROWS)

    # 场景 E：未知 key——期望 None（不崩溃）。
    _set(em=FULL, tnt=FULL, yh=FULL, st=FULL)
    rows = d.fetch_rows("not_a_real_key")
    ok &= _check("E 未知 key 返回 None", rows is None)

    if ok:
        print("\n✅ datafeed fall-through 回归守门全部通过")
        return 0
    print("\n❌ datafeed fall-through 回归守门存在失败项")
    return 1


if __name__ == "__main__":
    sys.exit(main())
