FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

# requirements.txt carries --extra-index-url for the CPU-only PyTorch wheels,
# so no CUDA runtime is pulled in.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# --chown avoids a second full copy of app/ (the model dir alone is ~541MB)
COPY --chown=appuser:appuser app ./app

USER appuser

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
