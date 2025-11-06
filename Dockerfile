FROM python:3.11-slim
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1

COPY requirements.txt .
RUN python -m pip install -U pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Varsayilanlar — HF Spaces'de "Variables" ile override edilebilir
ENV DOCS_DIR=/app/docs \
    CACHE_TTL=300 \
    APP_TITLE="Firat Universitesi Sinif Asistani" \
    DEBUG=0

EXPOSE 7860
CMD ["sh","-c","uvicorn app:app --host 0.0.0.0 --port ${PORT:-7860}"]