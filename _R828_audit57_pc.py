# -*- coding: utf-8 -*-
"""R828 · audit57 缺陷注入正控

纪律（R824b 沉淀）：每个注入用例必须先自证两点，否则"用例写错"会被误判成"断言漏报"——
  ① 产物前后 diff 非空（真的改到了）；
  ② 该变更按定义就是缺陷（能说出违反了哪条不变量）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
DATA_JS = os.path.join(BASE, "data", "data.js")
BT = os.path.join(BASE, "data", "backtest.json")
HTML = os.path.join(BASE, "index.html")


def load_js(p):
    s = open(p, encoding="utf-8").read()
    i, j = s.index("{"), s.rindex("}")
    return s[:i], s[i: j + 1], s[j + 1:]


def run_audit(tmp):
    env = dict(os.environ)
    env["A57_BT"] = tmp["bt"]
    env["A57_HTML"] = tmp["html"]
    p = subprocess.run([PY, os.path.join(BASE, "audit57.py"), tmp["js"]],
                       capture_output=True, text=True, cwd=BASE, env=env)
    fails = [l for l in p.stdout.splitlines() if "[FAIL]" in l]
    return p.returncode, fails, p.stdout


def mutate(paths, fn_bt, fn_js, fn_html):
    tmp = tempfile.mkdtemp(prefix="a57pc_")
    bt = json.load(open(BT, encoding="utf-8"))
    fn_bt(bt)
    p_bt = os.path.join(tmp, "bt.json")
    json.dump(bt, open(p_bt, "w", encoding="utf-8"), ensure_ascii=False)

    head, body, tail = load_js(DATA_JS)
    D = json.loads(body)
    if fn_js:
        fn_js(D)
    p_js = os.path.join(tmp, "d.js")
    open(p_js, "w", encoding="utf-8").write(head + json.dumps(D, ensure_ascii=False) + tail)

    p_html = os.path.join(tmp, "index.html")
    h = open(HTML, encoding="utf-8").read()
    if fn_html:
        h = fn_html(h)
    open(p_html, "w", encoding="utf-8").write(h)
    return {"bt": p_bt, "js": p_js, "html": p_html, "dir": tmp}


CASES = [
    ("PC1 pairedBase +3", 
     lambda b: b.__setitem__("dirPairedBase", round(b["dirPairedBase"] + 3, 1)),
     None, None, ["P2", "P3b", "P4b"]),
    ("PC2 pairedEdge +5（造假优势）",
     lambda b: b.__setitem__("dirPairedEdge", round(b["dirPairedEdge"] + 5, 1)), None, None, ["P3b"]),
    ("PC3 pairedN +1",
     lambda b: b.__setitem__("dirPairedN", b["dirPairedN"] + 1), None, None, ["P1", "P4a"]),
    ("PC4 maturedRateRaw +4（未同步 edge）",
     lambda b: b.__setitem__("dirMaturedRateRaw", round(b["dirMaturedRateRaw"] + 4, 1)), None, None,
     ["P3a", "P3b"]),
    ("PC5 bySide 的 sell 命中数 +3",
     lambda b: [x.__setitem__("hits", x["hits"] + 3) for x in b["dirPairedBySide"] if x["side"] == "sell"],
     None, None, ["P4a", "P5"]),
    ("PC6 bySide 的 buy base 篡改",
     lambda b: [x.__setitem__("base", x["base"] + 4) for x in b["dirPairedBySide"] if x["side"] == "buy"],
     None, None, ["P4b", "P5"]),
    # ⚠ PC7 必须同时改 backtest.json 与 data.js 里的 backtest 块：
    #    P6a 读的是 data.js（前端数据源），只改 backtest.json 是 no-op（用例写错，非断言漏报）。
    ("PC7 windowRule 回退到 legacy",
     lambda b: b.__setitem__("dirWindowRule", "legacy:max(30,expDays)"),
     lambda d: d["backtest"].__setitem__("dirWindowRule", "legacy:max(30,expDays)"),
     None, ["P6a"]),
    # PC8 删除整段披露块（以特征串为锚），而非只删一处字段引用
    ("PC8 前端删掉配对披露块",
     lambda b: None, None,
     lambda h: h.replace("严格配对检验：引擎相对", "已移除："), ["P7a"]),
    ("PC9 前端恢复已被推翻的表述",
     lambda b: None, None, lambda h: h.replace("</body>", "那是巧合，不是结论</body>"), ["P7b"]),
    # ---- R828b 新增：样本窗口与跨市场新鲜度 ----
    ("PC10 样本窗口根数篡改",
     lambda b: b.__setitem__("dataWindowRows", b["dataWindowRows"] - 7), None, None, ["P8"]),
    # PC11 只改 asOf 不同步 lagDays ⇒ 自洽性必须被抓（否则 lag 校验是摆设）
    ("PC11 asOf 改而 lagDays 不跟进",
     lambda b: None,
     lambda d: d["crossMarket"]["details"][0].__setitem__("asOf", "2026-01-05"), None, ["P9a"]),
    # PC12 asOf 与 lagDays 同步改成滞后 672 天，但 staleSeries/breadthAvailable 不更新
    #      ⇒ 这正是 R828b 要防的"陈旧冒充新鲜"形态，P9b/P9c 必须同时报警
    ("PC12 陈旧市场未剔出广度",
     lambda b: None,
     lambda d: (d["crossMarket"]["details"][0].__setitem__("asOf", "2025-01-01"),
                d["crossMarket"]["details"][0].__setitem__("lagDays", 672)),
     None, ["P9b", "P9c"]),
]


def main():
    ok = 0
    total = 0
    print("=" * 78)
    print("R828 · audit57 缺陷注入正控")
    print("=" * 78)

    # 零注入基线：必须全绿，否则后续读数作废
    base = mutate(None, lambda b: None, None, None)
    rc, fails, _ = run_audit(base)
    print("  [基线] 注入为空 → exit=%d FAIL=%s %s"
          % (rc, [f.split("]")[0].replace("[FAIL", "").strip() for f in fails] or "无",
             "OK" if rc == 0 and not fails else "!!! 基线不绿，正控作废 !!!"))
    if rc != 0 or fails:
        return 1
    print()

    for name, fb, fj, fh, expect in CASES:
        total += 1
        tmp = mutate(None, fb, fj, fh)
        # ① diff 非空自证
        d_bt = open(tmp["bt"], encoding="utf-8").read() != open(BT, encoding="utf-8").read()
        d_js = open(tmp["js"], encoding="utf-8").read() != open(DATA_JS, encoding="utf-8").read()
        d_ht = open(tmp["html"], encoding="utf-8").read() != open(HTML, encoding="utf-8").read()
        nonempty = (d_bt or d_js or d_ht)
        rc, fails, out = run_audit(tmp)
        hit = any(any(e in f for f in fails) for e in expect)
        good = (rc == 1) and hit and nonempty
        ok += good
        got = [f.split("]")[1].split("::")[0].strip() if "]" in f else f for f in fails]
        print("  [%s] %-32s 期望 %-14s diff=%-5s exit=%d 捕获=%s"
              % ("OK  " if good else "MISS", name, ",".join(expect), nonempty, rc,
                 [g[:22] for g in got] or "无"))
        shutil.rmtree(tmp["dir"], ignore_errors=True)

    print("-" * 78)
    print("正控结果: %d/%d 全捕获" % (ok, total))
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
