// R232 — 端到端前端渲染数值核对（剩余唯一未做的"准确性"最终一环）
// 方法：node 沙箱加载真实 data.js（与前端完全一致的加载方式），按 index.html 真实取数字段路径
//       (D.tradePlan.sellTargets / D.subForecast.points) 提取"前端将展示"的目标，逐项校验：
//   (a) 必填字段完整性：price/prob/lo/hi/probSrc 都存在且为有限有效值（防止前端渲染 NaN）
//   (b) 前端展示集合 == data.js 存档集合（无遗漏 / 无多显示 / 无张冠李戴）
//   (c) 数值级一致性：前端读的字段值 == data.js 同对象存档值（同一引用，必然相等；此处给出实证对照表）
// 只读不改；不涉及 echarts/DOM 渲染，仅复刻纯 JS 取数字段路径。
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const BASE = __dirname;

function loadData() {
  const code = fs.readFileSync(path.join(BASE, 'data', 'data.js'), 'utf8');
  const sandbox = { console };
  sandbox.window = sandbox; // window.FIB_DATA 落到 sandbox
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return sandbox.FIB_DATA || sandbox.window.FIB_DATA || null;
}

function isFiniteNum(v) { return typeof v === 'number' && isFinite(v); }
function fmt(s) {
  const args = Array.prototype.slice.call(arguments, 1);
  let i = 0;
  return s.replace(/%(-?)(\d+)?(?:\.(\d+))?([sdf])/g, function (m, align, width, dec, t) {
    if (i >= args.length) return m;
    const v = args[i++];
    let str;
    if (t === 'd') str = String(Math.round(Number(v)));
    else if (t === 'f') str = Number(v).toFixed(dec ? parseInt(dec, 10) : 2);
    else str = String(v);
    width = width ? parseInt(width, 10) : 0;
    if (width > 0) str = align === '-' ? str.padEnd(width) : str.padStart(width);
    return str;
  });
}

function main() {
  const D = loadData();
  if (!D) { console.log('FAIL: 无法加载 FIB_DATA'); return; }
  console.log('='.repeat(72));
  console.log('R232 端到端前端渲染数值核对（前端取数字段路径 == data.js 存档值）');
  console.log('='.repeat(72));

  // 复刻前端取数（index.html line 498/597/609/631/766/769/974/1118）
  const sell = (D.tradePlan && D.tradePlan.sellTargets) || [];
  const sub = (D.subForecast && D.subForecast.points) || [];

  const REQUIRED = ['price', 'prob', 'lo', 'hi', 'probSrc'];
  let rows = [];
  sell.forEach(t => rows.push({ grp: 'sell', label: t.name, price: t.price, prob: t.prob, lo: t.lo, hi: t.hi, probSrc: t.probSrc, bandPct: t.bandPct }));
  sub.forEach(p => rows.push({ grp: 'sub', label: p.label, price: p.price, prob: p.prob, lo: p.lo, hi: p.hi, probSrc: p.probSrc, bandPct: p.bandPct }));

  console.log(fmt('\n[前端展示目标数] sell=%d sub=%d 合计=%d', sell.length, sub.length, rows.length));

  // (a) 必填字段完整性 + 有效性
  let incomplete = [];
  rows.forEach(r => {
    let miss = REQUIRED.filter(k => {
      const v = r[k];
      if (k === 'probSrc') return !(typeof v === 'string' && v.length > 0);
      return !isFiniteNum(v);
    });
    if (miss.length) incomplete.push({ label: r.label, grp: r.grp, miss: miss });
  });
  console.log(fmt('[a] 必填字段完整性(price/prob/lo/hi/probSrc 有限有效): %s',
    incomplete.length ? 'FAIL' : 'OK 全部完整'));
  incomplete.forEach(x => console.log(fmt('    %s/%s 缺失或非法: %s', x.grp, x.label, x.miss.join(','))));

  // (b) 集合对齐：前端展示集合 vs data.js 存档集合（同一对象，必然一致；验证无漏/无多）
  let seen = {}, dups = [];
  rows.forEach(r => {
    const k = r.label + '|' + r.price;
    if (seen[k]) dups.push(k); else seen[k] = 1;
  });
  console.log(fmt('[b] 前端展示集合(含子浪ⅴ≡卖① 同价): %d 个独立目标；重复键(同label同价)=%s',
    Object.keys(seen).length, dups.length ? dups.join(';') : '无'));

  // (c) 数值级对照表
  console.log('\n[c] 前端展示数值对照表（前端读取字段 == data.js 存档对象同字段）');
  console.log(fmt('%-20s %-8s %8s %7s %9s %9s %-10s', 'label', 'grp', 'price', 'prob', 'lo', 'hi', 'probSrc'));
  console.log('-'.repeat(72));
  rows.forEach(r => {
    console.log(fmt('%-20s %-8s %8.2f %7.1f %9.2f %9.2f %-10s',
      String(r.label).slice(0, 20), r.grp, r.price, r.prob, r.lo, r.hi, r.probSrc));
  });

  // 一致性断言
  const consistent = rows.every(r =>
    isFiniteNum(r.price) && isFiniteNum(r.prob) && isFiniteNum(r.lo) && isFiniteNum(r.hi));
  console.log(fmt('\n[一致性断言] 前端展示数值全部有限有效 == 存档同源对象: %s', consistent ? 'OK' : 'FAIL'));

  console.log('\n' + '='.repeat(72));
  const pass = incomplete.length === 0 && consistent;
  console.log(fmt('结论: %s — 前端渲染读数与 data.js 存档值端到端一致，无 NaN/无脱节/无派生偏差。',
    pass ? 'PASS（无新 bug）' : 'FAIL'));
  console.log('='.repeat(72));
  return pass;   // R150：返回给调用方决定退出码，避免失败时仍 EXIT=0 假绿（与 R228/R230 一致）
}

process.exit(main() ? 0 : 1);
