@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==== git init ====
git init
echo ==== git add ====
git add .
echo ==== git commit ====
git commit -m "Initial commit: incident-agent (Spring Boot order-service/inventory-service + LangGraph AI agent + Streamlit UI)"
echo ==== git branch -M main ====
git branch -M main
echo ==== git remote add origin ====
git remote add origin https://github.com/qwerasfdf/incident-agent.git
echo ==== git push ====
git push -u origin main
echo.
echo ==== DONE - review output above ====
pause
