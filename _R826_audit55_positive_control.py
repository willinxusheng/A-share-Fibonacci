#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit55 缺陷注入正控（R826）。

纪律：每个用例必须自证 ① 篡改后产物 diff 非空；② 该变更是【按定义】的缺陷。
否则"未捕获"可能是用例写错，而不是断言漏报。

用例：
  PC1 dirEarlyHitCount 置 0        → A1 三态恒等式失衡
  PC2 dirRealizedHitRateMatured 抄成全窗口值 → A2（双口径无差异）+ A4（复算不一致）
  PC3 dirBaseRecentValidKeys 置 0  → A3（自洽）+ A6
  PC4 dirBaseRateRecent 改 19.5    → A5（与独立复算不符）——模拟退回旧口径的伪值
  PC5 dirBaseRecentSkippedKeys 置 0 → A3（valid+skipped != total）+ A6
  PC6 删除 dirRealizedHitRateMatured → A2/A4
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BT = os.path.join(HERE, "data", "backtest.json")
BAK = BT + ".r826bak"
PY = sys.executable

shutil.copyfile(BT, BAK)


def run():
    p = subprocess.run([PY, os.path.join(HERE, "audit55.py")], cwd=HERE,
                       capture_output=True, text=True)
    return p.returncode, p.stdout


def load():
    return json.load(open(BT, encoding="utf-8"))


def save(d):
    json.dump(d, open(BT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


CASES = [
    ("PC1 dirEarlyHitCount→0", lambda d: d.__setitem__("dirEarlyHitCount", 0), ["A1", "A1b"]),
    ("PC2 matured 抄成全窗口值",
     lambda d: d.__setitem__("dirRealizedHitRateMatured", d["dirRealizedHitRate"]), ["A2", "A4"]),
    # 注：PC3 只破坏 A3 自洽性（0+155 != 466），skippedKeys 仍够多故 A6 通过。
    # 初版把期望写成 [A3, A6] 属**用例写错**（A6 只关心 skipped 是否 >= 长窗 key 数，与 valid 无关）。
    ("PC3 validKeys→0", lambda d: d.__setitem__("dirBaseRecentValidKeys", 0), ["A3"]),
    ("PC4 dirBaseRateRecent→19.5（旧口径伪值）",
     lambda d: d.__setitem__("dirBaseRateRecent", 19.5), ["A5"]),
    ("PC5 skippedKeys→0", lambda d: d.__setitem__("dirBaseRecentSkippedKeys", 0), ["A3", "A6"]),
    ("PC6 删除 dirRealizedHitRateMatured",
     lambda d: d.pop("dirRealizedHitRateMatured", None), ["A2", "A4"]),
    # PC7 专测 A6：让 valid+skipped 仍等于 total（A3 通过），但 skipped 少于长窗 key 数
    # ⇒ 说明生产把一部分"同期内无完整未来窗"的 key 仍拿去采样了（旧口径回归）。
    ("PC7 skipped 缩到 66（和仍等于 total）",
     lambda d: (d.__setitem__("dirBaseRecentSkippedKeys", 66),
                d.__setitem__("dirBaseRecentValidKeys", d["dirBaseTotalKeys"] - 66)), ["A6"]),
]

print("=== audit55 缺陷注入正控 ===")
base_rc, base_out = run()
print("  基线（未注入）：exit=%d 结论行=%s"
      % (base_rc, [l for l in base_out.splitlines() if l.startswith("audit55 结论")][-1:]))
ok_n = 0
try:
    for name, mut, expect in CASES:
        d0 = load()
        before = json.dumps(d0, sort_keys=True)
        mut(d0)
        after = json.dumps(d0, sort_keys=True)
        if before == after:
            print("  [SKIP] %-38s 注入是 no-op（diff 为空），用例本身无效" % name)
            continue
        save(d0)
        rc, out = run()
        failed = [t for t in expect if ("[FAIL] " + t) in out]
        caught = (rc != 0) and len(failed) == len(expect)
        print("  [%s] %-38s exit=%d 捕获=%s（期望 %s）"
              % ("PASS" if caught else "MISS", name, rc, failed, expect))
        ok_n += caught
        shutil.copyfile(BAK, BT)      # 每个用例后立即恢复
finally:
    shutil.copyfile(BAK, BT)
    os.remove(BAK)

print()
print("正控结果：%d/%d 捕获；基线产物已恢复" % (ok_n, len(CASES)))
rc2, _ = run()
print("恢复后复跑 audit55 exit=%d %s" % (rc2, "✓" if rc2 == 0 else "✗ 未恢复干净"))
sys.exit(0 if (ok_n == len(CASES) and rc2 == 0) else 1)
