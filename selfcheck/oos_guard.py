# -*- coding: utf-8 -*-
"""OOS Brier 防退化闸门（R85 纪律自动化，落实项 ③）。

为什么需要：
  概率引擎(build_data._fit_prior_recal / calibrate)的任何改动，若不慎恶化首达概率校准，
  会悄悄拉低预测准确性。R85 要求"引擎层准确度改动须先 OOS 复验确降 Brier 才部署"，
  本脚本把该纪律固化进 daily.yml——每次云端重建都会自动跑，回归超容差即 EXIT=1 阻断推送。

做什么：
  复用 R217_segcal_check.run_oos()（与生产分桶校准逐字一致的 walk-forward OOS），
  取 bucket_oos_brier（= 生产部署方法），与 selfcheck/oos_baseline.json 比对。
  若 当前 > 基线 × (1 + TOL_REL) → 阻断；否则通过。

注：OOS Brier 只读 data/sh000001.csv（git 跟踪的历史价，每日仅追加），故跨日稳定，
  闸门是确定性的"引擎改动守门员"，不受每日行情噪声影响。
"""
import os
import sys
import json

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import R217_segcal_check as r217  # noqa: E402

TOL_REL = 0.05  # 相对容差 +5%（Brier 退化超此即判回归）
BASELINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oos_baseline.json")


def main():
    o = r217.run_oos()
    brier = o["bucket_oos_brier"]  # 生产部署方法 = 分桶经验校准(已含 PAVA 单调后处理)

    if not os.path.exists(BASELINE_PATH):
        # 首次运行无基线：写出当前值作为基线，绝不阻断（避免误杀首次接入）
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "bucket_oos_brier": round(brier, 4),
                "note": "auto-baseline from first run; 后续引擎改动若使该值退化>%.0f%% 将被阻断" % (TOL_REL * 100),
                "asOf": o.get("vol_bucket"),
            }, f, indent=2, ensure_ascii=False)
        print("ℹ️ 未检测到基线，已自动写入基线 bucket_oos_brier=%.4f（不阻断本次）" % brier)
        return 0

    with open(BASELINE_PATH, encoding="utf-8") as f:
        base = json.load(f)["bucket_oos_brier"]
    ceil = base * (1.0 + TOL_REL)
    rel = (brier / base - 1.0) * 100.0
    print("OOS 闸门：当前分桶校准验证 Brier=%.4f，基线=%.4f，上限=%.4f (容差 +%.0f%%)"
          % (brier, base, ceil, TOL_REL * 100))
    if brier <= ceil + 1e-9:
        print("✅ OOS Brier 未退化（Δ=%.2f%%），通过。" % rel)
        return 0
    print("❌ OOS Brier 退化 %.2f%% 超容差 → 阻断部署。请排查概率引擎改动（R85：须先 OOS 复验确降 Brier）。" % rel)
    return 1


if __name__ == "__main__":
    sys.exit(main())
