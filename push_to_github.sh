#!/usr/bin/env bash
# Mac 版自动推送：等同 Windows push_silent.bat（SSH，免密码/令牌）
# 由 launchd 定时调用（见 com.user.ashare.autopush.plist），也可手动执行。
set -u

# 自动定位脚本所在目录即仓库根（无需写死路径）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

LOG="/tmp/ashare_push.log"

git add -A
git commit -m "chore: auto-update" >/dev/null 2>&1
git pull --ff-only origin main >> "$LOG" 2>&1
git push -u origin main >> "$LOG" 2>&1

# 退出码取最后一次 push 的结果（0=成功）
exit 0
