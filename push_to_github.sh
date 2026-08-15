#!/usr/bin/env bash
# Mac 版自动推送：等同 Windows push_silent.bat（SSH，免密码/令牌）
# 由 launchd 定时调用（见 com.user.ashare.autopush.plist），也可手动执行。
set -u

# launchd 默认 PATH 极简，未必含 brew 安装的 git/ssh；扩充后保证可找到
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# 自动定位脚本所在目录即仓库根（无需写死路径）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

LOG="/tmp/ashare_push.log"

git add -A
# 无改动时 commit 会失败，忽略（不影响后续 push）
git commit -m "chore: auto-update" >/dev/null 2>&1 || true
git pull --ff-only origin main >> "$LOG" 2>&1 || true
# push 失败要暴露：launchd 退出码非0会在 err 日志留痕，便于排查
git push -u origin main >> "$LOG" 2>&1
exit $?
