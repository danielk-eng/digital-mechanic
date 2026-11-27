# x86_64 + RTX 3090: default base. DGX Spark (arm64): --build-arg BASE=nvcr.io/nvidia/pytorch:26.11-py3
ARG BASE=pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime
FROM ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /srv
COPY server.py .
ENV PYTHONUNBUFFERED=1
EXPOSE 9000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9000"]
