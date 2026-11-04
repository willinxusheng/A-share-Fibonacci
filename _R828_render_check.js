// R828 前端渲染实测：真跑内联脚本（vm + mock DOM）
// ① 回测脚注新披露是否真的渲染出来（9 项）
// ② 铁律线注入必须是【全局替换】：注入"同一元素出现两次 + KL 不同"的场景，
//    若实现是单次 replace（旧代码），第二处会残留旧值 → 本项 FAIL。
const fs = require('fs');
const vm = require('vm');

function ctx2d() {
  return {
    font: '12px x', measureText(t) { return { width: String(t).length * 11 }; },
    fillRect() {}, clearRect() {}, fillText() {}, strokeText() {}, beginPath() {},
    moveTo() {}, lineTo() {}, stroke() {}, fill() {}, save() {}, restore() {},
    setTransform() {}, scale() {}, translate() {}, arc() {}, rect() {}, closePath() {},
    clip() {}, createLinearGradient() { return { addColorStop() {} }; }
  };
}
function mk(id) {
  const e = {
    id, style: {}, className: '', value: '', children: [], dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    appendChild(c) { this.children.push(c); return c; }, removeChild() {},
    insertBefore(c) { this.children.unshift(c); return c; },
    addEventListener() {}, removeEventListener() {},
    getAttribute() { return null; }, setAttribute() {}, removeAttribute() {},
    getContext() { return ctx2d(); },
    getBoundingClientRect() { return { width: 1000, height: 600, left: 0, top: 0 }; },
    offsetWidth: 1000, offsetHeight: 600,
    querySelector() { return null; }, querySelectorAll() { return []; },
    focus() {}, blur() {}, scrollIntoView() {}
  };
  e.parentNode = { insertBefore() {}, removeChild() {}, appendChild() {} };
  let t = '', h = '';
  Object.defineProperty(e, 'textContent', { get() { return t; }, set(v) { t = String(v); } });
  Object.defineProperty(e, 'innerHTML', { get() { return h; }, set(v) { h = String(v); } });
  return e;
}

function render(htmlText, D) {
  const els = {};
  const doc = {
    getElementById(id) { if (!els[id]) els[id] = mk(id); return els[id]; },
    createElement(t) { return t === 'canvas' ? { getContext() { return ctx2d(); } } : mk(); },
    body: mk('body'),
    documentElement: { style: {}, clientWidth: 1000, clientHeight: 600, getElementsByTagName() { return []; } },
    addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    getElementsByClassName() { return []; }, getElementsByTagName() { return []; }
  };
  const ec = {
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
  const scripts = htmlText.match(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g);
  let js = '';
  for (const s of scripts) {
    if (s.indexOf('var D = window.FIB_DATA') >= 0) {
      js = s.replace(/^<script[^>]*>/, '').replace(/<\/script>$/, '');
      break;
    }
  }
  const w = { devicePixelRatio: 1, innerWidth: 1432, addEventListener() {}, FIB_DATA: D, navigator: { userAgent: 'node' } };
  w.document = doc;
  const sb = {
    window: w, document: doc, echarts: ec, console, Math, Date, JSON, String, Number, Array,
    Object, Boolean, isNaN, isFinite, parseFloat, parseInt, RegExp,
    setTimeout() {}, clearTimeout() {}, navigator: w.navigator
  };
  vm.createContext(sb);
  vm.runInContext(js, sb, { filename: 'inline.js' });
  return els;
}

const raw = fs.readFileSync('data/data.js', 'utf8');
const BASE_D = JSON.parse(raw.match(/window\s*\.\s*FIB_DATA\s*=\s*(\{[\s\S]*\});?\s*$/)[1]);
const html = fs.readFileSync('index.html', 'utf8');
const strip = s => s.replace(/<br\s*\/?>/g, '\n').replace(/<[^>]+>/g, '')
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/[ \t]+/g, ' ').trim();

let bad = 0;
const results = [];
function check(name, ok) {
  results.push([name, ok]);
  if (!ok) bad++;
}

// ---------- ① 回测脚注披露 ----------
const els = render(html, BASE_D);
const foot = strip(els['btFoot'] ? els['btFoot'].innerHTML : '');
const bt = BASE_D.backtest;
check('脚注含「同批配对」', /同批配对/.test(foot));
check('脚注含配对基线数值 ' + bt.dirPairedBase + '%', foot.indexOf('配对基线 ' + bt.dirPairedBase + '%') >= 0);
check('脚注含优势 ' + bt.dirPairedEdge + 'pp', foot.indexOf(bt.dirPairedEdge + 'pp') >= 0);
check('脚注含「不优于随机」结论', /不优于随机/.test(foot));
check('脚注含观察窗口径修正说明', /按引擎承诺的 expDays/.test(foot));
check('脚注已删除倒因为果表述「那是巧合，不是结论」', foot.indexOf('那是巧合，不是结论') < 0);
check('脚注已删除「不是判据的问题」（R827 过度归因）', foot.indexOf('不是判据的问题') < 0);
check('脚注含 side 拆分（卖出）', /卖出 \d+\/\d+/.test(foot));
check('脚注含可达性分档', /可达/.test(foot));

// ---------- ② 铁律线注入必须全局替换 ----------
// 注入：在 noteChan 里制造两处 3674.40（现状每元素仅一处，单次 replace 也能过；
// 必须造出第二处才能区分"单次 replace"与"全局替换"）。
const OLD_KL = 3674.40;
const NEW_KL = 3675.00;
const D2 = JSON.parse(JSON.stringify(BASE_D));
D2.tradePlan.stopLine.price = NEW_KL;
const html2 = html.replace(
  /(<div class="note" id="noteChan">)([\s\S]*?)(<\/div>)/,
  (m, a, body, c) => a + body + ' 铁律参考 ' + OLD_KL.toFixed(2) + c
);
const injected = html2 !== html;
check('注入制造了第二处 3674.40（用例自证）',
  injected && (html2.match(/3674\.40/g) || []).length > (html.match(/3674\.40/g) || []).length);

if (injected) {
  const els2 = render(html2, D2);
  let left = 0;
  ['noteChan', 'noteP8', 'discFoot'].forEach(id => {
    const t = strip(els2[id] ? els2[id].innerHTML : '');
    left += (t.match(/3674\.40/g) || []).length;
  });
  check('KL 改为 ' + NEW_KL + ' 后无旧值残留（全局替换生效）', left === 0);
}

for (const [n, ok] of results) console.log('  [' + (ok ? 'OK  ' : 'FAIL') + '] ' + n);
console.log('\n--- btFoot 渲染文本（节选）---');
console.log(foot.slice(0, 1800));
console.log('\n结果: ' + (results.length - bad) + '/' + results.length + ' 通过');
process.exit(bad ? 1 : 0);
