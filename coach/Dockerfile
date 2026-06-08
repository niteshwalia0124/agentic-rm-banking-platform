FROM python:3.12-slim
WORKDIR /app

COPY coach/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy coach package + voice_coach agent (the agent it imports)
COPY coach/ ./coach/
COPY agents/ ./agents/

ENV PORT=8006
EXPOSE 8006

CMD ["uvicorn", "coach.server:app", "--host", "0.0.0.0", "--port", "8006"]
