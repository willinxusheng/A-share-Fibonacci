@echo off
cd /d "C:\Users\Administrator\WorkBuddy\2026-08-04-23-16-18\a-share-fib-wave"
set GIT="C:\Users\Administrator\.workbuddy\binaries\PortableGit\versions\1.2.0\cmd\git.exe"
%GIT% add -A
%GIT% commit -m "chore: auto-update on %date%" >nul 2>&1
REM 推前 ff-only 同步远端，避免远端有变动时 push 被 reject 导致定时任务失败
%GIT% pull --ff-only origin main >> "%TEMP%\ashare_push.log" 2>&1
%GIT% push -u origin main >> "%TEMP%\ashare_push.log" 2>&1
