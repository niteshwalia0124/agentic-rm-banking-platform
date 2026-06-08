FROM python:3.12-slim
WORKDIR /app

COPY bridge/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY bridge/ ./bridge/

ENV PORT=8010
ENV GCP_PROJECT=<YOUR_GCP_PROJECT>
ENV GCP_LOCATION=global
ENV GEMINI_LIVE_MODEL=gemini-3.1-flash-live-preview
EXPOSE 8010

CMD ["sh", "-c", "uvicorn bridge.liveapi_broker:app --host 0.0.0.0 --port ${PORT:-8010}"]
