FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY gateway/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy full project (agents + mcp_servers + external_agents)
COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "gateway.a2a_server:app", "--host", "0.0.0.0", "--port", "8080"]
