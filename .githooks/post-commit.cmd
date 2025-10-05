@echo off
REM Git can execute .cmd directly on Windows; this wrapper invokes the PowerShell hook.
SETLOCAL ENABLEDELAYEDEXPANSION

set HOOK_DIR=%~dp0
set PS_SCRIPT=%HOOK_DIR%post-commit.ps1
set LOG_DIR=dist
set LOG_FILE=%LOG_DIR%\.post-commit.log

if not exist %LOG_DIR% mkdir %LOG_DIR% >NUL 2>&1
echo [%DATE% %TIME%] wrapper start >> %LOG_FILE%

if not exist "%PS_SCRIPT%" (
  echo [post-commit wrapper] Missing PowerShell script: %PS_SCRIPT% >> %LOG_FILE%
  goto :EOF
)

REM Prefer pwsh, fallback to Windows PowerShell
where pwsh >NUL 2>&1
if %ERRORLEVEL%==0 (
  pwsh -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" >> %LOG_FILE% 2>&1
  set STATUS=%ERRORLEVEL%
) else (
  where powershell >NUL 2>&1
  if %ERRORLEVEL%==0 (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" >> %LOG_FILE% 2>&1
    set STATUS=%ERRORLEVEL%
  ) else (
    echo [post-commit wrapper] No PowerShell host found (pwsh/powershell) >> %LOG_FILE%
    set STATUS=0
  )
)

if NOT %STATUS%==0 (
  echo [post-commit wrapper] PowerShell script failed (exit %STATUS%). Attempting minimal fallback build. >> %LOG_FILE%
  where docker >NUL 2>&1
  if %ERRORLEVEL%==0 (
    for /f %%I in ('git rev-parse --short HEAD 2^>NUL') do set SHORT_SHA=%%I
    if "!SHORT_SHA!"=="" set SHORT_SHA=unknown
    set TAG=fallback-!SHORT_SHA!
    docker build -t personal-tracking:!TAG! . >> %LOG_FILE% 2>&1 && (
      docker save -o dist\personal-tracking-!TAG!.tar personal-tracking:!TAG! >> %LOG_FILE% 2>&1 && (
        copy /Y dist\personal-tracking-!TAG!.tar dist\personal-tracking-latest.tar >NUL 2>&1
        echo [post-commit wrapper] Fallback tar created: dist\personal-tracking-!TAG!.tar >> %LOG_FILE%
      ) || echo [post-commit wrapper] Fallback docker save failed >> %LOG_FILE%
    ) || echo [post-commit wrapper] Fallback docker build failed >> %LOG_FILE%
  ) else (
    echo [post-commit wrapper] Docker not available for fallback. >> %LOG_FILE%
  )
)

echo [%DATE% %TIME%] wrapper end status=%STATUS% >> %LOG_FILE%
ENDLOCAL
exit /B 0