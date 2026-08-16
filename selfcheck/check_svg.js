#!/usr/bin/env node
// check_svg.js — 前端 SVG 渲染产物核查（selfcheck 常驻工具）
//
// 用途：扫描指定目录下所有 _dbg_*.svg（由 _audit_overlap2.js 设 AUDIT_DUMP=1 落盘），
//       逐项检查：① 文件非空（size>3000B）；② 含 <text>（标签）与 <path>/<polygon>/<circle>
//       （图形）；③ 已在最终画布坐标系内的坐标（x/y/translate）在各自 viewBox 内（容差 30px），
//       捕获「标签画到画布外」异常。注：path d=/polygon points= 坐标受父级 <g transform> 影响，
//       不叠加变换直接抽数会误报（实测 -1295/1290 假阳性），故不解析；真·坐标/重叠判定由
//       audit52 + _audit_overlap2.js（echarts 真实布局）负责，本工具仅冗余二级 sanity check。
//       画布外」异常。对应 R245 SVG 越界核对思路（修正了初版固定阈值 1200 误报，改为以各 SVG
//       viewBox 为动态边界）。
// 依赖：Node.js（纯 fs + 正则，无第三方包）。
// 用法：
//   node check_svg.js [dir]      # dir 默认 process.cwd()
//   例：AUDIT_DUMP=1 node _audit_overlap2.js   # 先落 SVG 到仓库根
//        node check_svg.js                       # 再核查
// 退出码：0=全部真实渲染；1=存在渲染异常；2=未找到 _dbg_*.svg。
'use strict';
const fs = require('fs');
const path = require('path');

const dir = process.argv[2] || process.cwd();
let files;
try {
  files = fs.readdirSync(dir).filter(f => /^_dbg_.*\.svg$/i.test(f)).sort();
} catch (e) {
  console.log('⚠️ 无法读取目录:', dir, e.message);
  process.exit(2);
}

if (files.length === 0) {
  console.log('⚠️ 未找到 _dbg_*.svg（dump 未生效？先在仓库根跑 AUDIT_DUMP=1 node _audit_overlap2.js）');
  process.exit(2);
}

// 越界以每个 SVG 自身的 viewBox 为边界（容差 30px），捕获"标签画到画布外"异常
let allOk = true;

for (const f of files) {
  const raw = fs.readFileSync(path.join(dir, f), 'utf8');
  const size = raw.length;
  const nText = (raw.match(/<text[\s>]/g) || []).length;
  const nPath = (raw.match(/<path[\s>]/g) || []).length;
  const nPoly = (raw.match(/<polygon[\s>]/g) || []).length;
  const nCircle = (raw.match(/<circle[\s>]/g) || []).length;
  const vbM = raw.match(/viewBox="\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s*"/);
  const vbX = vbM ? parseFloat(vbM[1]) : 0;
  const vbY = vbM ? parseFloat(vbM[2]) : 0;
  const vbW = vbM ? parseFloat(vbM[3]) : 1000;
  const vbH = vbM ? parseFloat(vbM[4]) : 600;
  const vb = vbM ? `${vbM[1]} ${vbM[2]} ${vbM[3]} ${vbM[4]}` : '(none)';

  // 提取已在「最终画布坐标系」内的坐标源：x=/y= 属性 + translate() 平移量。
  // 刻意不解析 <path d=> / <polygon points=> —— 其坐标在局部坐标系、受父级 <g transform>
  // 影响，不叠加变换直接抽数会误把 clipPath/装饰路径的局部坐标当越界（实测抽到 -1295/1290
  // 假阳性）。真·坐标/重叠判定由 audit52 + _audit_overlap2.js（echarts 真实布局换算）负责，
  // 本工具仅作冗余二级 sanity check。若有实际渲染内容必含 translate 组，coordsFound 必为真。
  let maxX = -1e9, maxY = -1e9, minX = 1e9, minY = 1e9;
  let coordsFound = false;
  const consider = (x, y) => {
    if (typeof x === 'number' && !isNaN(x)) { coordsFound = true; maxX = Math.max(maxX, x); minX = Math.min(minX, x); }
    if (typeof y === 'number' && !isNaN(y)) { coordsFound = true; maxY = Math.max(maxY, y); minY = Math.min(minY, y); }
  };
  let m;
  const coordRe = /(?:x="(-?\d+(?:\.\d+)?)"|y="(-?\d+(?:\.\d+)?)"|translate\(\s*(-?\d+(?:\.\d+)?)[ ,]+(-?\d+(?:\.\d+)?)\s*\))/g;
  while ((m = coordRe.exec(raw))) {
    const xs = [m[1], m[3]].filter(v => v !== undefined).map(Number);
    const ys = [m[2], m[4]].filter(v => v !== undefined).map(Number);
    xs.forEach(x => consider(x, undefined));
    ys.forEach(y => consider(undefined, y));
  }
  const outOfBounds = coordsFound && (maxX > vbX + vbW + 30 || maxY > vbY + vbH + 30 || minX < vbX - 30 || minY < vbY - 30);
  const ok = size > 3000 && nText > 0 && (nPath + nPoly + nCircle) > 0 && !outOfBounds;
  if (!ok) allOk = false;

  console.log(`\n[${f}]`);
  console.log(`  size=${size}B viewBox=${vb}`);
  console.log(`  <text>=${nText} <path>=${nPath} <polygon>=${nPoly} <circle>=${nCircle}`);
  console.log(`  坐标范围 x:[${minX},${maxX}] y:[${minY},${maxY}] ${outOfBounds ? '⚠️越界' : '✅在画布内'}`);
  console.log(`  ${ok ? '✅ 渲染真实(非空、有标签、有图形)' : '❌ 渲染异常'}`);
}

console.log('\n========================================');
console.log(allOk ? '✅ 全部图表渲染产物真实、坐标在画布内、无空图/画外' : '⚠️ 存在渲染异常图表');
process.exit(allOk ? 0 : 1);
