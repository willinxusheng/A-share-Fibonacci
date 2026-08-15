@echo off
set "GIT_TERMINAL_PROMPT=0"
set "GIT_SSH_COMMAND=C:/Users/Administrator/.workbuddy/binaries/PortableGit/versions/1.2.0/usr/bin/ssh.exe"
cd /d "C:\Users\Administrator\WorkBuddy\2026-08-04-23-16-18\a-share-fib-wave"
C:\Users\Administrator\.workbuddy\binaries\PortableGit\versions\1.2.0\cmd\git.exe add -A
C:\Users\Administrator\.workbuddy\binaries\PortableGit\versions\1.2.0\cmd\git.exe commit -m "chore: auto-update" >nul 2>&1
C:\Users\Administrator\.workbuddy\binaries\PortableGit\versions\1.2.0\cmd\git.exe pull --ff-only origin main
C:\Users\Administrator\.workbuddy\binaries\PortableGit\versions\1.2.0\cmd\git.exe push -u origin main
echo.
echo === DONE: if you see 'main -> main', push succeeded ===
pause
