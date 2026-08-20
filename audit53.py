# -*- coding: utf-8 -*-
"""audit53 — 首达概率三副本同步守门员（数值一致性校验）。

目的：
- 生产链路里"首达概率（反射原理）"公式存在 **三份独立副本**：
    ① build_data._drift_prior_prob  （嵌套在 main()，R217 抽出的裸首达单一真源）
    ② calibrate.first_passage_prob   （模块级，可导入的校准真源候选）
    ③ oos_breadth.enrich 内联首达段  （弱守卫/生产旁路，R217 后逐字复刻 build_data）
- 三份当前逐字节一致，但**未来任何人改了其中一份却忘了改其他份**，弱守卫会"假绿"
  （自洽地算错、门禁仍 PASS），导致线上概率静默失真。本守门员用 ast 抽取
  ① 与 ③ 的真实源码、以桩变量执行，与可导入的 ② 及一份"完全独立重写的参考实现"
  在匹配入参下逐点数值比对，任意副本漂移即 sys.exit(1)，使 daily.yml 门禁链硬阻断
  数据推送（安全失败，不假绿）。

设计要点（信任但不轻信 / 不重复维护公式副本）：
- 不手抄公式：① 与 ③ 用 ast.get_source_segment 抽取生产文件里"真实在跑"的源码，
  桩执行；② 直接 import calibrate.first_passage_prob（生产在跑的同一函数）。
- 独立参考实现 ref_fp 仅作"三方副本万一一起写错"的最后兜底锚点（覆盖共享笔误类 bug）。
- 输入契约对齐：三份都以 (base=last_close, price, exp) 驱动；calibrate 用相同的
  _frac/_mu_eff/_sv 推导把 (base,price,exp) 映射成 (a,mu,sigma,T) 后调用。
- 仅在"非边界早返回"区间比对（base<_barrier<price 上行 / price<_barrier<base 下行），
  让三份都落进首达公式分支；同时覆盖 _frac 是否触发 0.235 钳制两种情形。
"""
import ast
import math
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_fn_source(src, name):
    """从源码里抽取名为 name 的 FunctionDef 原文（含装饰器/签名/体）。"""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            seg = ast.get_source_segment(src, node)
            if seg is None:
                raise RuntimeError("无法抽取 %s 源码" % name)
            return seg
    raise RuntimeError("源码中未找到函数 %s" % name)


def _make_shared_stubs(sigma, mu, vol_scale, drift_conf):
    """构造三份副本共用的桩 regime 环境（恒定 vol/drift，便于隔离公式本身）。

    返回 dict，含 math / _vol_for / _drift_for / _vol_scale / _drift_conf / _norm_cdf /
    _frac_for / last_close。保证 oos_breadth 的 _frac_for == build_data 的 _frac 推导。
    """
    # 真实局部变量绑定，使嵌套桩函数闭包能正确捕获（避免 NameError）
    _vol_scale = float(vol_scale)
    _drift_conf = float(drift_conf)

    def _vol_for(exp):
        return float(sigma)

    def _drift_for(exp):
        return float(mu)

    def _norm_cdf(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def _frac_for(exp):
        _e = max(10, int(round(exp)))
        return min(_vol_for(_e) * math.sqrt(_e) * _vol_scale, 0.235)

    ns = {}
    ns["math"] = math
    ns["_vol_for"] = _vol_for
    ns["_drift_for"] = _drift_for
    ns["_vol_scale"] = _vol_scale
    ns["_drift_conf"] = _drift_conf
    ns["_norm_cdf"] = _norm_cdf
    ns["_frac_for"] = _frac_for
    ns["last_close"] = None  # 由调用方按点注入
    return ns


def _build_runners(sigma, mu, vol_scale, drift_conf):
    """按给定 regime 构造两个生产副本的执行器，返回 (run_bd, run_oos)。

    calibrate 直接 import，不需在此构造。每个 regime 重建，确保桩变量与 _run_cal 同口径。
    """
    bd_src = _read(os.path.join(_HERE, "build_data.py"))
    ob_src = _read(os.path.join(_HERE, "oos_breadth.py"))

    # ① build_data._drift_prior_prob（整函数抽取，桩执行）
    bd_fn = _extract_fn_source(bd_src, "_drift_prior_prob")

    # ③ oos_breadth.enrich 内联首达段：抽取 enrich 全文，截到 _recal_g 之前，
    #    尾部补 return _hit，桩执行（避免拉入 _recal_g/bt_lookup/breadth 等重依赖）。
    enrich_full = _extract_fn_source(ob_src, "enrich")
    if "_hit = _recal_g(_hit)" not in enrich_full:
        raise RuntimeError("oos_breadth.enrich 结构变更：未找到 _recal_g 截断点，请人工复核")
    enrich_raw = enrich_full.split("_hit = _recal_g(_hit)", 1)[0].rstrip() + "\n    return _hit\n"

    # 共享桩环境（含 _vol_for/_drift_for/_norm_cdf/_frac_for/_vol_scale/_drift_conf/last_close）
    stub = _make_shared_stubs(sigma, mu, vol_scale, drift_conf)

    # 执行 build_data 函数
    bd_ns = dict(stub)
    exec(compile(bd_fn, "<build_data._drift_prior_prob>", "exec"), bd_ns)
    run_bd = bd_ns["_drift_prior_prob"]

    # 执行 oos_breadth 截断版 enrich
    ob_ns = dict(stub)
    exec(compile(enrich_raw, "<oos_breadth.enrich[raw]>", "exec"), ob_ns)
    enrich_raw_fn = ob_ns["enrich"]

    def run_oos(last_close, price, exp):
        ob_ns["last_close"] = last_close
        return enrich_raw_fn(None, None, price, exp, None)

    return run_bd, run_oos


def _ref_fp(a, mu, sigma, T):
    """完全独立重写的反射原理首达概率参考实现（兜底锚点，捕捉三副本共享笔误）。

    与生产一致地钳制 exo∈[-50,50]，避免极端入参下与生产产生伪差异。
    """
    if T <= 0:
        return 0.5
    sv = sigma * math.sqrt(T)
    if sv <= 0:
        return 0.5
    exo = max(-50.0, min(50.0, 2.0 * mu * a / (sigma ** 2 + 1e-12)))
    if a >= 0:
        d1 = (a - mu * T) / sv
        d2 = (-a - mu * T) / sv
        return 1.0 - 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0))) + math.exp(exo) * 0.5 * (1.0 + math.erf(d2 / math.sqrt(2.0)))
    else:
        b = -a
        d1 = (b + mu * T) / sv
        d2 = (mu * T - b) / sv
        return 1.0 - 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0))) + math.exp(exo) * 0.5 * (1.0 + math.erf(d2 / math.sqrt(2.0)))


def _run_cal(base, price, exp, sigma, mu, vol_scale, drift_conf):
    """用与 build_data/oos_breadth 同一套参数推导，把 (base,price,exp) 映射成
    (a,mu_eff,sigma,T) 后调用可导入真源 calibrate.first_passage_prob。"""
    from calibrate import first_passage_prob
    _exp = max(10, int(round(exp)))
    _frac = min(sigma * math.sqrt(_exp) * vol_scale, 0.235)
    _mu_eff = mu * drift_conf
    _sv = sigma * math.sqrt(_exp) if sigma > 0 else 1e-9
    _dir = 1 if price >= base else -1
    if _dir > 0:
        _barrier = price * (1.0 - _frac)
        if base >= _barrier:
            return 1.0
        _a = math.log(_barrier / base)
    else:
        _barrier = price * (1.0 + _frac)
        if base <= _barrier:
            return 1.0
        _a = math.log(_barrier / base)
    return first_passage_prob(_a, _mu_eff, sigma, _exp)


def _verify_run_calibration_guard():
    """运行时校验 calibrate.run_calibration 的跨锚点守卫与 build_data 一致（防第四副本漂移）。

    run_calibration 是生产校准诊断，其首达概率调用内含独立守卫副本（calibrate.py L141-152），
    未被 audit53 既有三副本比对覆盖——既有比对只测"非边界"区间且跳过早返回。
    若将来 build_data._drift_prior_prob 的守卫被改而 run_calibration 忘了同步，诊断会静默
    走错反射分支（正是 6893527 修复的 44% 样本 bug），audit53 仍 PASS → 假绿。

    做法（信任但不轻信 / 实跑）：抽取 run_calibration 真实源码，把两处 first_passage_prob
    调用改写为带方向标签的 _rec_fp（断言 a 的符号与方向上/下行自洽），在合成 df 上实跑，
    直接捕获"上行目标却传入 a<=0 / 下行目标却传入 a>=0"的走错分支迹象。
    注：run_calibration 用锚点 i 自身 vol regime（walk-forward），不能改为调用
    build_data._drift_prior_prob（后者用末日 regime），故守卫须作为独立副本被显式校验。
    """
    import numpy as np
    import pandas as pd
    from calibrate import first_passage_prob
    import calibrate as _cal

    cal_src = _read(os.path.join(_HERE, "calibrate.py"))
    rc_src = _extract_fn_source(cal_src, "run_calibration")

    # 改写两处调用，注入方向标签（上行=up / 下行=dn）
    up_pat = re.compile(
        r"first_passage_prob\(\s*math\.log\(_bar_up / base\)\s*,([^)]*)\)")
    dn_pat = re.compile(
        r"first_passage_prob\(\s*math\.log\(_bar_dn / base\)\s*,([^)]*)\)")
    rc2 = up_pat.sub(r'_rec_fp(math.log(_bar_up / base),\1, "up")', rc_src)
    rc2 = dn_pat.sub(r'_rec_fp(math.log(_bar_dn / base),\1, "dn")', rc2)
    if rc2.count("_rec_fp(") != 2:
        raise RuntimeError(
            "run_calibration 守卫调用改写失败，请人工复核(regex 未命中2处)")

    def _rec_fp(a, mu, sigma, T, direction):
        # 守卫正确时：上行目标仅当 _bar_up>base(即 a>0)才进首达公式；下行仅当 a<0。
        # 若守卫缺失/写反，会上行传入 a<=0 或下行传入 a>=0 → 走错反射分支，此处捕获。
        if direction == "up" and a <= 0:
            raise AssertionError(
                "run_calibration 上行目标传入 a<=0（守卫缺失/写反，将走错下行分支）")
        if direction == "dn" and a >= 0:
            raise AssertionError(
                "run_calibration 下行目标传入 a>=0（守卫缺失/写反，将走错上行分支）")
        return first_passage_prob(a, mu, sigma, T)

    # 合成 df：足够行数触发 walk-forward 锚点 + 随机波动使部分样本跨越锚点(覆盖边界情形)
    np.random.seed(12345)
    n = 400
    rets = np.random.normal(0.0003, 0.015, n)
    close = 3000.0 * np.cumprod(1.0 + rets)
    high = close * (1.0 + np.abs(np.random.normal(0, 0.008, n)))
    low = close * (1.0 - np.abs(np.random.normal(0, 0.008, n)))
    df = pd.DataFrame({"close": close, "high": high, "low": low})

    ns = {"np": np, "pd": pd, "math": math,
          "first_passage_prob": first_passage_prob, "_rec_fp": _rec_fp}
    for name in dir(_cal):
        if name == "_WINDOWS" or name == "_interp":
            ns[name] = getattr(_cal, name)
    exec(compile(rc2, "<run_calibration[guarded]>", "exec"), ns)
    rc_fn = ns["run_calibration"]
    rc_fn(df, vol_conf=1.0)
    print("[audit53] run_calibration 跨锚点守卫与 build_data 一致（合成df实跑，方向自洽）")


def main():
    # run_bd / run_oos 在每个 regime 内按桩重建（与 _run_cal 同口径），见下方循环。

    # 测试网格：base / price(上行+下行) / exp / regime(sigma,mu,vol_scale,drift_conf)
    bases = [3000.0, 3500.0]
    # 上行目标：price 明显高于 base（落进首达分支）
    up_prices = [3200.0, 3400.0, 3800.0]
    # 下行目标：price 明显低于 base（落进首达分支）
    dn_prices = [2800.0, 2600.0, 2300.0]
    exps = [20, 60, 120, 250]
    # regime 组合：覆盖 vol_scale 高/中/低、_frac 是否触发 0.235 钳制
    regimes = [
        (0.012, 0.0004, 1.0, 0.85),    # 中波动，frac 不钳制
        (0.012, 0.0004, 1.15, 0.60),   # 高波动，frac 不钳制
        (0.012, 0.0004, 0.88, 1.0),    # 低波动，frac 不钳制
        (0.020, 0.0006, 1.15, 0.60),   # 高波动+大 sigma，exp=250 时 frac 触发 0.235 钳制
    ]

    TOL_PROD = 1e-11   # 三份生产副本应逐位一致
    TOL_REF = 1e-9     # 独立参考实现容差

    checked = 0
    failures = []
    for sigma, mu, vs, dc in regimes:
        run_bd, run_oos = _build_runners(sigma, mu, vs, dc)
        for base in bases:
            for exp in exps:
                for price in up_prices + dn_prices:
                    # 仅比对"非边界早返回"区间：上行 base<price，下行 price<base
                    if price >= base:
                        # 上行点：要求 base < _barrier < price（不早返回）
                        _frac = min(sigma * math.sqrt(max(10, round(exp))) * vs, 0.235)
                        _barrier = price * (1.0 - _frac)
                        if not (base < _barrier < price):
                            continue
                    else:
                        _frac = min(sigma * math.sqrt(max(10, round(exp))) * vs, 0.235)
                        _barrier = price * (1.0 + _frac)
                        if not (price < _barrier < base):
                            continue

                    p_bd, _ = run_bd(base, price, exp)
                    p_oos = run_oos(base, price, exp)
                    p_cal = _run_cal(base, price, exp, sigma, mu, vs, dc)

                    # 三份生产副本逐位一致
                    if abs(p_bd - p_oos) > TOL_PROD:
                        failures.append(("bd!=oos", base, price, exp, sigma, vs, p_bd, p_oos))
                    if abs(p_bd - p_cal) > TOL_PROD:
                        failures.append(("bd!=cal", base, price, exp, sigma, vs, p_bd, p_cal))
                    if abs(p_oos - p_cal) > TOL_PROD:
                        failures.append(("oos!=cal", base, price, exp, sigma, vs, p_oos, p_cal))

                    # 独立参考实现兜底（exo 在合理区间才比对，避免极端钳制伪差异）
                    _a = math.log(_barrier / base)
                    _mu_eff = mu * dc
                    _exo = 2.0 * _mu_eff * _a / (sigma ** 2 + 1e-12)
                    if abs(_exo) <= 40.0:
                        p_ref = _ref_fp(_a, _mu_eff, sigma, max(10, int(round(exp))))
                        if abs(p_bd - p_ref) > TOL_REF:
                            failures.append(("bd!=ref", base, price, exp, sigma, vs, p_bd, p_ref))
                    checked += 1

    print("[audit53] 首达概率三副本同步校验：比对点数 = %d" % checked)
    if failures:
        print("[audit53] FAIL：发现 %d 处副本漂移" % len(failures))
        for f in failures[:20]:
            print("   ", f)
        sys.exit(1)
    print("[audit53] PASS：build_data._drift_prior_prob ≡ calibrate.first_passage_prob ≡ "
          "oos_breadth.enrich 内联首达段（三副本数值一致，且与独立参考实现吻合）")

    # 第四副本校验：calibrate.run_calibration 调用内守卫（诊断旁路，未被上面三副本覆盖）
    _verify_run_calibration_guard()

    sys.exit(0)


if __name__ == "__main__":
    main()
