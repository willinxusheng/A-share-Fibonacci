# -*- coding: utf-8 -*-
"""R230: 验证 R225->R226 闭环的基石假设——data.js 真实 probSrc 分布。
判断"线上确实 8/9 目标走经验频率档(回测实证)、仅 1 个走漂移模型档"是否成立。
只报告，不改引擎。
"""
import os, re, json, collections, sys

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

def main():
    s = open(os.path.join(DATA, "data.js"), encoding="utf-8").read()
    m = re.search(r'FIB_DATA\s*=\s*(\{.*\})\s*;', s, re.S)
    d = json.loads(m.group(1))

    targets = []
    def walk(o):
        if isinstance(o, dict):
            # 只统计图上的目标点(含 price)；排除 subForecast.rows 表格行(无 price, 供前端表格渲染)
            if "prob" in o and "probSrc" in o and "price" in o:
                targets.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d)

    # 去重（子浪ⅴ≡卖① 同价，会出现两次）
    seen = set()
    rows = []
    for t in targets:
        name = t.get("name") or t.get("label") or t.get("wave") or "?"
        price = float(t.get("price", 0))
        prob = float(t.get("prob", 0))
        src = t.get("probSrc")
        exp = t.get("expDays")
        key = (round(price, 2), src)
        if key in seen:
            continue
        seen.add(key)
        rows.append((name, price, prob, src, exp))

    cnt = collections.Counter(r[3] for r in rows)
    print("=" * 78)
    print("R230  data.js 真实 probSrc 分布核查（验证 R225->R226 闭环基石）")
    print("=" * 78)
    print("去重后目标数 = %d" % len(rows))
    print("probSrc 分布 = %s" % dict(cnt))
    print("-" * 78)
    print("%-22s %10s %8s %12s %8s" % ("name", "price", "prob", "probSrc", "expDays"))
    print("-" * 78)
    for name, price, prob, src, exp in rows:
        print("%-22s %10.2f %8.1f %12s %8s" % (str(name)[:22], price, prob, str(src), str(exp)))

    # 关键判定
    emp = cnt.get("回测实证", 0)
    drf = cnt.get("漂移模型", 0)
    print("-" * 78)
    if emp >= 1 and drf == 1:
        print("[基石假设 OK] 经验频率档= %d 个、漂移模型档= %d 个(浪⑤起) → R225 候选提升对线上零影响结论成立" % (emp, drf))
    elif drf > 1:
        print("[基石假设 FAIL] 漂移模型档有 %d 个(>1) → R225 候选提升可能波及线上, 需重新审视!" % drf)
        sys.exit(1)
    else:
        print("[基石假设 NOTE] 分布 emp=%d drf=%d → 请人工核对" % (emp, drf))
    print("=" * 78)

if __name__ == "__main__":
    main()
