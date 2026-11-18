@echo off
cd /d "%~dp0"
echo 장애 대응 콘솔을 시작합니다. 잠시 후 브라우저가 열립니다. (종료: Ctrl + C)
start "" /b cmd /c "timeout /t 5 /nobreak > nul & start http://localhost:8501"
py -3 -m streamlit run ui\app.py
