FROM python:3.12-slim
ARG WITH_OPTIONAL=0
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements.txt && if [ "$WITH_OPTIONAL" = "1" ]; then pip install --no-cache-dir -r requirements-optional.txt; fi
COPY src ./src
COPY data ./data
COPY scripts ./scripts
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fs http://127.0.0.1:8000/api/health || exit 1
CMD ["uvicorn", "digital_mechanic.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
