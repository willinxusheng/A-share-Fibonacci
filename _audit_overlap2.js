'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const realEcharts = require('echarts');
const { createCanvas } = require('canvas');

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
  addEventListener() {}, querySelector() { return null; }
};

const windowObj = { devicePixelRatio: 1, addEventListener() {}, FIB_DATA: D, navigator: { userAgent: 'node' } };

// ---------- 移动端验证开关（AUDIT_MOBILE=1）：用手机宽度渲染，验证 R211 响应式滚动条不重叠 ----------
const MOB = process.env.AUDIT_MOBILE === '1';
windowObj.innerWidth = MOB ? 375 : 1432;  // 让 index.html 的 IS_MOB() 命中对应分支

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
    os(o);
    let svg = '';
    try { svg = inst.renderToSVGString(); } catch (e) { svg = '<error>' + e.message + '</error>'; }
    collector[tag] = { opt: o, svg: svg };
    return inst;
  };
  inst.resize = function () {}; inst.dispose = function () {}; inst.on = function () {}; inst.off = function () {}; inst.clear = function () {};
  return inst;
};
global.echarts = wrapper;
global.document = document;
global.window = windowObj;

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
const KEYS = (process.env.AUDIT_KEYS || 'chart1,chart2,chartSubF_strong,chartSubF_base,chartSubF_risk').split(',');
for (const key of KEYS) {
  const t0 = Date.now();
  const c = collector[key];
  if (!c || !c.svg) { console.error('==== ' + (NAMES[key] || key) + ' : 无渲染结果 ===='); continue; }
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
console.error('\n########## 总重叠数 = ' + totalOverlaps + ' ##########');
// 显式退出码：0 重叠才算通过，可直接串进门禁链
process.exit(totalOverlaps > 0 ? 1 : 0);
