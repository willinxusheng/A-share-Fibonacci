// R156/R280：真浏览器验证所有图表 axisPointer label 日期显示是否正常
// 增强点（R280）：
// - 用 Playwright route 把 CDN echarts 重定向到本地 _verify_assets/echarts.min.js，
//   避免沙箱出网不稳导致图表加载失败而误判。
// - headless 模式，适配无显示环境。
// - 捕获 pageerror + console error/warning（formatter 抛错反假绿）。
// - tooltip 文本断言：必须含日期且无 NaN/undefined。
const { chromium } = require('playwright');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const PORT = 8138;
const HOST = '127.0.0.1';
const ECHARTS_LOCAL = path.join(ROOT, '_verify_assets', 'echarts.min.js');
const CDN = 'https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js';

const SHOTS = [
  { id: 'chart1', name: 'chart1' },
  { id: 'chart2', name: 'chart2' },
  { id: 'chartSubF', name: 'subF' },
  { id: 'sentChart', name: 'sent' },
  { id: 'sentVsChart', name: 'sentVs' }
];

const jsErrors = [];
const consoleErrors = [];

(async () => {
  const server = http.createServer((req, res) => {
    const file = path.join(ROOT, req.url.split('?')[0]);
    const safe = file.startsWith(ROOT + path.sep);
    if (!safe || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404); res.end('Not found'); return;
    }
    const ext = path.extname(file);
    const type = { '.html': 'text/html', '.js': 'application/javascript', '.json': 'application/json', '.css': 'text/css' }[ext] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': type });
    fs.createReadStream(file).pipe(res);
  });
  await new Promise((resolve) => server.listen(PORT, HOST, resolve));

  const browser = await chromium.launch({
    executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true
  });
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await context.newPage();
  page.on('pageerror', (e) => jsErrors.push(String(e.message || e)));
  page.on('console', (msg) => {
    if (msg.type() === 'error' || msg.type() === 'warning') consoleErrors.push(`[${msg.type()}] ${msg.text()}`);
  });

  try {
    // 本地存在 echarts 副本时拦截 CDN（沙箱出网不稳）；真机直接走 CDN。
    if (fs.existsSync(ECHARTS_LOCAL)) {
      await page.route(CDN, (route) => {
        route.fulfill({ status: 200, contentType: 'application/javascript', body: fs.readFileSync(ECHARTS_LOCAL) });
      });
    }

    await page.goto(`http://${HOST}:${PORT}/index.html`, { waitUntil: 'load' });
    await page.waitForSelector('#chart1 canvas', { timeout: 30000 });
    await page.waitForTimeout(2500);

    for (const s of SHOTS) {
      const el = page.locator(`#${s.id}`).first();
      const box = await el.boundingBox();
      if (!box) { console.log(`SKIP ${s.id}: no box`); continue; }
      await el.scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);
      const cx = Math.round(box.width * 0.55);
      const cy = Math.round(box.height * 0.5);
      await page.evaluate(({ id, cx, cy }) => {
        const el = document.getElementById(id);
        if (!el || !window.echarts) return;
        const chart = echarts.getInstanceByDom(el);
        if (!chart) return;
        chart.dispatchAction({ type: 'showTip', x: cx, y: cy });
      }, { id: s.id, cx, cy });
      await page.waitForTimeout(900);

      const tip = await page.evaluate((id) => {
        const el = document.getElementById(id);
        const all = el.querySelectorAll('div');
        let best = '';
        for (const a of all) {
          const t = (a.innerText || a.textContent || '').trim();
          if (/\d{4}-\d{2}-\d{2}/.test(t) && t.length > best.length) best = t;
        }
        return best;
      }, s.id);

      const hasDate = /\d{4}-\d{2}-\d{2}/.test(tip);
      const hasNaN = /NaN|undefined/.test(tip);
      const ok = tip && hasDate && !hasNaN;
      console.log(`[${s.id}] tooltip_len=${tip.length} hasDate=${hasDate} hasNaN=${hasNaN} => ${ok ? 'PASS' : 'FAIL'}`);
      if (tip) console.log(`   tooltip="${tip.replace(/\s+/g, ' ').slice(0, 160)}"`);

      const shotPath = path.join(ROOT, `selfcheck/verify_axispointer_${s.name}.png`);
      await page.screenshot({ path: shotPath });
      console.log(`   shot -> ${shotPath}`);
    }

    console.log('\n=== JS_ERRORS (' + jsErrors.length + ') ===');
    jsErrors.forEach((e) => console.log('  ' + e));
    console.log('=== CONSOLE_ERRORS (' + consoleErrors.length + ') ===');
    consoleErrors.slice(0, 20).forEach((e) => console.log('  ' + e));
    process.exit((jsErrors.length || consoleErrors.length) ? 1 : 0);
  } catch (e) {
    console.error('FAILED:', e.message);
    process.exit(1);
  } finally {
    await browser.close();
    server.close();
  }
})();
