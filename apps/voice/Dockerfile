FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CONTRACTS_DIR=/contracts

WORKDIR /srv
COPY apps/voice/pyproject.toml apps/voice/README.md ./
COPY apps/voice/app ./app
RUN pip install --no-cache-dir .

COPY packages/contracts /contracts

RUN useradd --system --no-create-home voice
USER voice

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
