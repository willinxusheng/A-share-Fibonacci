#!/usr/bin/env bash
# Mac 版自动推送：等同 Windows push_silent.bat（SSH，免密码/令牌）
# 由 launchd 定时调用（见 com.user.ashare.autopush.plist），也可手动执行。
set -u

# launchd 默认 PATH 极简，未必含 brew 安装的 git/ssh；扩充后保证可找到
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# launchd 非交互环境下 SSH key 往往未加载到 ssh-agent（GUI 登录的 keychain 与 launchd agent 不同），
# 不预加载则 git push 会因找不到 key 静默失败。两条命令兼容新旧 macOS 的 ssh-add 参数。
ssh-add --apple-use-keychain ~/.ssh/id_ed25519 2>/dev/null || ssh-add ~/.ssh/id_ed25519 2>/dev/null || true

# 自动定位脚本所在目录即仓库根（无需写死路径）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

LOG="/tmp/ashare_push.log"

# 只同步本脚本职责范围内的 data/（与云端 daily.yml 的 git add data/ 同口径），
# 避免 git add -A 把诊断残留(_dbg_*.svg/_probe_*.js 等)误提交，造成双写污染与无谓 merge 冲突
git add data/
# 无改动时 commit 会失败，忽略（不影响后续 push）
git commit -m "chore: auto-update data" >/dev/null 2>&1 || true
git pull --ff-only origin main >> "$LOG" 2>&1 || true
# push 失败要暴露：launchd 退出码非0会在 err 日志留痕，便于排查
git push -u origin main >> "$LOG" 2>&1
exit $?
