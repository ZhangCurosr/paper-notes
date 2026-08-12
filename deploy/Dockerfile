# MinerU API Server 容器镜像（Render / HF Spaces / 任意 Docker 平台）
FROM python:3.11-slim

WORKDIR /app

# 依赖
RUN pip install --no-cache-dir requests

# 复制代码
COPY scripts/ scripts/
COPY mineru_accounts.csv.example mineru_accounts.csv.example

# 运行（默认 8900；Render 通过 PORT 环境变量注入端口）
ENV PYTHONUNBUFFERED=1
EXPOSE 8900

CMD ["sh", "-c", "python scripts/mineru_api_server.py --host 0.0.0.0 --port ${PORT:-8900} --data-dir /app/data --out-dir /app/output"]
