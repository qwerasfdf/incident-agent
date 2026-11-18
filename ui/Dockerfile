# 빌드 컨텍스트는 레포 루트 (requirements.txt를 참조하기 위해)
FROM python:3.11-slim
WORKDIR /workspace
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 8501
# 실제 코드(agent/, ui/, order-service/logs 등)는 docker-compose의 volume으로
# 레포 전체가 /workspace에 그대로 마운트되므로 여기서는 COPY하지 않는다.
CMD ["streamlit", "run", "ui/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
