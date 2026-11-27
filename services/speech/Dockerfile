FROM python:3.12-slim
RUN pip install --no-cache-dir fastapi==0.165.0 uvicorn==0.54.0 python-multipart==0.0.34 faster-whisper==1.1.1 "av==13.1.0" \
 && pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
 && pip install --no-cache-dir numpy omegaconf requests silero-stress==1.5
WORKDIR /srv
COPY server.py ru_tts.py denoise.py ./
ENV PYTHONUNBUFFERED=1 HF_HOME=/models/hf
EXPOSE 9000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9000"]
