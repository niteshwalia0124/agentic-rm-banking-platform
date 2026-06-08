# Registering the Orchestrator in Gemini Enterprise Agentspace

## Step 1 — Deploy the A2A Gateway to Cloud Run

```bash
# From the project root
gcloud run deploy fsi-rm-a2a-gateway \
  --source . \
  --dockerfile gateway/Dockerfile \
  --region asia-south1 \
  --set-env-vars GCP_PROJECT=$GCP_PROJECT,BQ_DATASET=fsi_rm_poc \
  --set-env-vars GATEWAY_URL=https://fsi-rm-a2a-gateway-<hash>-el.a.run.app \
  --allow-unauthenticated \
  --memory 2Gi \
  --concurrency 80

# Note the Cloud Run URL — you'll need it in Step 2
```

## Step 2 — Verify the Agent Card

```bash
curl https://fsi-rm-a2a-gateway-<hash>.a.run.app/.well-known/agent.json
```

You should see the JSON Agent Card describing the orchestrator's skills.

## Step 3 — Register in Agentspace

1. Open **Google Workspace Admin Console** → Apps → Google Workspace → Agentspace
2. Go to **Agents** → **Add Agent** → **External Agent (A2A)**
3. Enter the Agent Card URL:
   ```
   https://fsi-rm-a2a-gateway-<hash>.a.run.app/.well-known/agent.json
   ```
4. Agentspace reads the card, shows the skills, asks you to confirm
5. Set **Audience**: limit to your bank's Workspace domain (e.g., `@yourbank.com`)
6. Click **Register**

## Step 4 — Test in Gemini for Workspace

Open Google Chat or Gmail → click the Gemini icon → type:

```
Give me my morning brief for RM001
```

Agentspace routes this to your orchestrator via A2A. The orchestrator calls the right
sub-agents, MCP servers, and AWS agents — and returns the response in Gemini's UI.

## How the A2A call looks (what Agentspace sends)

```json
POST https://fsi-rm-a2a-gateway-<hash>.a.run.app/
{
  "jsonrpc": "2.0",
  "method": "tasks/send",
  "id": "req-abc123",
  "params": {
    "id": "task-xyz789",
    "message": {
      "role": "user",
      "parts": [{ "type": "text", "text": "Give me my morning brief for RM001" }]
    },
    "metadata": {
      "user_id": "rm001@yourbank.com",
      "session_id": "conv-20260521-rm001"
    }
  }
}
```

The gateway translates this to `runner.run()`, the orchestrator responds,
and the gateway wraps the reply in A2A format back to Agentspace.

## Production hardening (post-demo)

| What | How |
|---|---|
| Auth | Validate Agentspace bearer token against Google's OIDC |
| Sessions | Replace InMemorySessionService with Vertex AI Session Service |
| Rate limiting | Add Agent Gateway in front of the A2A server |
| Scaling | Cloud Run auto-scales; set `--min-instances 1` to avoid cold starts |
