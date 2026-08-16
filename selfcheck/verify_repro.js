#!/usr/bin/env node
// verify_repro.js — 构建可复现性深度比对（selfcheck 常驻工具）
//
// 用途：比对「原版 data.js」（如 git HEAD 版备份）与「重跑版 data.js」（重跑 build_data 生成）
//       的数值一致性，逐字段递归比对，排除时间戳类字段白名单，验证引擎确定性、无隐藏时间依赖
//       （_sf_exp 用 last_date 基准、_horizon_for 由历史摆动腿独立派生等）。
//       对应 R245 端到端验证思路，固化进仓库以便日常复用。
// 依赖：Node.js（纯 fs + 正则 + JSON，无第三方包）。
// 用法：
//   node verify_repro.js <原版data.js> <重跑版data.js>
//   例：node verify_repro.js /tmp/data_head.js data/data.js
// 退出码：0=完全一致；1=存在数值差异；2=参数/解析错误。
// 说明：本工具只做比对，需调用方先准备好两个版本的 data.js。重跑需 raw.md——Mac 沙箱取数受
//       透明代理限制（见 R246），完整重跑流程在 Windows 自动机执行；Mac 上可改用「git stash 前
//       的 data.js 备份」与「当前 data.js」比对（前提：两次取数水源一致）。
'use strict';
const fs = require('fs');

const orig = process.argv[2];
const rerun = process.argv[3];
if (!orig || !rerun) {
  console.log('用法: node verify_repro.js <原版data.js> <重跑版data.js>');
  process.exit(2);
}

function load(fp) {
  const raw = fs.readFileSync(fp, 'utf8');
  const m = raw.match(/window\s*\.\s*FIB_DATA\s*=\s*(\{[\s\S]*\});?\s*$/);
  if (!m) { console.log('PARSE FAIL', fp); process.exit(2); }
  return JSON.parse(m[1]);
}

let A, B;
try { A = load(orig); } catch (e) { console.log('读取原版失败:', orig, e.message); process.exit(2); }
try { B = load(rerun); } catch (e) { console.log('读取重跑版失败:', rerun, e.message); process.exit(2); }

// 时间戳类字段白名单（取数时间/构建时间随运行变化，非数值漂移）：
// updated / _buildAt / buildAt / generatedAt / updatedAt / fetchedAt / buildAtISO
const SKIP_KEYS = new Set([
  'updated', '_buildAt', 'buildAt', 'generatedAt', 'updatedAt', 'fetchedAt', 'buildAtISO'
]);

let diffs = 0;
function walk(path, a, b) {
  const key = path.split('.').pop();
  if (SKIP_KEYS.has(key)) return; // 时间戳类白名单跳过
  if (typeof a !== typeof b) {
    console.log(`TYPE  ${path}: ${typeof a}(${JSON.stringify(a).slice(0, 40)}) vs ${typeof b}(${JSON.stringify(b).slice(0, 40)})`);
    diffs++; return;
  }
  if (a === null || typeof a !== 'object') {
    if (a !== b && !(Number.isNaN(a) && Number.isNaN(b))) {
      console.log(`VAL   ${path}: ${JSON.stringify(a)} -> ${JSON.stringify(b)}`);
      diffs++;
    }
    return;
  }
  if (Array.isArray(a) !== Array.isArray(b)) {
    console.log(`ARR   ${path}: array/object mismatch`); diffs++; return;
  }
  const keys = new Set([...Object.keys(a || {}), ...Object.keys(b || {})]);
  for (const k of keys) {
    if (SKIP_KEYS.has(k)) continue;
    if (!(k in a)) { console.log(`ADD   ${path}.${k}: ${JSON.stringify(b[k]).slice(0, 40)}`); diffs++; continue; }
    if (!(k in b)) { console.log(`DEL   ${path}.${k}: ${JSON.stringify(a[k]).slice(0, 40)}`); diffs++; continue; }
    walk(`${path}.${k}`, a[k], b[k]);
  }
}

walk('FIB_DATA', A, B);

console.log('========================================');
console.log(`顶层 key 数 A=${Object.keys(A).length} B=${Object.keys(B).length}`);
console.log(`实质差异数 (已跳过时间戳白名单): ${diffs}`);
console.log(diffs === 0 ? '✅ 构建可复现：重跑版与原版数值完全一致' : '⚠️ 存在数值差异，需核查');
process.exit(diffs === 0 ? 0 : 1);
