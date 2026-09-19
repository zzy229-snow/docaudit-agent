FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# curl 供 HEALTHCHECK 使用;OCR/向量模型等重依赖不进入镜像(见 requirements-rag.txt 与 docs/OCR.md)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data
COPY docs ./docs
COPY scripts ./scripts
COPY tests ./tests
COPY streamlit_app.py README.md ./

# API 8000 / Streamlit 8500(PRD §22.3 公共联调端口)
EXPOSE 8000 8500

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
