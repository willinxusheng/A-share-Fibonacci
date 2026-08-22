# -*- coding: utf-8 -*-
"""情绪指标有效性验证（R114）：sentiment.history score 与真实行情的关系。

问题：情绪指标到底是「顺势」还是「逆势」信号？是否有信息含量？
方法（trust-but-verify，全部用真实 kline 数据实证）：
  1) score vs 当日涨跌幅 相关性（同期动量）
  2) score vs 未来 N 交易日收益 相关性（预测力，N=1/3/5/10/20）
  3) 分档（冰点/偏冷/中性/偏热/狂热）后的未来平均收益（逆向策略回测）
  4) score 与次日收益的符号一致性（方向命中率）
  5) 与随机基准对比（打乱 score 序列 1000 次，看真实相关性是否显著优于随机）
"""
import json
import random
import re
import statistics

REPO = r"C:\Users\Administrator\WorkBuddy\2026-08-04-23-16-18\A-share-Fibonacci"

def load_js(path, var):
    raw = open(path, encoding="utf-8").read()
    return json.loads(re.sub(r"^\s*window\.%s\s*=\s*" % re.escape(var), "", raw).rstrip().rstrip(";"))

def main():
    data = load_js(REPO + r"\data\data.js", "FIB_DATA")
    sent = json.load(open(REPO + r"\data\sentiment.json", encoding="utf-8"))

    kd = data["kline"]["dates"]
    kc = data["kline"]["close"]
    kset = {d: i for i, d in enumerate(kd)}
    hist = sent["history"]
    hist_dates = [h["date"] for h in hist]

    # 对齐：history 250 点 == kline 末 250 根（R113 已逐点验证）
    base = len(kd) - len(hist)
    idx = [base + i for i in range(len(hist))]
    scores = [h["score"] for h in hist]

    # 当日涨跌幅（前收）
    rets = []
    for i in idx:
        prev = kc[i - 1] if i > 0 else kc[i]
        rets.append((kc[i] / prev - 1.0) * 100.0)

    def corr(a, b):
        n = min(len(a), len(b))
        if n < 5:
            return None
        a, b = a[:n], b[:n]
        ma, mb = sum(a) / n, sum(b) / n
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        va = sum((x - ma) ** 2 for x in a) ** 0.5
        vb = sum((y - mb) ** 2 for y in b) ** 0.5
        if va == 0 or vb == 0:
            return None
        return cov / (va * vb)

    print("样本：history %d 点（%s ~ %s），与 kline 对齐" % (len(hist), hist_dates[0], hist_dates[-1]))
    print("score 范围: %.1f ~ %.1f  均值 %.1f  中位 %.1f" %
          (min(scores), max(scores), statistics.mean(scores), statistics.median(scores)))
    print()

    # 1) 同期动量：score vs 当日涨跌
    c0 = corr(scores, rets)
    print("1) score vs 当日涨跌幅  相关系数 r = %s" % (None if c0 is None else round(c0, 3)))

    # 2) 未来 N 日收益预测力
    print("\n2) score vs 未来 N 交易日收益（正=顺势预测，负=逆向预测）")
    for N in (1, 3, 5, 10, 20):
        fwd = []
        for i in idx:
            j = i + N
            if j < len(kc):
                fwd.append((kc[j] / kc[i] - 1.0) * 100.0)
            else:
                fwd.append(None)
        pairs = [(s, f) for s, f in zip(scores, fwd) if f is not None]
        c = corr([p[0] for p in pairs], [p[1] for p in pairs])
        print("   N=%-3d 样本=%-3d  r = %s" % (N, len(pairs), None if c is None else round(c, 3)))

    # 3) 分档后的未来收益（逆向策略视角：恐慌后买、狂热后卖）
    print("\n3) 分档后的未来 5 日/20 日平均收益（% 表示）")
    bands = [("冰点(<20)", lambda s: s < 20), ("偏冷(20-40)", lambda s: 20 <= s < 40),
             ("中性(40-60)", lambda s: 40 <= s < 60), ("偏热(60-80)", lambda s: 60 <= s < 80),
             ("狂热(>=80)", lambda s: s >= 80)]
    fwd5, fwd20 = [], []
    for i in idx:
        f5, f20 = None, None
        if i + 5 < len(kc):
            f5 = (kc[i + 5] / kc[i] - 1.0) * 100.0
        if i + 20 < len(kc):
            f20 = (kc[i + 20] / kc[i] - 1.0) * 100.0
        fwd5.append(f5)
        fwd20.append(f20)
    for name, pred in bands:
        s5, s20 = [], []
        for s, f5, f20 in zip(scores, fwd5, fwd20):
            if pred(s):
                if f5 is not None:
                    s5.append(f5)
                if f20 is not None:
                    s20.append(f20)
        print("   %-14s 样本=%3d  未来5日=%.2f%%  未来20日=%.2f%%" %
              (name, len(s5), sum(s5) / len(s5) if s5 else 0, sum(s20) / len(s20) if s20 else 0))

    # 4) 方向命中率：score 上穿/下穿 50 后次日方向
    print("\n4) score 穿越 50 后的次日方向命中（上穿=情绪转暖，下穿=转冷）")
    up_hit = up_tot = dn_hit = dn_tot = 0
    for i in range(1, len(idx)):
        if scores[i - 1] <= 50 < scores[i] and i + 1 < len(kc):
            up_tot += 1
            if kc[i + 1] > kc[i]:
                up_hit += 1
        if scores[i - 1] >= 50 > scores[i] and i + 1 < len(kc):
            dn_tot += 1
            if kc[i + 1] < kc[i]:
                dn_hit += 1
    print("   上穿50 次日上涨: %d/%d = %.0f%%" % (up_hit, up_tot, 100 * up_hit / up_tot if up_tot else 0))
    print("   下穿50 次日下跌: %d/%d = %.0f%%" % (dn_hit, dn_tot, 100 * dn_hit / dn_tot if dn_tot else 0))

    # 5) 随机基准：打乱 score 1000 次，比较未来5日相关性的真实显著性
    print("\n5) 显著性检验（未来 5 日，真实 r vs 1000 次随机打乱）")
    pairs5 = [(s, f) for s, f in zip(scores, fwd5) if f is not None]
    real = corr([p[0] for p in pairs5], [p[1] for p in pairs5]) or 0.0
    rnd = []
    random.seed(42)
    for _ in range(1000):
        sh = scores[:]
        random.shuffle(sh)
        c = corr(sh[:len(pairs5)], [p[1] for p in pairs5]) or 0.0
        rnd.append(c)
    rnd.sort()
    pct = sum(1 for c in rnd if abs(c) >= abs(real)) / len(rnd)
    print("   真实 r(5日) = %.3f" % real)
    print("   随机分布: p2.5=%.3f  p50=%.3f  p97.5=%.3f" % (rnd[24], rnd[500], rnd[974]))
    print("   显著性 p = %.3f（<0.05 表示优于随机）" % pct)

    # 6) 今日读数与最新价趋势的当前状态
    print("\n6) 当前状态")
    print("   今日 score = %s（%s），最新收盘 %s" % (sent["today"]["score"], sent["today"]["label"], kc[-1]))
    chg = (kc[-1] / kc[-6] - 1.0) * 100.0 if len(kc) > 5 else 0
    print("   近5日涨跌 = %.2f%%" % chg)

if __name__ == "__main__":
    main()
