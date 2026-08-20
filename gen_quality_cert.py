# -*- coding: utf-8 -*-
"""预测质量自检证书生成器（R87 借鉴缠论 gen_quality_cert.py，做 A股斐波那契适配版）。

复用既有「单一真源」，绝不重算概率（反假绿、防公式漂移）：
  - OOS Brier（双守卫真源，与生产 OOS 闸门 oos_guard 完全一致）：
      ① bucket_oos_brier      ← R217_segcal_check.run_oos()["bucket_oos_brier"]
                                  （裸首达公式 + 训练集分桶经验校准，基线 0.1441）
      ② production_oos_brier  ← oos_breadth.compute_production_oos_brier() 的 brier5/cnt
                                  （生产啮合式，逐字复刻 build_data._enrich 的 empirical 融合 /
                                   _FUSE_K / _hist_calib / _breadth 共振，基线 0.2276）
  - 回测实证命中率            ← data/backtest.json（build_data 已通过 backtest.run_backtest 写入）
  - 引擎/校准 regime         ← data.js 的 volRegime / resonance / probCalib（只读）

与缠论差异（不照搬其 T+8/T+30 方向预测，本仓无此口径）：
  - 本仓的预测准确性锚点是「首达概率 OOS Brier 退化」+「斐波那契点位回测实证命中率」+
    「波动/漂移 regime 透明度」，三者构成诚实的「预测质量」自画像。
  - 退出码恒 0（透明化、不阻断部署）；结果写 data/quality_cert.json，供 index.html 渲染
    「📊 预测质量自检证书」常驻区块，与缠论 quality_cert.json 等价定位。

设计纪律（延续 R85 反假绿）：
  - 任何计算异常都降级为「在场但不失真」的证书（标 error 字段 + accuracy_status=REVIEW），
    绝不抛 SystemExit 让 CI 步骤变红而阻断当日数据推送（软门禁哲学，对标缠论）。
  - 仅当文件/依赖彻底缺失时写最小证书并仍 exit 0，保证每日看板始终有证书区块可读。
"""
import os
import sys
import json
import re
import math
import datetime

REPO = os.path.dirname(os.path.abspath(__file__))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

BASELINE_PATH = os.path.join(REPO, "selfcheck", "oos_baseline.json")
CERT_PATH = os.path.join(REPO, "data", "quality_cert.json")
DATA_JS = os.path.join(REPO, "data", "data.js")
BACKTEST_JSON = os.path.join(REPO, "data", "backtest.json")

TOL_REL = 0.05  # 与生产 OOS 闸门一致的相对容差 +5%

_cert = {
    "schema": "a-share-fib-quality-cert/v1",
    "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "error": None,
}


def _load_data():
    with open(DATA_JS, encoding="utf-8") as f:
        src = f.read()
    return json.loads(re.search(r"window\.FIB_DATA\s*=\s*(\{.*\})\s*;?\s*$", src, re.S).group(1))


def _main():
    global _cert
    D = _load_data()
    last_close = float(D["lastClose"])
    fetched = D.get("fetchedAt") or D.get("updated")
    drift_conf = float(D["volRegime"]["driftConf"])
    band_scale = float(D["volRegime"]["bandScale"])
    breadth = float(D.get("resonance", {}).get("breadth", 0.0))
    calib = D.get("probCalib") or {}
    n_calib = len(calib.get("vals") or [])

    # ---------- 双守卫 OOS Brier（与生产 oos_guard 同源）----------
    import R217_segcal_check as r217
    import oos_breadth as ob
    o = r217.run_oos()
    bucket = float(o["bucket_oos_brier"])
    b2, b5, cnt, _per2, _per5 = ob.compute_production_oos_brier()
    prod_brier5 = (b5 / cnt) if cnt else None
    vol_bucket = o.get("vol_bucket")

    base = {}
    if os.path.exists(BASELINE_PATH):
        with open(BASELINE_PATH, encoding="utf-8") as f:
            base = json.load(f)
    base_bucket = base.get("bucket_oos_brier")
    base_prod = base.get("production_oos_brier")

    def _delta(cur, b):
        if b is None or cur is None:
            return None
        return round((cur / b - 1.0) * 100.0, 2)

    prod_delta = _delta(prod_brier5, base_prod)
    bucket_delta = _delta(bucket, base_bucket)
    worst_delta = None
    for d in (prod_delta, bucket_delta):
        if d is not None:
            worst_delta = d if worst_delta is None else max(worst_delta, d)

    if worst_delta is None:
        calib_status = "baseline_missing"
    elif worst_delta > TOL_REL * 100:
        calib_status = "regressed"
    elif worst_delta < -TOL_REL * 100:
        calib_status = "improved"
    else:
        calib_status = "ok"

    # ---------- 回测实证命中率 ----------
    bt_summary = []
    total_eval = 0
    logged = 0
    cold = True
    overall = None
    if os.path.exists(BACKTEST_JSON):
        with open(BACKTEST_JSON, encoding="utf-8") as f:
            bt = json.load(f)
        bt_summary = bt.get("summary", [])
        total_eval = bt.get("totalEvaluated", 0)
        logged = bt.get("totalLogged", 0)
        cold = bt.get("coldStart", True)
        ev = [s for s in bt_summary if s.get("hitRate") is not None and s.get("n")]
        if ev:
            n_sum = sum(s["n"] for s in ev)
            h_sum = sum(s["hitRate"] / 100.0 * s["n"] for s in ev)
            overall = round(h_sum / n_sum * 100.0, 1) if n_sum else None

    # ---------- 综合 accuracy_status（capped，避免极端值误导）----------
    if cold or total_eval < 3:
        accuracy_status = "COLD"   # 样本不足，不评级（但引擎照常运行）
    elif calib_status == "regressed":
        accuracy_status = "REVIEW"  # OOS 退化超容差，需人工复核（软门禁已告警）
    elif overall is not None and (overall < 35.0 or overall > 85.0):
        accuracy_status = "WATCH"   # 命中率异常（过悲观/过乐观），提示关注
    else:
        accuracy_status = "OK"

    targets = [
        {"cat": s["cat"], "key": s["key"], "n": s["n"],
         "hitRate": s["hitRate"], "avgDays": s.get("avgDays")}
        for s in bt_summary
    ]

    # ---------- 情绪透明化标注（R87 ⑥，monitor_only，不参与建模）----------
    # 情绪温度由 gen_sentiment.py 独立生成 data/sentiment.json；此处仅做「透明标注」——
    # 让人一眼看到情绪是 monitor_only、未参与任何概率/方向计算（对齐 docs 第四节第4条）。
    SENTI_PATH = os.path.join(REPO, "data", "sentiment.json")
    senti = {"mode": "monitor_only", "source": "data/sentiment.json", "score": None, "label": None}
    if os.path.exists(SENTI_PATH):
        try:
            with open(SENTI_PATH, encoding="utf-8") as f:
                _s = json.load(f)
            senti["score"] = _s.get("score")
            senti["label"] = _s.get("label")
            senti["asOf"] = _s.get("asOf")
        except Exception:
            pass

    _cert = {
        "schema": "a-share-fib-quality-cert/v1",
        "generated_at": _cert["generated_at"],
        "data_last_date": fetched,
        "engine": {
            "driftConf": drift_conf,
            "bandScale": band_scale,
            "resonance_breadth": breadth,
            "calib_points": n_calib,
            "last_close": last_close,
            "vol_bucket": vol_bucket,
        },
        "oos_brier": {
            "production_brier5": round(prod_brier5, 5) if prod_brier5 is not None else None,
            "production_baseline": base_prod,
            "production_delta_pct": prod_delta,
            "bucket_brier": round(bucket, 5),
            "bucket_baseline": base_bucket,
            "bucket_delta_pct": bucket_delta,
            "sample_cnt": cnt,
            "status": calib_status,
            "tolerance_pct": TOL_REL * 100,
        },
        "backtest": {
            "total_logged": logged,
            "total_evaluated": total_eval,
            "cold_start": cold,
            "overall_hit_rate": overall,
            "targets": targets,
        },
        "accuracy_status": accuracy_status,
        "sentiment": senti,
        "error": None,
    }


def main():
    try:
        _main()
    except Exception as e:  # 降级：在场但不失真，绝不阻断当日推送
        _cert["error"] = "gen_quality_cert 异常: %s" % e
        _cert["accuracy_status"] = "REVIEW"
        # 尽量保留已能拿到的引擎信息
        try:
            D = _load_data()
            _cert["data_last_date"] = D.get("fetchedAt") or D.get("updated")
            _cert.setdefault("engine", {})["last_close"] = float(D.get("lastClose", 0.0))
        except Exception:
            pass
    finally:
        os.makedirs(os.path.dirname(CERT_PATH), exist_ok=True)
        with open(CERT_PATH, "w", encoding="utf-8") as f:
            json.dump(_cert, f, ensure_ascii=False, indent=2)
        # 同步写一份 JS 全局版本（window.QUALITY_CERT），供 index.html 用 <script> 标签加载
        # —— 与 data.js 同机制，Pages(https) 与本地 file:// 打开均可读，无需 fetch(CORS 受限)。
        with open(CERT_PATH.replace(".json", ".js"), "w", encoding="utf-8") as f:
            f.write("window.QUALITY_CERT = ")
            json.dump(_cert, f, ensure_ascii=False)
            f.write(";")

    c = _cert
    print("=== 预测质量自检证书 (R87) ===")
    print("data_last_date    = %s" % c.get("data_last_date"))
    obr = c["oos_brier"]
    print("OOS 生产Brier5     = %s (基线 %s, Δ=%s%%) [%s]"
          % (obr.get("production_brier5"), obr.get("production_baseline"),
             obr.get("production_delta_pct"), obr.get("status")))
    print("OOS 裸公式Bucket   = %s (基线 %s, Δ=%s%%)"
          % (obr.get("bucket_brier"), obr.get("bucket_baseline"), obr.get("bucket_delta_pct")))
    bt = c["backtest"]
    print("回测实证命中率(整体, Laplace收缩) = %s%%, 已评估 %d / 存档 %d, 冷启动=%s"
          % (bt.get("overall_hit_rate"), bt.get("total_evaluated"), bt.get("total_logged"),
             bt.get("cold_start")))
    print("calibration_status = %s ; accuracy_status = %s" % (obr.get("status"), c.get("accuracy_status")))
    if c.get("error"):
        print("⚠️ %s" % c["error"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
