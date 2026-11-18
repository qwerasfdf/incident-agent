@echo off
setlocal
cd /d "%~dp0"
title 장애 대응 Agent - 시작

rem ===== 포트 설정 (application.yml의 server.port와 같아야 함) =====
set SERVICE_PORT=8081
set INVENTORY_PORT=8082
set UI_PORT=8501
set OLLAMA_PORT=11434

echo ============================================
echo   장애 대응 Agent 환경 시작
echo ============================================
echo.

rem ---------- 0. 필수 프로그램 확인 ----------
where java > nul 2>&1 || (echo [오류] Java가 없습니다. JDK 17을 설치하세요. & goto end)
py -3 --version > nul 2>&1 || (echo [오류] Python 3가 없습니다. & goto end)
where curl > nul 2>&1 || (echo [오류] curl이 없습니다. Windows 10 이상이 필요합니다. & goto end)

py -3 -c "import streamlit, langgraph" > nul 2>&1
if errorlevel 1 (
    echo [준비] 필요한 Python 패키지를 설치합니다...
    py -3 -m pip install -r requirements.txt
    if errorlevel 1 (echo [오류] 패키지 설치 실패. 위 메시지를 확인하세요. & goto end)
)

rem ---------- 1. Ollama ----------
echo [1/4] Ollama 확인
curl -s -f -o nul http://localhost:%OLLAMA_PORT%/api/tags
if errorlevel 1 (
    where ollama > nul 2>&1
    if errorlevel 1 (
        echo       Ollama가 설치되어 있지 않습니다. Agent 분석 외 기능만 사용할 수 있습니다.
    ) else (
        echo       꺼져 있어서 실행합니다.
        start "ollama" /min ollama serve
        timeout /t 3 /nobreak > nul
    )
) else (
    echo       이미 실행 중
)

rem ---------- 2. 재고 서비스 (inventory-service) ----------
echo [2/4] 재고 서비스 (포트 %INVENTORY_PORT%)
curl -s -f -o nul http://localhost:%INVENTORY_PORT%/actuator/health
if not errorlevel 1 (
    echo       이미 실행 중
    goto inventory_ok
)
start "inventory-service" /d "%~dp0inventory-service" cmd /k gradlew.bat bootRun
echo       시작하는 중... (처음에는 1~2분 걸릴 수 있음)
set tries=0
:wait_inventory
timeout /t 3 /nobreak > nul
curl -s -f -o nul http://localhost:%INVENTORY_PORT%/actuator/health
if not errorlevel 1 goto inventory_ok
set /a tries+=1
if %tries% geq 80 goto inventory_fail
goto wait_inventory
:inventory_fail
echo [오류] 4분이 지나도 재고 서비스가 뜨지 않았습니다. "inventory-service" 창의 오류 메시지를 확인하세요.
goto end
:inventory_ok
echo       준비 완료 - http://localhost:%INVENTORY_PORT%/actuator/health

rem ---------- 3. 장애 재현 서비스 (order-service) ----------
echo [3/4] 장애 재현 서비스 (포트 %SERVICE_PORT%)
curl -s -f -o nul http://localhost:%SERVICE_PORT%/actuator/health
if not errorlevel 1 (
    echo       이미 실행 중
    goto service_ok
)
start "order-service" /d "%~dp0order-service" cmd /k gradlew.bat bootRun
echo       시작하는 중... (처음에는 1~2분 걸릴 수 있음)
set tries=0
:wait_service
timeout /t 3 /nobreak > nul
curl -s -f -o nul http://localhost:%SERVICE_PORT%/actuator/health
if not errorlevel 1 goto service_ok
set /a tries+=1
if %tries% geq 80 goto service_fail
goto wait_service
:service_fail
echo [오류] 4분이 지나도 서비스가 뜨지 않았습니다. "order-service" 창의 오류 메시지를 확인하세요.
goto end
:service_ok
echo       준비 완료 - http://localhost:%SERVICE_PORT%/actuator/health

rem ---------- 4. 장애 대응 콘솔 (Streamlit) ----------
echo [4/4] 장애 대응 콘솔 (포트 %UI_PORT%)
curl -s -f -o nul http://localhost:%UI_PORT%
if not errorlevel 1 (
    echo       이미 실행 중
    goto ui_ok
)
start "console" /d "%~dp0" cmd /k py -3 -m streamlit run ui\app.py --server.port %UI_PORT%
set tries=0
:wait_ui
timeout /t 2 /nobreak > nul
curl -s -f -o nul http://localhost:%UI_PORT%
if not errorlevel 1 goto ui_ok
set /a tries+=1
if %tries% geq 30 (echo [오류] 콘솔이 뜨지 않았습니다. "console" 창을 확인하세요. & goto end)
goto wait_ui
:ui_ok
echo       준비 완료 - http://localhost:%UI_PORT%
start "" http://localhost:%UI_PORT%

echo.
echo ============================================
echo   모두 실행되었습니다. 브라우저에서 콘솔을 확인하세요.
echo   끌 때는 stop_all.bat 을 실행하세요.
echo ============================================

:end
echo.
pause
