'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

// node-canvas 是原生包，在 GitHub ubuntu-latest runner上无法编译（缺 cairo/pango 系统库），
// 会导致第6道门禁在 CI 被静默跳过。本脚本只用其 getContext('2d').measureText 估算文字宽，
// 故在此提供纯 JS 兜底——优先用真实/垫片 canvas，缺失时回退，保证 CI 也能真跑此门禁。
// 兜底逻辑与本地 node_modules/canvas/index.js 垫片一致：CJK 字符 = fontSize px，ASCII = 0.55*fontSize。
let createCanvas;
try {
  ({ createCanvas } = require('canvas'));
} catch (e) {
  const _charW = (ch, fs) => (ch.charCodeAt(0) > 255 ? fs : fs * 0.55);
  createCanvas = function () {
    return {
      width: 0, height: 0,
      getContext: function () {
        return {
          font: '12px sans-serif', fillStyle: '', strokeStyle: '',
          measureText: function (t) {
            let fs = 12; const m = /(\d+(?:\.\d+)?)px/.exec(this.font || '');
            if (m) fs = parseFloat(m[1]);
            let w = 0; for (const ch of String(t)) w += _charW(ch, fs);
            return { width: w };
          },
          fillRect() {}, clearRect() {}, fillText() {}, strokeText() {},
          beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, fill() {},
          save() {}, restore() {}, setTransform() {}, scale() {}, translate() {},
          arc() {}, rect() {}, closePath() {}, clip() {},
          createLinearGradient() { return { addColorStop() {} }; }
        };
      },
      toDataURL: function () { return ''; }
    };
  };
}

const ROOT = __dirname;
const DATA_JS = path.join(ROOT, 'data/data.js');

// ---------- load data.js ----------
const rawData = fs.readFileSync(DATA_JS, 'utf-8');
const dm = rawData.match(/window\s*\.\s*FIB_DATA\s*=\s*(\{[\s\S]*\});?\s*$/);
if (!dm) { console.error('FIB_DATA parse fail'); process.exit(1); }
const D = JSON.parse(dm[1]);

// ---------- mock DOM ----------
function canvasCtx() { return createCanvas(300, 150).getContext('2d'); }
function makeEl(id) {
  const el = {
    id: id || ('el_' + Math.random().toString(36).slice(2)),
    style: {}, className: '', value: '', children: [],
    classList: { add() {}, remove() {}, toggle() {} },
    appendChild(c) { this.children.push(c); return c; },
    removeChild() {}, insertBefore(c) { this.children.unshift(c); return c; },
    addEventListener() {}, removeEventListener() {},
    getAttribute() { return null; }, setAttribute() {},
    getContext() { return canvasCtx(); },
    getBoundingClientRect() { return { width: 1000, height: 600, left: 0, top: 0 }; },
    offsetWidth: 1000, offsetHeight: 600,
    querySelector() { return null; }, querySelectorAll() { return []; },
    focus() {}, blur() {}
  };
  el.parentNode = { insertBefore() {}, removeChild() {}, appendChild() {} };
  let _t = '', _h = '';
  Object.defineProperty(el, 'textContent', { get() { return _t; }, set(v) { _t = String(v); } });
  Object.defineProperty(el, 'innerHTML', { get() { return _h; }, set(v) { _h = String(v); } });
  return el;
}
const _els = {};
const document = {
  getElementById(id) { if (!_els[id]) _els[id] = makeEl(id); return _els[id]; },
  createElement(tag) { if (tag === 'canvas') return { getContext() { return canvasCtx(); } }; return makeEl(); },
  body: makeEl('body'),
  // echarts 模块求值阶段会读 document.documentElement.style（env 探测），SSR 下需一个最小可用的元素
  documentElement: { style: {}, clientWidth: 1000, clientHeight: 600, getElementsByTagName: function () { return []; } },
  addEventListener() {}, querySelector() { return null; },
  querySelectorAll() { return []; }, getElementsByClassName() { return []; }, getElementsByTagName() { return []; }
};

const windowObj = { devicePixelRatio: 1, addEventListener() {}, FIB_DATA: D, navigator: { userAgent: 'node' } };

// ---------- 移动端验证开关（AUDIT_MOBILE=1）：用手机宽度渲染，验证 R211 响应式滚动条不重叠 ----------
const MOB = process.env.AUDIT_MOBILE === '1';
windowObj.innerWidth = MOB ? 375 : 1432;  // 让 index.html 的 IS_MOB() 命中对应分支

// ---------- 必须【先】把 mock DOM / window 挂到全局，再 require echarts ----------
// 根因(R233)：echarts 在模块求值阶段即访问全局 window（env.touchEventsSupported = 'ontouchstart' in window），
// 旧代码 require 在最顶部、global.window 直到文件末尾才赋值，导致本脚本在 node 下永远
// ReferenceError: window is not defined —— 标注重叠维度实际从未被运行时验证。
// 修正顺序：mock DOM → 挂 global.window/document → require echarts。
windowObj.document = document;
global.window = windowObj;
global.document = document;
const realEcharts = require('echarts');

// ---------- echarts wrapper that captures SVG per container id ----------
// 真实桌面渲染宽度（.wrap max-width 1480 − 左右 padding 48 ≈ 1432 画布）；此前用 1000 过窄，
// 会误触发 chart2 图例 scroll 的「1/2」页码指示与图例项重叠的假阳性。改回真实宽度。
// 移动端（AUDIT_MOBILE=1）按 375 窄屏容器实测：.wrap(±12) + .panel(±13) ≈ 325 宽，图表高 460。
const DIMS = MOB
  ? { chart1: [325, 460], chart2: [325, 460], chartSubF: [325, 460] }
  : { chart1: [1432, 900], chart2: [1432, 640], chartSubF: [1432, 680] };
let currentActive = 'strong';
const collector = {};

const wrapper = Object.create(realEcharts);
wrapper.init = function (dom, theme, opts) {
  const baseId = (dom && dom.id) ? dom.id : 'unknown';
  const tag = baseId === 'chartSubF' ? 'chartSubF_' + currentActive : baseId;
  const [w, h] = DIMS[baseId] || [1000, 600];
  const inst = realEcharts.init(null, null, { renderer: 'svg', ssr: true, width: w, height: h });
  const os = inst.setOption.bind(inst);
  inst.setOption = function (o) {
    // R269 加固：os(o)(真实 echarts.setOption) 与 renderToSVGString 双层 try/catch。
    // 旧代码仅包裹 renderToSVGString，setOption 自身抛错会穿透为未捕获异常使进程崩溃
    // （虽 exit=1，但非干净失败，且第 211-212 行注释声称「setOption 抛错会被包装成 <error>」不实）。
    // 现两层均拦截：任何失败都转 '<error>...' svg 落入 collector，被第 213 行显式判缺图/异常→missing++→硬失败。
    let svg = '<error>setOption-failed';
    try {
      os(o);
      try { svg = inst.renderToSVGString(); } catch (e) { svg = '<error>' + e.message + '</error>'; }
    } catch (e) { svg = '<error>' + e.message + '</error>'; }
    collector[tag] = { opt: o, svg: svg };
    return inst;
  };
  inst.resize = function () {}; inst.dispose = function () {}; inst.on = function () {}; inst.off = function () {}; inst.clear = function () {};
  return inst;
};
global.echarts = wrapper;

// ---------- extract inline script ----------
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf-8');
const scripts = html.match(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g) || [];
let js = '';
for (const s of scripts) { if (s.indexOf('var D = window.FIB_DATA') >= 0) { js = s.replace(/^<script[^>]*>/, '').replace(/<\/script>$/, ''); break; } }
if (!js) { console.error('inline script not found'); process.exit(1); }

const sandbox = {
  window: windowObj, document, echarts: wrapper, console,
  Math, Date, JSON, String, Number, Array, Object, Boolean, isNaN, isFinite,
  parseFloat, parseInt, RegExp, setTimeout: function () {}, navigator: windowObj.navigator
};
vm.createContext(sandbox);

function runOnce(active) {
  currentActive = active;
  windowObj.FIB_DATA.scenarioSwitch.active = active;
  vm.runInContext(js, sandbox, { filename: 'inline.js' });
}

['strong', 'base', 'risk'].forEach(runOnce);

// ---------- SVG text extraction (transform-aware) ----------
function parseTranslate(attrs) {
  const t = attrs.match(/transform="([^"]*)"/);
  if (!t) return { x: 0, y: 0 };
  const mm = t[1].match(/translate\(\s*([-\d.]+)[ ,]+([-\d.]+)\s*\)/);
  if (mm) return { x: parseFloat(mm[1]), y: parseFloat(mm[2]) };
  const mm1 = t[1].match(/translate\(\s*([-\d.]+)\s*\)/);
  if (mm1) return { x: parseFloat(mm1[1]), y: 0 };
  return { x: 0, y: 0 };
}
function resolveTexts(svg) {
  const texts = [];
  const stack = [{ x: 0, y: 0 }];
  let off = { x: 0, y: 0 };
  const re = /<(\/?)(g|text)\b([^>]*)>/g;
  let m;
  while ((m = re.exec(svg))) {
    const closing = m[1] === '/'; const tag = m[2]; const attrs = m[3];
    if (closing) { if (tag === 'g') { stack.pop(); off = stack.length ? stack[stack.length - 1] : { x: 0, y: 0 }; } continue; }
    const tr = parseTranslate(attrs);
    if (tag === 'g') { const no = { x: off.x + tr.x, y: off.y + tr.y }; stack.push(no); off = no; }
    else {
      const xm = attrs.match(/x="([^"]+)"/); const ym = attrs.match(/y="([^"]+)"/);
      const bx = xm ? parseFloat(xm[1]) : 0;
      const by = ym ? parseFloat(ym[1]) : 0;
      const ax = off.x + tr.x + bx, ay = off.y + tr.y + by;
      const after = m.index + m[0].length;
      const endIdx = svg.indexOf('</text>', after);
      const inner = svg.substring(after, endIdx < 0 ? after : endIdx);
      const txt = inner.replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&#\d+;/g, '?').replace(/&#x[0-9a-fA-F]+;/g, '?').trim();
      if (txt) {
        let fs = 11; const fsm = attrs.match(/font-size:\s*([\d.]+)px/) || attrs.match(/font-size="([\d.]+)"/); if (fsm) fs = parseFloat(fsm[1]);
        const anchor = (attrs.match(/text-anchor="([^"]+)"/) || [, 'start'])[1];
        texts.push({ x: ax, y: ay, fs, anchor, text: txt });
      }
    }
  }
  return texts;
}
function charW(ch, fs) { return ch.charCodeAt(0) > 255 ? fs : fs * 0.55; }
function textW(t) { let w = 0; for (const ch of t.text) w += charW(ch, t.fs); return w; }
function box(t) { const w = textW(t), h = t.fs; let x0 = t.anchor === 'middle' ? t.x - w / 2 : (t.anchor === 'end' ? t.x - w : t.x); return { x0, x1: x0 + w, y0: t.y - h * 0.85, y1: t.y + h * 0.15 }; }
function overlap(a, b) { const p = 2; return a.x0 - p < b.x1 + p && b.x0 - p < a.x1 + p && a.y0 - p < b.y1 + p && b.y0 - p < a.y1 + p; }
function isAxisTick(t) {
  const s = t.text;
  if (/^[\d,]+(\.\d+)?%$/.test(s)) return true;
  if (/^[\d,]+(\.\d+)?$/.test(s)) return true;
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return true;
  if (/^\d{4}年/.test(s)) return true;
  if (/^\d+月\d+日$/.test(s)) return true;
  if (/^T\+\d+$/.test(s)) return true; // 竖排时间参考标注（沿时间轴旋转，与价格标注不冲突）
  return false;
}

const NAMES = { chart1: 'chart1_全景图', chart2: 'chart2_艾略特通道', chartSubF_strong: 'chartSubF_strong', chartSubF_base: 'chartSubF_base', chartSubF_risk: 'chartSubF_risk' };

let totalOverlaps = 0;
let missing = 0;  // 缺图/渲染异常计数：任一图未渲染或 setOption 抛错被吞，都必须判失败而非假通过
const KEYS = (process.env.AUDIT_KEYS || 'chart1,chart2,chartSubF_strong,chartSubF_base,chartSubF_risk').split(',');
for (const key of KEYS) {
  const t0 = Date.now();
  const c = collector[key];
  if (!c || !c.svg) { console.error('==== ' + (NAMES[key] || key) + ' : 无渲染结果(缺图) ===='); missing++; continue; }
  // R240 加固回归防护：setOption 抛错会被包装成 '<error>...</error>'，resolveTexts 会解析出 0 个 <text>
  // → 误判 0 重叠假通过。此处显式拦截（任何图渲染失败都应是硬失败，而非静默放行）。
  if (typeof c.svg === 'string' && c.svg.indexOf('<error>') === 0) { console.error('==== ' + (NAMES[key] || key) + ' : 渲染异常(setOption 错误被吞) ===='); missing++; continue; }
  const opt = c.opt || {};
  const legendSet = new Set();
  if (opt.legend && Array.isArray(opt.legend.data)) opt.legend.data.forEach(d => legendSet.add(typeof d === 'string' ? d : (d && d.name)));
  const all = resolveTexts(c.svg).filter(t => !legendSet.has(t.text));
  const ann = all.filter(t => !isAxisTick(t));
  const found = [];
  for (let i = 0; i < ann.length; i++)
    for (let j = i + 1; j < ann.length; j++)
      if (overlap(box(ann[i]), box(ann[j]))) found.push([ann[i], ann[j]]);
  const nm = NAMES[key] || key;
  console.error('==== ' + nm + ' : 标注类文本=' + ann.length + '  重叠=' + found.length + ' ====');
  ann.forEach(t => console.error('   · (' + t.x.toFixed(0) + ',' + t.y.toFixed(0) + ') [' + t.fs + 'px ' + t.anchor + '] ' + t.text));
  found.forEach(p => console.error('   ✗ 重叠: "' + p[0].text + '" @(' + p[0].x.toFixed(0) + ',' + p[0].y.toFixed(0) + ')  <->  "' + p[1].text + '" @(' + p[1].x.toFixed(0) + ',' + p[1].y.toFixed(0) + ')'));
  totalOverlaps += found.length;
  // SVG 快照仅在排障时落盘（AUDIT_DUMP=1），避免每次审计都在仓库里留临时文件
  if (process.env.AUDIT_DUMP === '1') {
    try { fs.writeFileSync(path.join(ROOT, '_dbg_' + key + '.svg'), c.svg); } catch (e) {}
  }
  console.error('   [timing] ' + key + ' 用时 ' + (Date.now() - t0) + 'ms');
}
console.error('\n########## 总重叠数 = ' + totalOverlaps + '  缺图/异常 = ' + missing + ' ##########');
// 显式退出码：0 重叠 且 无缺图/异常 才算通过，可直接串进门禁链
process.exit(totalOverlaps > 0 || missing > 0 ? 1 : 0);
