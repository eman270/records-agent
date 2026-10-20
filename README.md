# Records agent

Standalone employee lookup agent. It reads the Supabase `employees` table and exposes an A2A endpoint the policy router can call.

## Local

```powershell
copy .env.example .env
pip install -r requirements.txt
uvicorn agent:app --host 127.0.0.1 --port 8002
streamlit run streamlit_app.py --server.port 8502
```

Agent card: `http://127.0.0.1:8002/.well-known/agent-card.json`

Streamlit UI: `http://127.0.0.1:8502`

## Render

Push this repo to GitHub, then in Render choose **New** → **Blueprint** and select the repo. `render.yaml` creates two web services:

| Service | What it is | URL to keep |
| --- | --- | --- |
| `records-agent` | A2A API | `https://<records-agent-host>/.well-known/agent-card.json` |
| `records-agent-ui` | Streamlit page | the service URL itself |

Set `GOOGLE_API_KEY`, `SUPABASE_URL`, and `SUPABASE_KEY` on both services. Do not commit `.env`.

Paste the agent-card URL into `Learning-Agentic-AI` as `RECORDS_AGENT_CARD_URL`.
