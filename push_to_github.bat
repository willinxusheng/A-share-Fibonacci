@echo off
cd /d "C:\Users\Administrator\WorkBuddy\2026-08-04-23-16-18\a-share-fib-wave"
set GIT="C:\Users\Administrator\.workbuddy\binaries\PortableGit\versions\1.2.0\cmd\git.exe"
%GIT% add -A
%GIT% commit -m "chore: auto-update on %date%" >nul 2>&1
%GIT% push -u origin main
echo.
echo === DONE: if you see 'main -^> main', push succeeded ===
pause
