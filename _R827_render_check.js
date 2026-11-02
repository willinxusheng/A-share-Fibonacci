'use strict';
/**
 * R827 前端渲染实测：验证 targetTable / triggerCallout 的「预期到达」「历史可达性」列
 * 真的渲染出数据，且在 reach 缺失时能优雅降级（不出现 undefined / NaN）。
 *
 * 做法：mock DOM + echarts(空实现) → 在 vm 里真跑 index.html 主内联脚本 →
 * 检查被写入的 innerHTML 字符串。比截图快且可断言。
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = __dirname;
const raw = fs.readFileSync(path.join(ROOT, 'data/data.js'), 'utf-8');
const dm = raw.match(/window\s*\.\s*FIB_DATA\s*=\s*(\{[\s\S]*\});?\s*$/);
if (!dm) { console.error('FIB_DATA parse fail'); process.exit(1); }
const D0 = JSON.parse(dm[1]);

// ---------- mock DOM ----------
function makeEl(id) {
  const el = {
    id: id || ('el_' + Math.random().toString(36).slice(2)),
    style: {}, className: '', value: '', children: [], clientWidth: 1000, clientHeight: 600,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild(c) { this.children.push(c); return c; },
    removeChild() {}, insertBefore(c) { this.children.unshift(c); return c; },
    addEventListener() {}, removeEventListener() {},
    getAttribute() { return null; }, setAttribute() {},
    getContext() { return ctx(); },
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
function ctx() {
  return {
    font: '12px sans-serif', fillStyle: '', strokeStyle: '',
    measureText(t) {
      let fs = 12; const m = /(\d+(?:\.\d+)?)px/.exec(this.font || ''); if (m) fs = parseFloat(m[1]);
      let w = 0; for (const ch of String(t)) w += (ch.charCodeAt(0) > 255 ? fs : fs * 0.55);
      return { width: w };
    },
    fillRect() {}, clearRect() {}, fillText() {}, strokeText() {},
    beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, fill() {},
    save() {}, restore() {}, setTransform() {}, scale() {}, translate() {},
    arc() {}, rect() {}, closePath() {}, clip() {},
    createLinearGradient() { return { addColorStop() {} }; }
  };
}
const _els = {};
const documentMock = {
  getElementById(id) { if (!_els[id]) _els[id] = makeEl(id); return _els[id]; },
  createElement(tag) { return tag === 'canvas' ? { getContext() { return ctx(); } } : makeEl(); },
  body: makeEl('body'),
  documentElement: { style: {}, clientWidth: 1000, clientHeight: 600, getElementsByTagName() { return []; } },
  addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
  getElementsByClassName() { return []; }, getElementsByTagName() { return []; }
};

const ecStub = {
  init() {
    return {
      setOption() {}, resize() {}, dispose() {}, on() {}, off() {}, clear() {},
      getOption() { return {}; }, getWidth() { return 1000; }, getHeight() { return 600; },
      convertToPixel() { return [0, 0]; }, dispatchAction() {}, showLoading() {}, hideLoading() {}
    };
  },
  graphic: { LinearGradient: function () { return {}; } },
  registerTheme() {}, registerLocale() {}, getInstanceByDom() { return null; }
};

const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf-8');
const scripts = html.match(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g) || [];
let js = '';
for (const s of scripts) {
  if (s.indexOf('var D = window.FIB_DATA') >= 0) {
    js = s.replace(/^<script[^>]*>/, '').replace(/<\/script>$/, '');
    break;
  }
}
if (!js) { console.error('主内联脚本未找到'); process.exit(1); }

function runCase(label, mutate) {
  Object.keys(_els).forEach(k => delete _els[k]);
  const D = JSON.parse(JSON.stringify(D0));
  if (mutate) mutate(D);
  const windowObj = { devicePixelRatio: 1, innerWidth: 1432, addEventListener() {}, FIB_DATA: D, navigator: { userAgent: 'node' } };
  windowObj.document = documentMock;
  const sandbox = {
    window: windowObj, document: documentMock, echarts: ecStub, console,
    Math, Date, JSON, String, Number, Array, Object, Boolean, isNaN, isFinite,
    parseFloat, parseInt, RegExp, setTimeout() {}, clearTimeout() {}, navigator: windowObj.navigator
  };
  vm.createContext(sandbox);
  try {
    vm.runInContext(js, sandbox, { filename: 'inline.js' });
  } catch (e) {
    return { label, err: e.message };
  }
  return {
    label,
    targetTable: (_els['targetTable'] || {}).innerHTML || '',
    triggerCallout: (_els['triggerCallout'] || {}).innerHTML || '',
    D
  };
}

let fail = 0;
function chk(cond, name, detail) {
  if (cond) { console.log('  [PASS] ' + name + (detail ? '  ' + detail : '')); }
  else { fail++; console.log('  [FAIL] ' + name + (detail ? '  ' + detail : '')); }
}

console.log('='.repeat(78));
console.log('R827 前端渲染实测');
console.log('='.repeat(78));

// ---------- 场景1：正常数据 ----------
const r1 = runCase('normal');
if (r1.err) { console.log('  [FAIL] 主脚本执行异常: ' + r1.err); process.exit(1); }

const tt = r1.targetTable;
chk(tt.length > 0, 'targetTable 已渲染', '长度=' + tt.length);
chk(/预期到达/.test(tt), 'targetTable 含「预期到达」列');
chk(/历史可达性/.test(tt), 'targetTable 含「历史可达性」列');
const expMatches = tt.match(/(\d+)\s*日<br><span[^>]*>约 \d{4}-\d{2}-\d{2}/g) || [];
chk(expMatches.length === r1.D.targets.length,
  '每个目标都有「N 日 + 约 YYYY-MM-DD」', '命中 ' + expMatches.length + '/' + r1.D.targets.length);
const probMatches = tt.match(/<b style="color:#[0-9a-f]{6}">(\d+\.\d{2})%<\/b>/g) || [];
chk(probMatches.length === r1.D.targets.length,
  '每个目标都有可达性百分比', '命中 ' + probMatches.length + '/' + r1.D.targets.length);
chk(!/undefined|NaN|null%/.test(tt), 'targetTable 无 undefined/NaN');

const tc = r1.triggerCallout;
chk(/历史可达/.test(tc), 'triggerCallout 含「历史可达」');
const tcN = (tc.match(/历史可达/g) || []).length;
chk(tcN === r1.D.tradePlan.sellTargets.length,
  '每档触发带都有历史可达', tcN + '/' + r1.D.tradePlan.sellTargets.length);
chk(!/undefined|NaN/.test(tc), 'triggerCallout 无 undefined/NaN');

// 交叉核对：前端展示的百分比必须等于数据里的 reach.prob（不得前端二次计算）
let crossOK = 0;
r1.D.targets.forEach(t => {
  if (!t.reach || t.reach.prob == null) return;
  const s = t.reach.prob.toFixed(2) + '%';
  if (tt.indexOf(s) >= 0) crossOK++;
});
chk(crossOK === r1.D.targets.length,
  '前端百分比 == 数据 reach.prob', crossOK + '/' + r1.D.targets.length);

// ---------- 场景2：reach 缺失（降级） ----------
const r2 = runCase('no-reach', D => {
  D.targets.forEach(t => { delete t.reach; delete t.expDays; delete t.expDate; });
  (D.tradePlan.sellTargets || []).forEach(t => { delete t.reach; });
});
if (r2.err) {
  chk(false, 'reach 缺失时不抛异常', r2.err);
} else {
  chk(true, 'reach 缺失时不抛异常');
  chk(!/undefined|NaN/.test(r2.targetTable), '降级后 targetTable 无 undefined/NaN');
  chk(!/undefined|NaN/.test(r2.triggerCallout), '降级后 triggerCallout 无 undefined/NaN');
}

console.log('-'.repeat(78));
console.log(fail === 0 ? '渲染实测: 全部通过' : ('渲染实测: ' + fail + ' 项失败'));
process.exit(fail === 0 ? 0 : 1);
