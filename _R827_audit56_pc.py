#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R827 audit56 缺陷注入正控

目的：证明 audit56 的 11 项断言**真的能抓到对应的缺陷**，而不是恒真。
纪律（R824b 教训）：每个注入用例必须先自证两点 ——
  ① 产物前后 diff **非空**（真的改到了，不是 no-op）
  ② 该变更**按定义就是缺陷**（能说出违反了哪条不变量）
否则「用例写错」会被误判成「断言漏报」，诱发无意义的加固改动。

同时必须验证：注入后 audit56 的 **exit code = 1**（不能只看打印文字）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "data", "data.js")
PY = sys.executable


def load_src():
    s = open(SRC, encoding="utf-8").read()
    return s, json.loads(s[s.index("{"): s.rindex("}") + 1])


def write_js(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        f.write("window.DATA = ")
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")


def run_audit(path):
    p = subprocess.run([PY, os.path.join(BASE, "audit56.py"), path],
                       capture_output=True, text=True)
    failed = [l.strip() for l in p.stdout.splitlines() if "[FAIL]" in l]
    return p.returncode, failed, p.stdout


# ---- 注入器：每个必须真的改变数据 ----
def pc1_del_expdays(d):
    d["targets"][0].pop("expDays", None)


def pc2_past_expdate(d):
    d["targets"][0]["expDate"] = "2020-01-01"


def pc2b_weekend_expdate(d):
    d["targets"][0]["expDate"] = "2027-02-20"   # 周六


def pc3_window_mismatch(d):
    d["targets"][0]["reach"]["window"] = 30


def pc4_cls_wrong(d):
    d["targets"][0]["reach"]["cls"] = "achievable"   # prob 实为 ~5%


def pc5_prob_off(d):
    d["targets"][0]["reach"]["prob"] = round(d["targets"][0]["reach"]["prob"] + 10.0, 2)


def pc6_dailyrate_off(d):
    d["targets"][0]["reach"]["dailyRate"] = round(d["targets"][0]["reach"]["dailyRate"] * 2, 4)


def pc7_sell_target_diverged(d):
    d["tradePlan"]["sellTargets"][0]["reach"]["prob"] = 99.0


def pc8_band_count(d):
    for b in d["backtest"]["dirBandsByReach"]:
        if b["pending"] > 0:
            b["pending"] += 1
            return


def pc9_band_raw_inconsistent(d):
    # 只改 hits 不改 rateRaw ⇒ 必须被 T8a 抓到（首版 T8 只能读 rateRaw，会漏）
    for b in d["backtest"]["dirBandsByReach"]:
        if b["matured"] >= 5:
            b["hits"] = b["matured"]
            return


def pc10_inversion(d):
    # 把最低档做成 100%（并同步 rateRaw，绕过 T8a）⇒ 必须被 T8b 单调性抓到
    for b in d["backtest"]["dirBandsByReach"]:
        if b["matured"] >= 5:
            b["hits"] = b["matured"]
            b["rateRaw"] = 100.0
            return


def pc11_meta_lie(d):
    d["backtest"]["dirReachMeta"]["monotonic"] = True
    d["backtest"]["dirReachMeta"]["inversions"] = ["伪造逆向"]


def pc12_speed_off(d):
    d["reachSummary"]["wave5Implied"]["speedVsWave3"] = 9.99


def pc13_byclass(d):
    k = list(d["reachSummary"]["byClass"].keys())[0]
    d["reachSummary"]["byClass"][k] += 3


CASES = [
    ("PC1 删除 targets[0].expDays", pc1_del_expdays, "T1a"),
    ("PC2 expDate 改到过去", pc2_past_expdate, "T1b"),
    ("PC2b expDate 改成周六", pc2b_weekend_expdate, "T1b"),
    ("PC3 reach.window 与 expDays 不符", pc3_window_mismatch, "T2"),
    ("PC4 cls 与 prob 阈值矛盾", pc4_cls_wrong, "T3"),
    ("PC5 prob 偏移 +10pp", pc5_prob_off, "T4"),
    ("PC6 dailyRate 翻倍", pc6_dailyrate_off, "T5"),
    ("PC7 sellTargets.reach 与 targets 背离", pc7_sell_target_diverged, "T6"),
    ("PC8 分档 pending 多计 1", pc8_band_count, "T7"),
    ("PC9 只改 hits 不改 rateRaw", pc9_band_raw_inconsistent, "T8a"),
    ("PC10 最低档做成 100%（含 rateRaw）", pc10_inversion, "T8b"),
    ("PC11 monotonic=true 但 inversions 非空", pc11_meta_lie, "T9"),
    ("PC12 speedVsWave3 篡改", pc12_speed_off, "T10"),
    ("PC13 byClass 合计不符", pc13_byclass, "T10"),
]


def main():
    src_text, src_obj = load_src()
    tmpdir = tempfile.mkdtemp(prefix="r827pc_")
    ok_cnt = 0
    print("=" * 78)
    print("R827 audit56 缺陷注入正控")
    print("=" * 78)
    # 基线：未注入必须 PASS（否则后续"捕获"无意义）
    base = os.path.join(tmpdir, "base.js")
    write_js(base, src_obj)
    rc, failed, _ = run_audit(base)
    if rc != 0:
        print("!! 基线未通过，正控作废：%s" % failed)
        return 2
    print("基线：exit=0（干净数据通过）\n")

    for i, (name, fn, expect) in enumerate(CASES):
        d = json.loads(json.dumps(src_obj))   # deep copy
        before = json.dumps(d, ensure_ascii=False, sort_keys=True)
        fn(d)
        after = json.dumps(d, ensure_ascii=False, sort_keys=True)
        if before == after:
            print("  [NO-OP] %-42s 注入未改变数据 ⇒ 用例无效" % name)
            continue
        pth = os.path.join(tmpdir, "pc%02d.js" % i)
        write_js(pth, d)
        rc, failed, out = run_audit(pth)
        hit = any(expect in f for f in failed)
        status = "OK " if (rc == 1 and hit) else "MISS"
        if rc == 1 and hit:
            ok_cnt += 1
        names = [f.strip().replace("[FAIL] ", "", 1).split(" ")[0] for f in failed]
        print("  [%s] %-42s 期望 %-5s exit=%d 捕获=%s"
              % (status, name, expect, rc, names or "无"))
        if not (rc == 1 and hit):
            print("      !! 未按预期捕获；real output tail:")
            print("      " + "\n      ".join(out.strip().splitlines()[-6:]))

    shutil.rmtree(tmpdir, ignore_errors=True)
    print("-" * 78)
    print("正控结果: %d/%d 捕获" % (ok_cnt, len(CASES)))
    return 0 if ok_cnt == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
