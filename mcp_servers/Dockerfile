FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_servers/ ./mcp_servers/
COPY external_agents/ ./external_agents/

# Set by Cloud Run deploy --set-env-vars MCP_SERVER=<server_module>
ARG MCP_SERVER=core_banking_mcp
ENV MCP_SERVER=${MCP_SERVER}

# Cloud Run injects PORT; MCP servers read it via os.getenv("PORT", 8001)
CMD python mcp_servers/${MCP_SERVER}.py
