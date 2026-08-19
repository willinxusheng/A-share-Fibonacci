# -*- coding: utf-8 -*-
"""OOS Brier 防退化闸门（R85 纪律自动化，落实项 ③）—— 双守卫。

为什么需要：
  概率引擎(build_data._fit_prior_recal / calibrate)的任何改动，若不慎恶化首达概率校准，
  会悄悄拉低预测准确性。R85 要求"引擎层准确度改动须先 OOS 复验确降 Brier 才部署"，
  本脚本把该纪律固化进 daily.yml——每次云端重建都会自动跑，回归超容差即 EXIT=1 阻断推送。

双守卫（2026-08-18 补漏，修复原假绿）：
  ① 裸公式守卫 bucket_oos_brier：来自 R217_segcal_check.run_oos()（裸首达公式 + 训练集分桶校准）。
     稳健但仅守护裸首达公式，对生产 empirical 融合/_FUSE_K/_hist_calib/共振 无感。
  ② 生产线守卫 production_oos_brier：来自 selfcheck/production_oos（逐字复刻 build_data._enrich，
     含 empirical 融合/_FUSE_K/_hist_calib/_breadth 共振）。真正捕捉生产概率引擎的校准退化——
     已定量证实改 _FUSE_K 会让它变化 >5%，而 bucket_oos_brier 不变。两个守卫任一退化超容差即阻断。

注：两守卫只读 git 跟踪的 data/sh000001.csv + data/data.js（每日仅追加），故跨日稳定，
  闸门是确定性的"引擎改动守门员"，不受每日行情噪声影响。
"""
import os
import sys
import json

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import R217_segcal_check as r217  # noqa: E402
import production_oos as prod_oos  # noqa: E402

TOL_REL = 0.05  # 相对容差 +5%（Brier 退化超此即判回归）
BASELINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oos_baseline.json")


def _guard(name, cur, base):
    """返回 (passed(bool), msg(str))。base 为 None 表示首次无基线，写当前值不阻断。"""
    if base is None:
        return True, "ℹ️ %s 无基线，已自动写入当前值=%.4f（不阻断本次）" % (name, cur)
    ceil = base * (1.0 + TOL_REL)
    rel = (cur / base - 1.0) * 100.0
    if cur <= ceil + 1e-9:
        return True, "✅ %s 未退化（Δ=%.2f%%），通过。" % (name, rel)
    return False, "❌ %s 退化 %.2f%% 超容差 → 阻断部署。请排查概率引擎改动（R85：须先 OOS 复验确降 Brier）。" % (name, rel)


def main():
    # ---------- 守卫 ① 裸公式 ----------
    o = r217.run_oos()
    bucket = o["bucket_oos_brier"]

    # ---------- 守卫 ② 生产线啮合 ----------
    prod = prod_oos.compute()

    # ---------- 基线读取/写入 ----------
    if not os.path.exists(BASELINE_PATH):
        # 首次运行无基线：写出当前值作为基线，绝不阻断（避免误杀首次接入）
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "bucket_oos_brier": round(bucket, 4),
                "production_oos_brier": (round(prod, 4) if prod is not None else None),
                "note": "auto-baseline from first run; 后续引擎改动若使任一 Brier 退化>%.0f%% 将被阻断" % (TOL_REL * 100),
                "asOf": o.get("vol_bucket"),
            }, f, indent=2, ensure_ascii=False)
        print("ℹ️ 未检测到基线，已自动写入基线 bucket_oos_brier=%.4f / production_oos_brier=%s（不阻断本次）"
              % (bucket, ("%.4f" % prod if prod is not None else "None")))
        return 0

    with open(BASELINE_PATH, encoding="utf-8") as f:
        base = json.load(f)
    base_bucket = base.get("bucket_oos_brier")
    base_prod = base.get("production_oos_brier")

    # 若基线缺 production 字段（旧基线向后兼容），补写当前值不阻断
    if base_prod is None and prod is not None:
        base["production_oos_brier"] = round(prod, 4)
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(base, f, indent=2, ensure_ascii=False)
        print("ℹ️ 旧基线缺 production_oos_brier，已补写=%.4f（不阻断本次）" % prod)

    print("OOS 闸门：")
    print("  ① 裸公式 bucket_oos_brier=%.4f，基线=%s，上限=%.4f (容差 +%.0f%%)"
          % (bucket, ("%.4f" % base_bucket if base_bucket is not None else "无"),
             (base_bucket * (1 + TOL_REL) if base_bucket is not None else float("nan")), TOL_REL * 100))
    print("  ② 生产线 production_oos_brier=%s，基线=%s，上限=%.4f (容差 +%.0f%%)"
          % (("%.4f" % prod if prod is not None else "None"),
             ("%.4f" % base_prod if base_prod is not None else "无"),
             (base_prod * (1 + TOL_REL) if base_prod is not None else float("nan")), TOL_REL * 100))

    # ---------- 判定 ----------
    p1, m1 = _guard("bucket_oos_brier", bucket, base_bucket)
    print(m1)
    # 生产线守卫是双守卫之一，不得因"无样本"静默消失（否则引擎失效→守卫被掏空，
    # 违反反假绿纪律）。prod is None 视为计算失败（引擎改动/数据缺失），必须阻断而非放行。
    if prod is None:
        p2, m2 = (False, "❌ production_oos_brier 无样本(None)：生产线守卫无法评估，按失败阻断（避免引擎失效假绿）")
    else:
        p2, m2 = _guard("production_oos_brier", prod, base_prod)
    print(m2)

    if p1 and p2:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
