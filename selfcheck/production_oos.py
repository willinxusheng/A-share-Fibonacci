# -*- coding: utf-8 -*-
"""生产线 OOS Brier 守门器（修复 oos_guard 假绿：R85 纪律真正守护生产概率引擎）。

为什么需要：
  原 oos_guard 守护的 metric 来自 R217_segcal_check.run_oos() 的 bucket_oos_brier，
  它只复刻"裸首达公式 + 训练集分桶经验校准"，完全不含生产真实合成路径
  (build_data._enrich 的 empirical 融合/_FUSE_K/_hist_calib/_breadth 共振)，也不 import build_data。
  已定量证实：改生产融合核心参数 _FUSE_K 会让生产 OOS Brier 变化 >5%，但 bucket_oos_brier 纹丝不动
  → 原守门员对引擎改动假绿（R85 最该警惕的那类）。

本模块改用 oos_breadth.compute_production_oos_brier()（逐字复刻 build_data._enrich 的生产啮合式
walk-forward OOS Brier，含 empirical 融合/_FUSE_K/_hist_calib/共振），使其对引擎核心改动敏感。
只读 git 跟踪的 data/data.js + data/sh000001.csv，跨日稳定、确定性守门。

返回 None 表示无样本（数据缺失），调用方应安全降级（不阻断也不漏报，交由其它守门员兜底）。
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

try:
    import oos_breadth as _ob
except SystemExit:
    # oos_breadth 在样本为 0 时会 raise SystemExit(1)；视为无样本，安全降级
    _ob = None


def compute():
    """返回生产啮合式 OOS Brier（= brier2/cnt，生产现状 2只宽基口径），无样本返回 None。"""
    if _ob is None:
        return None
    brier2, _brier5, cnt, _per2, _per5 = _ob.compute_production_oos_brier()
    if cnt == 0:
        return None
    return brier2 / cnt


if __name__ == "__main__":
    v = compute()
    print("production_oos_brier (生产啮合式 OOS Brier) = %s" % ("%.4f" % v if v is not None else "None(无样本)"))
