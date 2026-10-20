# PocketProf API and scheduler image (SDS Table 8). Build from the repository root:
#   docker build -f deploy/api.Dockerfile -t pocketprof-api .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data MODEL_CACHE_DIR=/models

# Tesseract for image-only PDF pages (SDS Table 3); curl for the health check.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng curl \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 pocketprof
WORKDIR /app
COPY backend/pyproject.toml backend/pyproject.toml
COPY backend/src backend/src
RUN pip install ./backend
COPY arms arms
COPY eval eval

# Download the embedding models at build time so the first request is not slow and the stack can run
# without Hugging Face access.
RUN mkdir -p /models /data && chown -R pocketprof /models /data \
 && su pocketprof -c "python -c 'from pocketprof.retrieval.embedder import get_embedder; get_embedder().warm_up()'"

USER pocketprof
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fs http://localhost:8000/health || exit 1
CMD ["uvicorn", "pocketprof.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--proxy-headers"]
