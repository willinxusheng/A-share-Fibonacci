# -*- coding: utf-8 -*-
"""audit52 (R124): 前端↔数据脱节 + 前端运行时崩溃 守门员。

闭环 R121 发现的唯一盲区：现有 validate/audit49-51 只查数值一致性/不变量，
从不管「前端访问的字段在真实 data.js 里是否真的存在」以及「构造图表时 JS 是否崩溃」。

- Part A（硬闸，必须过）：静态核对 index.html 对 data.js 的全部字段依赖确实存在
  （顶层字段 + 关键嵌套路径 + sellTargets 结构 + ohlc 顺序不变量）。
  若 build_data 改了字段名/结构而前端没同步，这里会在部署前拦下。
- Part B（node 可用时运行）：在 node 沙箱里用真实 data.js 执行 index.html 主脚本。
  R审计加固：document.getElementById 对「HTML 中真实不存在的 id」返回 null（对齐浏览器），
  使「引用未兜底缺失 id」的崩溃类回归可被捕获并**硬失败(EXIT=1)**；另新增 RT_BLANK 软检查，
  跑完后断言核心面板被填充，静默早退类回归提示人工复核（不阻断）。node 不可用时跳过。

任一硬闸失败 → EXIT=1，阻断自动化部署。
"""
import os
import re
import sys
import json
import subprocess
import tempfile
import shutil

BASE = os.path.dirname(os.path.abspath(__file__))


def _find_node():
    """跨平台定位 node 可执行文件。

    R160: 原代码硬编码 Windows 绝对路径 NODE_EXE，在 Mac/Linux 上 os.path.exists 恒
    False，导致第六守门员 _part_b_runtime（前端运行时沙箱崩溃检查）永远静默跳过——
    即便 Mac 已装 node 也用不上，部署少了这道防护。改为探测：
      1) 优先系统 PATH 中的 node（Mac 用 brew/nvm 装的、Linux 系统 node、Windows 均适用）；
      2) 回退 WorkBuddy 管理的 node（Windows 绝对路径 + Mac/Linux 的 ~/.workbuddy 布局）。
    返回 None 时调用方跳过运行时沙箱（与原 skip 分支语义一致）。
    """
    p = shutil.which("node")
    if p:
        return p
    candidates = [
        r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2\node.exe",
        os.path.expanduser("~/.workbuddy/binaries/node/versions/22.22.2/node"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


NODE_EXE = _find_node()

# 前端真实依赖的关键嵌套路径（R121 验证过）
NESTED_PATHS = [
    "tradePlan.sellTargets", "tradePlan.stopLine.price", "tradePlan.buyZones",
    "tradePlan.subRefs", "tradePlan.trailingStop.rules",
    "kline.close", "kline.dates", "kline.ohlc", "kline.ma20", "kline.ma60", "kline.ma120", "kline.ma250",
    "kline.rsi", "kline.volume",
    "subForecast.calib", "subForecast.points", "subForecast.rows",
    "volatility.now", "volatility.pctile", "volRegime.bucket",
    "distances.toKeyLine",
    # R136：闭合「前端直接读/迭代但未受 NESTED 覆盖」的盲区。
    # 这些嵌套字段被 HTML 直接读或 .map/.forEach 迭代；若 build_data 改名，
    # 旧 Part A 只查顶层对象会放过，而 Part B 运行时检查是软警告(不阻断)——
    # 会让坏前端部署上线。下列路径均经 data.js 实查存在，加入后改为硬闸。
    "channel.lower", "channel.upper",          # D.channel.lower/upper.map -> 缺失即崩
    "state.text", "state.cls",                # 直接读，缺失显 'undefined'
    "distances.toTargetLow", "distances.toTargetHigh",
    "distances.toPrevHigh", "distances.fromW4Low",  # 仪表关键值，缺失=NaN
    "subForecast.crossCheck", "subForecast.realRef",  # 虽 ||{} 兜底，改名亦应拦
    "resonance.style", "resonance.style.groups",     # R168 风格轮动，改名应拦
    # R随机应变：情景自适应切换字段（子浪面板按 active 自动渲染三套，故这些嵌套路径必须存在）
    "scenarioSwitch.active", "scenarioSwitch.lastClose", "scenarioSwitch.keyLine",
    "scenarioSwitch.w4Low", "scenarioSwitch.rules",
    "scenarioSwitch.base", "scenarioSwitch.base.path", "scenarioSwitch.risk", "scenarioSwitch.risk.path",
    # R第八轮：闭合「反向覆盖缺口」——前端直接依赖但此前未列入清单的一级字段。
    # 这些字段若被 build_data 改名/删除，旧 Part A 清单不会拦截（门禁虚设残余盲区）。
    # 经 _audit_cov.py 反向比对确认：下列均为前端真实依赖且 data.js 实存，加入后改为硬闸。
    "lastClose", "updated", "wavePoints", "targets", "supports",
    "scenarios", "backtest", "crossMarket", "ratioCheck", "volByWave",
    "fib5y", "signals", "zigzag", "findings", "rules",
    "tzWaveStart", "tzWave3Top", "tzBaseTop", "w2Days", "spark",
]


def _load_data():
    src = open(os.path.join(BASE, "data", "data.js"), encoding="utf-8").read()
    return json.loads(re.search(r"window\.FIB_DATA\s*=\s*(\{.*\})\s*;?\s*$", src, re.S).group(1))


def _check_path(obj, path):
    cur = obj
    for tok in re.findall(r"[a-zA-Z0-9_]+|\[[0-9]+\]", path):
        if tok.startswith("["):
            idx = int(tok[1:-1])
            if not isinstance(cur, list) or idx >= len(cur):
                return False
            cur = cur[idx]
        else:
            if not isinstance(cur, dict) or tok not in cur:
                return False
            cur = cur[tok]
    return True


def _part_a(D, html):
    """返回 (fails, msgs)。"""
    fails = 0
    msgs = []

    # 1) 顶层字段依赖
    tops = set(re.findall(r"D\[(['\"])([a-zA-Z0-9_]+)\1\]", html))
    tops |= set(re.findall(r"\bD\.([a-zA-Z0-9_]+)", html))
    missing_top = sorted(t for t in tops if t not in D)
    if missing_top:
        fails += 1
        msgs.append("  [FAIL] 前端引用但 data.js 缺失的顶层字段: %s" % ", ".join(missing_top))
    else:
        msgs.append("  ok 前端顶层字段依赖全部存在 (%d 个)" % len(tops))

    # 2) 关键嵌套路径
    bad_nested = [p for p in NESTED_PATHS if not _check_path(D, p)]
    if bad_nested:
        fails += 1
        msgs.append("  [FAIL] 前端依赖的嵌套路径缺失: %s" % ", ".join(bad_nested))
    else:
        msgs.append("  ok 前端关键嵌套路径全部存在 (%d 条)" % len(NESTED_PATHS))

    # 3) sellTargets 结构
    st = D.get("tradePlan", {}).get("sellTargets", [])
    if len(st) < 3 or any(("price" not in s or "lo" not in s or "hi" not in s) for s in st):
        fails += 1
        msgs.append("  [FAIL] tradePlan.sellTargets 结构异常 (数量<%d 或字段缺失)" % 3)
    else:
        msgs.append("  ok sellTargets 结构完整 (%d 档, 均含 price/lo/hi)" % len(st))

    # 4) ohlc 顺序不变量 + 长度对齐
    oh = D.get("kline", {}).get("ohlc", [])
    dt = D.get("kline", {}).get("dates", [])
    bad = 0
    for row in oh:
        o, c, l, h = row[0], row[1], row[2], row[3]
        if not (l <= min(o, c) <= max(o, c) <= h):
            bad += 1
    if bad:
        fails += 1
        msgs.append("  [FAIL] ohlc 违反 low<=OH<=high 的行: %d" % bad)
    elif len(oh) != len(dt):
        fails += 1
        msgs.append("  [FAIL] ohlc/dates 长度不一致 %d vs %d" % (len(oh), len(dt)))
    else:
        msgs.append("  ok ohlc 顺序不变量 + 长度对齐 (%d 行)" % len(oh))

    return fails, msgs


def _part_b_runtime():
    """node 沙箱执行 index.html 主脚本，捕获运行时错误。返回 (level, text)。"""
    if not NODE_EXE or not os.path.exists(NODE_EXE):
        return "skip", "  -- node 不可用，跳过运行时沙箱检查（仅静态字段检查生效）"
    js = r"""
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const BASE = process.argv[2];
const ds = fs.readFileSync(path.join(BASE,'data','data.js'),'utf-8');
const m = ds.match(/window\.FIB_DATA\s*=\s*(\{[\s\S]*\})\s*;?\s*$/);
const dataObj = JSON.parse(m[1]);
// R审计加固：加载缠论视图快照，使 index.html 缠论面板渲染代码在沙箱中被真正执行
// （此前仅加载 data.js，缠论面板整段从未跑过 -> 该面板崩溃 audit52 也抓不到，盲区）。
const cvs = fs.readFileSync(path.join(BASE,'data','chanlun_view.js'),'utf-8');
const cm = cvs.match(/window\.CHANLUN_VIEW\s*=\s*(\{[\s\S]*\})\s*;?\s*$/);
const chanlunView = cm ? JSON.parse(cm[1]) : undefined;
const html = fs.readFileSync(path.join(BASE,'index.html'),'utf-8');
// R审计加固：解析 HTML 真实 id，未知 id 真实返回 null（对齐浏览器），
// 让「引用了但未兜底缺失 id」的崩溃类回归可被捕获（此前 stub 永远非 null 漏检）。
const knownIds = new Set([...html.matchAll(/id="([^"]+)"/g)].map(x=>x[1]));
const writes = {};
function mkEl(id){
  const el = { id, style:{}, getContext:()=>({}), appendChild(){}, addEventListener(){},
    getAttribute(){return null;}, setAttribute(){}, offsetWidth:800, offsetHeight:400,
    classList:{add(){},remove(){}},
    parentNode:{ insertBefore(){}, removeChild(){}, appendChild(){} } };
  Object.defineProperty(el,'innerHTML',{ set(v){ this._h=String(v); writes[id]={h:this._h,t:this._t||''}; }, get(){ return this._h||''; } });
  Object.defineProperty(el,'textContent',{ set(v){ this._t=String(v); writes[id]={h:this._h||'',t:this._t}; }, get(){ return this._t||''; } });
  return el;
}
const elCache = {};
const document = {
  getElementById:(id)=>{ if(knownIds.has(id)){ return elCache[id]||(elCache[id]=mkEl(id)); } return null; },
  querySelector:()=>({ style:{}, appendChild(){}, addEventListener(){}, innerHTML:'', textContent:'' }),
  querySelectorAll:()=>[],
  createElement:()=>({ style:{}, appendChild(){}, setAttribute(){}, getContext:()=>({}) }),
  addEventListener:()=>{}, body:{ innerHTML:'', style:{}, appendChild(){} }, write:()=>{} };
const echarts = { init:()=>({ setOption:()=>{}, resize:()=>{}, on:()=>{}, dispose:()=>{} }) };
const window = { FIB_DATA: dataObj, CHANLUN_VIEW: chanlunView, addEventListener:()=>{}, devicePixelRatio:1, location:{} };
const sandbox = { window, document, echarts, console, setTimeout:()=>{}, clearTimeout:()=>{},
  setInterval:()=>{}, Math, JSON, Date, parseInt, parseFloat, isNaN,
  Array, Object, String, Number, RegExp, FIB_DATA: dataObj };
sandbox.window.document = document;
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(x=>x[1]);
const code = scripts.join('\n;\n');
try {
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { timeout: 8000 });
  // 软检查：核心面板必须被填充，静默早退类回归暴露给人工复核（不阻断部署）
  const MUST = ['upd0','upd1','heroPrice','heroVol','spark','gauges','findings','rules','waveFlow','triggerCallout','ratioTable','volTable','targetTable','fibTable','buyTable','sellTable','planNote','btTable','subFTable','subFCalib','subFTableTitle'];
  const blank = MUST.filter(id=>{ const w=writes[id]; return !(w && ((w.h&&w.h.trim())||(w.t&&w.t.trim()))); });
  if(blank.length){ console.log('RT_BLANK: '+blank.join(',')); } else { console.log('RT_OK'); }
} catch(e) {
  console.log('RT_ERROR: ' + (e && e.message ? e.message : e));
}
"""
    tmp = os.path.join(tempfile.gettempdir(), "_audit52_rt.js")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(js)
        r = subprocess.run([NODE_EXE, tmp, BASE], capture_output=True, text=True, timeout=30)
        out = (r.stdout or "") + (r.stderr or "")
        if "RT_ERROR" in out:
            # R审计加固：前端运行时崩溃是真实缺陷，必须阻断部署。
            # 注意：必须返回 "fail"（main 据此 EXIT=1），此前误返回 "warn" 导致
            # 该硬失败分支成为死代码（RT_ERROR 仍只 warning、不阻断部署）——门禁虚设盲区。
            return "fail", "  [FAIL] 前端运行时沙箱捕获错误: " + out.strip().split("RT_ERROR:")[-1][:200]
        # R审计加固(2026-08-18)：此前未检查 returncode，且 chanlun_view.js 的 readFileSync
        # 位于 node try 块之外——该文件缺失/改名会致 node 顶层抛 ENOENT、退出码≠0、stderr
        # 不含 RT_ERROR，被误判 "ok" 静默通过（门禁假绿，缠论面板退化无人察觉）。现补
        # returncode 硬检查：node 异常退出一律视为硬失败，阻断部署。
        if r.returncode != 0:
            return "fail", "  [FAIL] 前端运行时沙箱进程异常退出(code=%d): %s" % (r.returncode, out.strip()[:200])
        # R审计加固(2026-08-18b)：RT_BLANK 软警告（核心面板未填充=静默早退类回归）原被
        # 静默吞噬、不打印到 CI 日志，与上方注释"暴露给人工复核"承诺不符，运维看不见前端
        # 退化信号。现提取并打印（仍不阻断部署，仅提升可见性，lvl 保持 "ok"）。
        _blank = ""
        for _ln in (r.stdout or "").splitlines():
            if _ln.startswith("RT_BLANK:"):
                _blank = "\n  [note] 前端运行时软警告(不阻断): 未填充面板 -> " + _ln[len("RT_BLANK:"):].strip()
                break
        return "ok", "  ok 前端运行时沙箱执行 0 错误" + _blank
    except subprocess.TimeoutExpired:
        # 前端死循环/挂死：vm 8s 超时未覆盖到的进程级挂死 → 真实缺陷，必须阻断部署。
        return "fail", "  [FAIL] 前端运行时沙箱超时(疑似前端死循环/挂死)"
    except Exception as e:
        # 其余子进程异常(节点不可用/权限/临时文件写失败等)：守门员无法验证即不得静默放过，
        # 改为硬失败阻断部署（避免旧的 "warn" 绿掩盖真实前端缺陷，R85 纪律）。
        return "fail", "  [FAIL] 运行时沙箱执行异常: %s" % e
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


def main():
    print("== audit52: 前端↔数据脱节 + 运行时崩溃 守门 ==")
    fails = 0
    try:
        D = _load_data()
    except Exception as e:
        print("  [FAIL] data.js 无法解析: %s" % e)
        sys.exit(1)

    html = open(os.path.join(BASE, "index.html"), encoding="utf-8").read()
    a_fails, a_msgs = _part_a(D, html)
    fails += a_fails
    for m in a_msgs:
        print(m)

    lvl, b_txt = _part_b_runtime()
    print(b_txt)
    if lvl == "fail":
        # 前端运行时崩溃是真实缺陷，必须阻断部署（此前 RT_ERROR 仅软警告，属盲区）
        fails += 1
        print("  (FAIL: 前端运行时崩溃，阻断部署)")
    elif lvl == "warn":
        # 软检查不阻断部署，但提示人工关注
        print("  (note: 运行时警告不阻断部署，请人工复核)")

    if fails == 0:
        print("\naudit52 结论：全部通过 ✓")
        sys.exit(0)
    else:
        print("\naudit52 结论：发现 %d 处硬性问题，EXIT=1 阻断部署。" % fails)
        sys.exit(1)


if __name__ == "__main__":
    main()
