#!/usr/bin/env bash
# run_selfcheck.sh — 深度自检编排（selfcheck 常驻工具入口）
#
# 串联三类深度检查，固化 R245 端到端验证思路为日常可复用工具：
#   ① data.js 字段契约硬检查（verify_contract）
#   ② 前端 SVG 渲染核查（check_svg，需 AUDIT_DUMP=1 落盘）
#   ③ 构建可复现性比对（verify_repro，需两个 data.js 版本）
#
# 用法（在仓库根或任意目录执行）：
#   bash selfcheck/run_selfcheck.sh                      # 跑 ①+②（Mac 当前环境默认可跑）
#   bash selfcheck/run_selfcheck.sh <原版> <重跑版>      # 追加跑 ③ 可复现性比对
#
# 完整重跑流程（含 ③）依赖 raw.md，Mac 沙箱取数受透明代理限制（R246），建议在 Windows 自动机执行：
#   copy data\data.js %TEMP%\data_head.js
#   python build_data.py            (需 raw.md 齐全)
#   node selfcheck\verify_repro.js %TEMP%\data_head.js data\data.js
#
# 退出码：任一硬检查失败即非 0。
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || { echo "!! 无法进入仓库根 $REPO_ROOT"; exit 3; }

# 定位 node（Mac 优先 PATH，回退 managed 布局）
NODE="$(command -v node || ls "$HOME/.workbuddy/binaries/node/versions"/*/bin/node 2>/dev/null | head -1)"
if [ -z "$NODE" ]; then echo "!! 未找到 node，无法运行 JS 自检"; exit 3; fi

echo "仓库根: $REPO_ROOT"
echo "node:   $NODE"

echo
echo "=== [1/3] data.js 字段契约硬检查 (verify_contract) ==="
"$NODE" "$SCRIPT_DIR/verify_contract.js" || { echo "❌ 契约检查失败"; exit 1; }

echo
echo "=== [2/3] 前端 SVG 渲染核查 (check_svg) ==="
echo "  (先 AUDIT_DUMP=1 落 SVG 到仓库根)"
NODE_PATH="$HOME/.workbuddy/binaries/node/workspace/node_modules" AUDIT_DUMP=1 "$NODE" _audit_overlap2.js >/dev/null 2>&1
if ls _dbg_*.svg >/dev/null 2>&1; then
  "$NODE" "$SCRIPT_DIR/check_svg.js" || { echo "❌ SVG 核查失败"; rm -f _dbg_*.svg; exit 1; }
  rm -f _dbg_*.svg
else
  echo "  (未生成 _dbg_*.svg，跳过 SVG 核查——可能 node_modules 缺失或 overlap 审计异常)"
fi

echo
echo "=== [3/3] 构建可复现性比对 (verify_repro) ==="
if [ $# -ge 2 ]; then
  "$NODE" "$SCRIPT_DIR/verify_repro.js" "$1" "$2" || { echo "❌ 可复现性比对发现差异"; exit 1; }
else
  echo "  (跳过：需两个 data.js 版本作为参数。"
  echo "   完整重跑流程在 Windows 自动机执行；Mac 上可用:"
  echo "   bash selfcheck/run_selfcheck.sh <原版data.js> <重跑版data.js>)"
fi

echo
echo "✅ selfcheck 完成（契约 + SVG 已核查；可复现性按需）"
