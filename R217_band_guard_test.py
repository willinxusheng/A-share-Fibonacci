# -*- coding: utf-8 -*-
"""R217 band 跨锚点守卫反假绿回归测试。

背景：R217.build_samples 此前漏掉 calibrate.run_calibration / build_data._drift_prior_prob /
oos_breadth.enrich 都有的「band 边跨过锚点 → 首达概率恒=1.0」守卫，导致 a_up<0 被 first_passage_prob
误投进下行分支、a_dn>0 误投进上行分支，~45% 样本 p/y 语义错配、OOS Brier 虚高（R103 修复）。

本测试构造高波动合成序列，强制小 r 目标的 band 边跨过锚点，断言这些样本的首达概率 == 1.0
（守卫生效）；并断言至少有一个 band 跨锚点样本被触发（证明测试确实构造到了该场景，反假绿：
若守卫被删，这些样本会回到 <1 的走错分支概率，断言失败）。

用法：python R217_band_guard_test.py  （exit 0 = 通过；非零 = 失败）
"""
import math
import numpy as np
import pandas as pd

import R217_segcal_check as r

_W = [20, 60, 120, 250]
_R_GRID = [0.03, 0.05, 0.08, 0.12, 0.15, 0.20]
_H_GRID = [20, 40, 60, 90, 120, 180, 250]


def _make_high_vol_df(n=520, seed=0):
    """高波动随机游走，使 _frac 触达 0.235 封顶 → (1+r)*(1-_frac)<1 对 r=0.03~0.20 成立。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n)
    shocks = rng.normal(0, 0.04, n)  # 4% 日波动，远超真实，确保 _frac 封顶
    close = 3000.0 * np.exp(np.cumsum(shocks))
    high = close * (1.0 + np.abs(rng.normal(0, 0.02, n)))
    low = close * (1.0 - np.abs(rng.normal(0, 0.02, n)))
    return pd.DataFrame({"close": close, "high": high, "low": low}, index=dates)


def test_band_cross_guard():
    df = _make_high_vol_df()
    n = len(df)
    samples = r.build_samples(df, 1.0)  # vol_conf=1.0 走"低波动桶"路径，不影响 _frac 封顶

    close_v = df["close"].values
    ret = np.log(df["close"] / df["close"].shift(1))
    hv20 = ret.rolling(20).std() * np.sqrt(244) * 100

    def _vscale(idx):
        if idx < 0 or idx >= len(hv20) or pd.isna(hv20.iloc[idx]):
            return 1.0
        pct = float((hv20.iloc[:idx + 1].dropna() < hv20.iloc[idx]).mean()) * 100
        return 1.15 if pct >= 66 else (1.0 if pct >= 33 else 0.88)

    vol_by_w = {w: ret.rolling(w).std().values for w in _W}

    si = 0          # 游标，对齐 build_samples 的 samples 追加顺序
    checked = 0
    for i in range(20, n - 250 - 1):
        base = close_v[i]
        for H in _H_GRID:
            if i + H >= n:
                continue
            w = min(_W[-1], max(_W[0], H))
            sig = r._interp({ww: vol_by_w[ww][i] for ww in _W}, w)
            if sig is None or math.isnan(sig) or sig <= 0:
                continue  # build_samples 此时不追加任何样本
            _exp = max(10, int(round(H)))
            _frac = min(sig * math.sqrt(_exp) * _vscale(i), 0.235)
            for rr in _R_GRID:
                # —— 上行样本（samples 中偶数偏移）——
                _bar_up = base * (1.0 + rr) * (1.0 - _frac)
                if base >= _bar_up:
                    assert samples[si][1] == 1.0, (
                        f"band 跨锚点上行样本 p={samples[si][1]} != 1.0 "
                        f"(base={base:.2f}, bar_up={_bar_up:.2f})")
                    checked += 1
                si += 1
                # —— 下行样本（samples 中奇数偏移）——
                _bar_dn = base * (1.0 - rr) * (1.0 + _frac)
                if base <= _bar_dn:
                    assert samples[si][1] == 1.0, (
                        f"band 跨锚点下行样本 p={samples[si][1]} != 1.0 "
                        f"(base={base:.2f}, bar_dn={_bar_dn:.2f})")
                    checked += 1
                si += 1

    assert checked > 0, "测试无效：高波动序列未触发任何 band 跨锚点样本（守卫未被真正检验）"
    print(f"[R217 band-guard] 校验 {checked} 个 band 跨锚点样本，全部 p==1.0 ✔")


if __name__ == "__main__":
    test_band_cross_guard()
