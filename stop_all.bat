@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title 장애 대응 Agent - 종료

set SERVICE_PORT=8081
set INVENTORY_PORT=8082
set UI_PORT=8501

echo 장애 대응 Agent 환경을 종료합니다.
echo.

echo [1/3] 장애 재현 서비스 종료 (포트 %SERVICE_PORT%)
call :kill_port %SERVICE_PORT%
taskkill /FI "WINDOWTITLE eq order-service*" /T /F > nul 2>&1

echo [2/3] 재고 서비스 종료 (포트 %INVENTORY_PORT%)
call :kill_port %INVENTORY_PORT%
taskkill /FI "WINDOWTITLE eq inventory-service*" /T /F > nul 2>&1

echo [3/3] 장애 대응 콘솔 종료 (포트 %UI_PORT%)
call :kill_port %UI_PORT%
taskkill /FI "WINDOWTITLE eq console*" /T /F > nul 2>&1

echo.
echo 완료. (Ollama는 다른 작업에서도 쓰므로 그대로 둡니다)
pause
goto :eof

:kill_port
set found=
set last=
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%1 .*LISTENING"') do (
    if not "%%p"=="!last!" (
        set found=1
        set last=%%p
        taskkill /PID %%p /F > nul 2>&1 && echo       종료: PID %%p || echo       PID %%p 종료 실패 ^(권한 부족일 수 있음^)
    )
)
if not defined found echo       실행 중이 아님
goto :eof
