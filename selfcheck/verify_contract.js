#!/usr/bin/env node
// verify_contract.js — data.js 字段契约硬检查（selfcheck 常驻工具）
//
// 用途：从 data/data.js（或指定路径）解析 FIB_DATA，硬检查前端消费字段齐全且无 NaN/undefined/
//       越界：state{text,cls}、tradePlan(stopLine/sellTargets/buyZones 价格数值)、scenarioSwitch
//       四分支、★ signals[i] 配 kline.ohlc[i] 长度越界风险、wavePoints/subWavePoints 价格等。
//       对应 R245 契约核对思路，固化进仓库作为部署前常驻自检。
// 依赖：Node.js（纯 fs + 正则 + JSON，无第三方包）。
// 用法：
//   node verify_contract.js [data.js路径]   # 默认 <仓库根>/data/data.js
// 退出码：0=契约全部通过；1=发现契约问题；2=解析失败。
'use strict';
const fs = require('fs');
const path = require('path');

const def = path.join(__dirname, '..', 'data', 'data.js');
const fp = process.argv[2] || def;
let raw;
try { raw = fs.readFileSync(fp, 'utf8'); }
catch (e) { console.log('读取失败:', fp, e.message); process.exit(2); }
const m = raw.match(/window\s*\.\s*FIB_DATA\s*=\s*(\{[\s\S]*\});?\s*$/);
if (!m) { console.log('PARSE FAIL', fp); process.exit(2); }
const D = JSON.parse(m[1]);

const issues = [];
const chk = (c, msg) => { if (!c) issues.push(msg); };
const numOK = (x) => typeof x === 'number' && !isNaN(x) && isFinite(x);

// state 徽章对象（前端 L428-429 用 .text/.cls）
chk(D.state && typeof D.state === 'object' && typeof D.state.text === 'string' && typeof D.state.cls === 'string', 'state 非 {text,cls} 对象');

// tradePlan 真实字段（前端 grep 证实消费字段）
chk(D.tradePlan && typeof D.tradePlan === 'object', 'tradePlan 缺失');
if (D.tradePlan) {
  chk(numOK(D.tradePlan.stopLine && D.tradePlan.stopLine.price), 'tradePlan.stopLine.price NaN');
  chk(Array.isArray(D.tradePlan.sellTargets) && D.tradePlan.sellTargets.length > 0, 'sellTargets 空/非数组');
  D.tradePlan.sellTargets.forEach((t, i) => {
    chk(numOK(t.price), 'sellTargets[' + i + '].price NaN');
    chk(numOK(t.hi), 'sellTargets[' + i + '].hi NaN');
    chk(numOK(t.lo), 'sellTargets[' + i + '].lo NaN');
  });
  chk(Array.isArray(D.tradePlan.buyZones) && D.tradePlan.buyZones.length > 0, 'buyZones 空/非数组');
  D.tradePlan.buyZones.forEach((z, i) => {
    chk(numOK(z.lo), 'buyZones[' + i + '].lo NaN');
    chk(numOK(z.hi), 'buyZones[' + i + '].hi NaN');
  });
  if (D.tradePlan.trailingStop !== undefined) {
    const ts = D.tradePlan.trailingStop;
    chk(numOK(ts.price) || (ts && typeof ts === 'object'), 'trailingStop 异常');
  }
}

// scenarioSwitch 三分支（实际数据模型只含 active/base/risk；strong 情景数据不走
// scenarioSwitch.strong，而是落在 D.scenarios[1] + D.subForecast，由前端 SCN_IDX
// 映射 strong→1 取用——见 index.html L405/L978/L1114。故此处只校验真实存在的三分支，
// strong 的真实契约改由下方 D.scenarios/subForecast 检查覆盖，避免永恒假阳性。）
['active', 'base', 'risk'].forEach(br =>
  chk(D.scenarioSwitch && D.scenarioSwitch[br] !== undefined, 'scenarioSwitch.' + br + ' 缺失'));

// 强势情景真实契约：D.scenarios[1]（SCN_IDX.strong=1）存在且含路径，且 D.subForecast 存在
// （前端 strong 模式渲染依赖这两项，而非 scenarioSwitch.strong）。
chk(Array.isArray(D.scenarios) && D.scenarios.length >= 3 &&
  D.scenarios[1] && Array.isArray(D.scenarios[1].points) && D.scenarios[1].points.length >= 2,
  'D.scenarios[1]（强势情景路径）缺失/非法');
chk(D.subForecast && Array.isArray(D.subForecast.points) && D.subForecast.points.length >= 2,
  'D.subForecast（强势子浪校准）缺失/非法');

// ★ 关键契约（修正 R29 假绿）：前端 L681-691 用 dateIdx[s.date] 按【日期】取 kline 索引，
// 而非数组下标。真实不变量是「每个 signals[i].date 必须存在于 kline.dates」，且对应 ohlc 行
// 价格非 NaN（前端取 ohlc[idx][2]=低 / [3]=高）。原检查按数组下标比对且 signals 长度远小于
// ohlc，恒通过——属假绿，从未真正校验前端依赖的日期对齐。
chk(Array.isArray(D.signals), 'signals 非数组');
chk(D.kline && Array.isArray(D.kline.dates) && Array.isArray(D.kline.ohlc), 'kline.dates/ohlc 缺失/非数组');
if (Array.isArray(D.signals) && D.kline && Array.isArray(D.kline.dates) && Array.isArray(D.kline.ohlc)) {
  const dateSet = new Set(D.kline.dates);
  const ohlcByDate = {};
  D.kline.dates.forEach((d, i) => { ohlcByDate[d] = D.kline.ohlc[i]; });
  D.signals.forEach((s, i) => {
    chk(s.signal === 1 || s.signal === -1 || s.signal === 0, 'signals[' + i + '].signal 异常值 ' + s.signal);
    chk(typeof s.date === 'string' && dateSet.has(s.date),
      'signals[' + i + '].date(' + s.date + ') 不在 kline.dates → 前端 L683 按 dateIdx 查不到将跳过该信号（孤儿信号）');
    const row = ohlcByDate[s.date];
    chk(Array.isArray(row) && numOK(row[2]) && numOK(row[3]),
      'signals[' + i + '] 对应 kline.ohlc[' + s.date + '] 行缺位/价格 NaN（前端取 [2]=低/[3]=高）');
  });
}

// wavePoints / subWavePoints 价格
(D.wavePoints || []).forEach((p, i) => chk(numOK(p.price), 'wavePoints[' + i + '].price NaN'));
(D.subWavePoints || []).forEach((p, i) => chk(numOK(p.price), 'subWavePoints[' + i + '].price NaN'));

// 模拟前端 _wpp("浪3")（L456/498/1211）
const w3 = (D.wavePoints || []).find(p => (p.label || '').indexOf('浪3') === 0);
chk(!!w3, '_wpp("浪3") 找不到浪3锚点');

// 模拟情景面板取数 SS.strong.note（L1154）
const SS = D.scenarioSwitch || {};
['strong', 'base', 'risk'].forEach(br => {
  if (SS[br] && SS[br].note !== undefined) chk(typeof SS[br].note === 'string', 'scenarioSwitch.' + br + '.note 非字符串');
});

// 补足契约覆盖盲区（R245 续）：原只校验 state/tradePlan/scenarioSwitch/scenarios/subForecast/
// signals/kline/wavePoints 子集，漏查的字段一旦缺失会让前端对应渲染 TypeError 白屏而本门禁
// 假绿通过。这些字段均由 build_data 产出，在此补齐存在性硬检查，使重构回归也能被门禁拦截。
chk(Array.isArray(D.zigzag), 'zigzag 缺失/非数组（前端 L850 遍历）');
chk(Array.isArray(D.fib5y) && D.fib5y.length > 0, 'fib5y 缺失/空（前端 L888）');
chk(Array.isArray(D.targets) && D.targets.length > 0, 'targets 缺失/空（前端 L1068 目标线）');
chk(Array.isArray(D.supports) && D.supports.length > 0, 'supports 缺失/空（前端 L1072 支撑线）');
chk(Array.isArray(D.findings), 'findings 缺失/非数组（前端 L806）');
chk(Array.isArray(D.rules), 'rules 缺失/非数组（前端 L811）');
chk(D.channel && Array.isArray(D.channel.upper) && Array.isArray(D.channel.lower),
    'channel.upper/lower 缺失（前端 L1060-1062 艾略特通道）');
chk(Array.isArray(D.tzWaveStart), 'tzWaveStart 缺失/非数组（前端 L903 时间窗）');
chk(Array.isArray(D.tzWave3Top), 'tzWave3Top 缺失/非数组（前端 L909 时间窗）');

console.log('state.cls =', D.state && D.state.cls);
console.log('sellTargets =', ((D.tradePlan && D.tradePlan.sellTargets) || []).length,
  ' buyZones =', ((D.tradePlan && D.tradePlan.buyZones) || []).length,
  ' signals =', (D.signals || []).length,
  ' kline.ohlc =', ((D.kline && D.kline.ohlc) || []).length);
console.log('精确字段契约检查: ' + (issues.length
  ? ('发现 ' + issues.length + ' 处:\n- ' + issues.join('\n- '))
  : '全部通过 — 无 NaN / undefined / 越界，前端消费字段齐全，signals 日期全部落在 kline.dates 且与 ohlc 行对齐'));
process.exit(issues.length ? 1 : 0);
