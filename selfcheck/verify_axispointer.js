// R156：真浏览器验证所有图表 axisPointer label 日期显示是否正常
// 用法：NODE_PATH=.../node_modules node selfcheck/verify_axispointer.js
const { chromium } = require('playwright');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const PORT = 8138;
const HOST = '127.0.0.1';
const URL = `http://${HOST}:${PORT}/index.html?v=${Date.now()}`;

const SHOTS = [
  { id: 'chart1', name: 'chart1' },
  { id: 'chart2', name: 'chart2' },
  { id: 'chartSubF', name: 'subF' },
  { id: 'sentChart', name: 'sent' },
  { id: 'sentVsChart', name: 'sentVs' }
];

(async () => {
  // 1) 启动静态文件服务器
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
    headless: false
  });
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await context.newPage();

  try {
    await page.goto(URL, { waitUntil: 'networkidle' });
    // 等待 chart1 出现，说明 echarts 已初始化
    await page.waitForSelector('#chart1 canvas', { timeout: 30000 });
    // 再等 2s 让所有图表 resize/渲染完成
    await page.waitForTimeout(2000);

    for (const s of SHOTS) {
      const el = await page.locator(`#${s.id}`).first();
      if (!el) { console.log(`SKIP ${s.id}: not found`); continue; }
      await el.scrollIntoViewIfNeeded();
      await page.waitForTimeout(500);
      const box = await el.boundingBox();
      if (!box) { console.log(`SKIP ${s.id}: no box`); continue; }
      // 用 ECharts dispatchAction 强制触发 tooltip/axisPointer，比 mousemove 更稳
      const cx = Math.round(box.width * 0.55);
      const cy = Math.round(box.height * 0.35);
      await page.evaluate(({ id, cx, cy }) => {
        var el = document.getElementById(id);
        if (!el || !window.echarts) return;
        var chart = echarts.getInstanceByDom(el);
        if (!chart) return;
        chart.dispatchAction({ type: 'showTip', x: cx, y: cy });
      }, { id: s.id, cx, cy });
      await page.waitForTimeout(1200);
      const shotPath = path.join(ROOT, `selfcheck/verify_axispointer_${s.name}.png`);
      await page.screenshot({ path: shotPath, fullPage: false });
      console.log(`SHOT ${s.id} -> ${shotPath}`);
    }

    // 最后读取页面 console 日志，看是否有 formatter 抛错
    const logs = await page.evaluate(() => {
      return (window.__errLogs || []);
    });
    if (logs && logs.length) console.log('ERRORS:', logs);
  } catch (e) {
    console.error('FAILED:', e.message);
  } finally {
    await browser.close();
    server.close();
  }
})();
