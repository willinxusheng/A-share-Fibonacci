#!/usr/bin/env node
/* R900 前端渲染冒烟测试：在 node 里 mock DOM + echarts，真实执行 index.html 主脚本，
 * 检查回测面板（#btTable / #btFoot）是否正常渲染、状态列是否合理、R900 新文案是否生效。
 *
 * 目的不是替代浏览器，而是把"改完前端只做语法检查"升级为"真跑一遍数据路径"：
 * 只 check 语法会漏掉 undefined 字段访问、字符串拼接出 undefined、条件分支未命中等问题。
 *
 * 用法：node _R900_render_check.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const REPO = __dirname;

/* ---------- 最小 DOM mock（够 index.html 顶层脚本跑通） ---------- */
function makeEl(id, tag) {
  const el = {
    id: id || '', tagName: (tag || 'div').toUpperCase(),
    style: {}, dataset: {}, children: [],
    _html: '', _text: '',
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
    setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
    appendChild(c) { this.children.push(c); return c; },
    insertBefore(c) { this.children.push(c); return c; },
    removeChild() {}, remove() {},
    addEventListener() {}, removeEventListener() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    getElementsByTagName() { return []; }, getElementsByClassName() { return []; },
    cloneNode() { return makeEl(this.id, this.tagName); },
    getBoundingClientRect() { return { width: 1432, height: 600, top: 0, left: 0 }; },
    parentNode: null, parentElement: null, firstChild: null, nextSibling: null,
    focus() {}, blur() {}, click() {}, scrollIntoView() {},
    offsetWidth: 1432, offsetHeight: 600, clientWidth: 1432, clientHeight: 600,
    getContext() { return { measureText: () => ({ width: 10 }) }; },
  };
  el.parentNode = { insertBefore() {}, appendChild() {}, removeChild() {} };
  el.parentElement = el.parentNode;
  return el;
}
const _els = new Map();
function getEl(id) {
  if (!_els.has(id)) _els.set(id, makeEl(id));
  return _els.get(id);
}
const documentMock = {
  documentElement: makeEl('html', 'html'),
  body: makeEl('body', 'body'),
  head: makeEl('head', 'head'),
  createElement: (t) => makeEl('', t),
  createTextNode: (t) => ({ textContent: t }),
  getElementById: getEl,
  querySelector: () => null,
  querySelectorAll: () => [],
  getElementsByTagName: () => [],
  getElementsByClassName: () => [],
  addEventListener() {}, removeEventListener() {},
  createElementNS: (ns, t) => makeEl('', t),
};
global.document = documentMock;
global.window = {
  devicePixelRatio: 1, innerWidth: 1432, innerHeight: 900,
  addEventListener() {}, removeEventListener() {},
  document: documentMock, navigator: { userAgent: 'node', maxTouchPoints: 0 },
  getComputedStyle: () => ({ getPropertyValue: () => '' }),
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  requestAnimationFrame: (f) => setTimeout(f, 0),
  setTimeout, clearTimeout, setInterval, clearInterval,
  location: { search: '?v=x', pathname: '/', replace() {} },
};
// Node 22 起 global.navigator 是只读 getter，需用 defineProperty 覆盖（否则 TypeError）
try {
  Object.defineProperty(global, 'navigator', {
    value: { userAgent: 'node', maxTouchPoints: 0 }, configurable: true, writable: true,
  });
} catch (e) { /* 已有可写 navigator 时忽略 */ }
global.getComputedStyle = global.window.getComputedStyle;
global.matchMedia = global.window.matchMedia;
global.requestAnimationFrame = global.window.requestAnimationFrame;
global.IntersectionObserver = class { constructor() {} observe() {} unobserve() {} };
global.ResizeObserver = class { constructor() {} observe() {} unobserve() {} };

/* ---------- echarts 桩：只保证不抛错，不校验图形 ---------- */
function chartStub() {
  return {
    setOption() {}, getOption() { return {}; }, on() {}, off() {}, resize() {}, dispose() {},
    dispatchAction() {}, clear() {},
    getZr() { return { on() {}, off() {} }; },
    getWidth() { return 1432; }, getHeight() { return 600; },
    getDom() { return makeEl('', 'div'); },
  };
}
global.echarts = { init: chartStub, use() {}, registerTheme() {}, graphic: { extendShape: () => ({}) } };

/* ---------- 载入数据与主脚本 ---------- */
const dataSrc = fs.readFileSync(path.join(REPO, 'data', 'data.js'), 'utf8');
eval(dataSrc);                                    // 定义 window.FIB_DATA

/* data/ 由云端 CI 独家写盘，本地 data.js 里还没有 R900 新字段。
 * 为验证"新数据到位后"的渲染行为，允许注入 _R900_verify.py 真跑产出的 backtest 块。
 * 未注入时按现有 data.js 渲染，并把倒挂/缺文案降级为「待 CI 刷新」提示（不算失败）。 */
const os = require('os');
const injectPath = process.env.AUDIT_BACKTEST || path.join(os.tmpdir(), 'r900_backtest.json');
let injected = false;
if (fs.existsSync(injectPath)) {
  try {
    const btNew = JSON.parse(fs.readFileSync(injectPath, 'utf8'));
    if (btNew && btNew.summary) {
      global.window.FIB_DATA.backtest = btNew;
      injected = true;
    }
  } catch (e) { console.warn('注入 backtest 失败：' + e.message); }
}

const html = fs.readFileSync(path.join(REPO, 'index.html'), 'utf8');
const blocks = [...html.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const main = blocks.reduce((a, b) => (b.length > a.length ? b : a), '');

let fatal = null;
try {
  eval(main);
} catch (e) {
  fatal = e;
}

/* ---------- 断言 ---------- */
const btTable = getEl('btTable').innerHTML;
const btFoot = getEl('btFoot').innerHTML;
const fail = [];
const ok = [];
const warn = [];
const strict = injected;   // 注入新口径数据后才对"新字段相关"的断言做硬失败

if (fatal) fail.push('主脚本执行抛异常: ' + fatal.message + '\n' + String(fatal.stack).split('\n')[1]);

if (!btTable) fail.push('#btTable 为空（回测表未渲染）');
else {
  const rows = (btTable.match(/<tr>/g) || []).length - 1;
  ok.push(`#btTable 渲染 ${rows} 行数据`);
  if (/undefined/.test(btTable)) fail.push('#btTable 出现字面 "undefined"');
  if (/NaN/.test(btTable)) fail.push('#btTable 出现 "NaN"');
  if (/defensive/.test(btTable)) fail.push('#btTable 仍显示原始英文分类 "defensive"（catName 未映射）');
  else ok.push('分类名已中文化（无裸 defensive）');
  if (!/精确命中率\(独立目标\)/.test(btTable)) fail.push('表头未切到「精确命中率(独立目标)」');
  else ok.push('表头已标注独立目标口径');
  // 精确命中率单元格与 band 触达率不应再出现「精确 > band」的倒挂
  const trs = [...btTable.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].slice(1).map(m => m[1]);
  let inverted = 0;
  for (const tr of trs) {
    const tds = [...tr.matchAll(/<td>([\s\S]*?)<\/td>/g)].map(m => m[1]);
    if (tds.length < 5) continue;
    const num = (s) => { const m = /(\d+(?:\.\d+)?)%/.exec(s.replace(/<[^>]+>/g, ' ')); return m ? parseFloat(m[1]) : null; };
    const band = num(tds[3]);
    const prec = num(tds[4]);
    if (band != null && prec != null && prec > band + 0.05) inverted++;
  }
  if (inverted) {
    const msg = `存在 ${inverted} 行「精确命中率 > band 触达率」的倒挂`;
    if (strict) fail.push(msg);
    else warn.push(msg + '（当前 data.js 尚无 R900 去重字段，等 CI 下一轮刷新后消失）');
  } else ok.push('无精确命中率 > band 触达率 的倒挂行');
}

if (!btFoot) fail.push('#btFoot 为空');
else {
  if (!/R900/.test(btFoot)) {
    const msg = '#btFoot 缺少 R900 口径变更说明';
    if (strict) fail.push(msg);
    else warn.push(msg + '（新字段未到位，说明按条件渲染）');
  } else ok.push('脚注含 R900 无偏口径说明');
  // 允许「引述+已撤回」的写法，禁止把该断言当作现行结论陈述
  if (/引擎仍劣于随机起点/.test(btFoot) && !/已撤回/.test(btFoot)) {
    fail.push('脚注仍把「引擎仍劣于随机起点」作为现行结论');
  } else ok.push('已撤回「引擎仍劣于随机起点」的过度断言');
  if (/同期配对基线/.test(btFoot)) ok.push('脚注已并列展示同期配对基线');
  if (/undefined/.test(btFoot)) fail.push('#btFoot 出现字面 "undefined"');
}

console.log('=== R900 前端渲染冒烟 ===');
console.log('  数据源：' + (injected ? '注入 ' + injectPath + '（R900 新口径）' : '现有 data.js（无新字段）'));
ok.forEach(s => console.log('  ✅ ' + s));
warn.forEach(s => console.log('  ⚠ ' + s));
if (fail.length) {
  fail.forEach(s => console.log('  ❌ ' + s));
  console.log(`\n结果：${fail.length} 项失败`);
  process.exit(1);
}
console.log('\n结果：全部通过' + (warn.length ? `（${warn.length} 项待 CI 刷新）` : ''));
