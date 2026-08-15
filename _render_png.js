'use strict';
// R235: 用 canvas 后端把真实图表渲染成 PNG，让 agent 自己"看"渲染结果验证视觉正确性。
// 复用 _audit_overlap2.js 的 mock DOM harness（global.window/document 先于 require echarts）。
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { createCanvas } = require('canvas');

const ROOT = __dirname;
const DATA_JS = path.join(ROOT, 'data/data.js');
const rawData = fs.readFileSync(DATA_JS, 'utf-8');
const dm = rawData.match(/window\s*\.\s*FIB_DATA\s*=\s*(\{[\s\S]*\});?\s*$/);
if (!dm) { console.error('FIB_DATA parse fail'); process.exit(1); }
const D = JSON.parse(dm[1]);

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
  createElement(tag) { if (tag === 'canvas') return createCanvas(300, 150); return makeEl(); },
  body: makeEl('body'),
  documentElement: { style: {}, clientWidth: 1000, clientHeight: 600, getElementsByTagName: function () { return []; } },
  addEventListener() {}, querySelector() { return null; }
};
const windowObj = { devicePixelRatio: 1, addEventListener() {}, FIB_DATA: D, navigator: { userAgent: 'node' }, innerWidth: 1432 };
windowObj.document = document;
global.window = windowObj;
global.document = document;
const realEcharts = require('echarts');
const ECHARTS_VER = (realEcharts.version || '?');

const DIMS = { chart1: [1432, 900], chart2: [1432, 640], chartSubF: [1432, 680] };
let currentActive = 'strong';
const wrapper = Object.create(realEcharts);
wrapper.init = function (dom, theme, opts) {
  const id = (dom && dom.id) || 'unknown';
  const [w, h] = DIMS[id] || [1000, 600];
  const canvas = createCanvas(w, h);
  // node-canvas 的 Canvas 实例缺 zrender HandlerDomProxy 需要的 DOM 事件桩
  canvas.addEventListener = function () {};
  canvas.removeEventListener = function () {};
  canvas.appendChild = function () {};
  canvas.removeChild = function () {};
  canvas.insertBefore = function () {};
  canvas.style = {};
  canvas.clientWidth = w; canvas.clientHeight = h;
  canvas.parentElement = null;
  canvas.ownerDocument = document;
  const inst = realEcharts.init(canvas, null, { renderer: 'canvas', width: w, height: h });
  const os = inst.setOption.bind(inst);
  inst.setOption = function (o) {
    os(o);
    // canvas 后端绘制走 raf/flush，需在出图前同步 flush，否则 PNG 空白
    try { const zr = inst.getZr(); if (zr && zr.flush) zr.flush(); } catch (e) {}
    const suffix = id === 'chartSubF' ? ('_' + id + '_' + currentActive) : ('_' + id);
    try { fs.writeFileSync(path.join(ROOT, '_png' + suffix + '.png'), canvas.toBuffer('image/png')); }
    catch (e) { console.error('PNG fail', id, e.message); }
    return inst;
  };
  inst.resize = function () {}; inst.dispose = function () {}; inst.on = function () {}; inst.off = function () {}; inst.clear = function () {};
  return inst;
};
global.echarts = wrapper;

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

['strong', 'base', 'risk'].forEach(a => {
  currentActive = a;
  windowObj.FIB_DATA.scenarioSwitch.active = a;
  try { vm.runInContext(js, sandbox, { filename: 'inline.js' }); }
  catch (e) { console.error('run fail active=' + a + ': ' + (e && e.stack ? e.stack : e)); }
});
console.error('== echarts version (node) = ' + ECHARTS_VER + ' ==');
console.error('== PNG render done ==');
