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

# 直接导入生产 OOS 真源 oos_breadth（仓根模块，REPO 已入 sys.path）。
# 注意：oos_breadth 仅在 __main__ 入口（cnt==0 时）raise SystemExit(1)，模块 import
# 阶段不会抛 SystemExit；若 data.js/structures.json 缺失或损坏导致 import 失败，
# 异常按 CI 门禁设计上抛 fail-loud（exit 1 阻断部署），这比静默跳过生产线守卫更安全，
# 因为其它 8 道门禁会先捕获数据损坏。故此处不做吞错降级。
import oos_breadth as _ob


def compute():
    """返回生产啮合式 OOS Brier（= brier5/cnt，生产现状 5只宽基口径，与 build_data._breadth_idx 一致），无样本返回 None。"""
    if _ob is None:
        return None
    brier2, _brier5, cnt, _per2, _per5 = _ob.compute_production_oos_brier()
    if cnt == 0:
        return None
    # 生产 _breadth 用 5 只宽基口径（build_data._breadth_idx=沪深300/创业板指/上证50/中证500/科创50，
    # 按 _breadth_total=5 归一），故生产啮合式 OOS Brier 须用 brier5；brier2 是 R166 对照设计(非生产口径)，
    # 用 b2 会让守卫对 breadth 轴改动假绿（R85 纪律违规）。
    return _brier5 / cnt


if __name__ == "__main__":
    v = compute()
    print("production_oos_brier (生产啮合式 OOS Brier) = %s" % ("%.4f" % v if v is not None else "None(无样本)"))
