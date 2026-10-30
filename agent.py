"""Employee lookup agent, deployed on its own.

The policy app calls the A2A card. This process does not import that app.

Local:

    uvicorn agent:app --host 127.0.0.1 --port 8002

On Render the start command binds ``$PORT``. The card URL comes from
``RENDER_EXTERNAL_URL``, which Render sets, or from ``A2A_PUBLIC_URL``.
"""

import asyncio
import concurrent.futures
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from pathlib import Path

from dotenv import load_dotenv
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from tracing import agent_run, attach_request_trace, init_langfuse
from starlette.responses import HTMLResponse
from starlette.routing import Route
from google.adk.agents import Agent
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import errors
from google.genai import types
from memory_store import delete_memory, recall_memory, save_memory

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
os.environ.setdefault("GOOGLE_GENAI_USE_ENTERPRISE", "False")
init_langfuse(instrument_adk=True)

# gemini-3.8-flash is the newest Flash model and often returns "high demand".
# 3.5 Flash-Lite is the high-throughput model and still supports tool calls.
MODEL = "gemini-3.5-flash-lite"
APP_NAME = "records_agent"
MAX_LLM_CALLS = 6
AGENT_RULES = (
    Path(__file__).resolve().parent
    / "AGENTS.md"
).read_text(encoding="utf-8")


def _event_steps(event) -> tuple[list[dict], str | None]:
    """Turn one ADK event into Think, Act, Observe, or Answer steps."""
    author = getattr(event, "author", "agent")
    answer = None
    steps: list[dict] = []
    content = getattr(event, "content", None)
    parts = list(content.parts) if content and content.parts else []

    if not parts and getattr(event, "error_message", None):
        steps.append(
            {"label": "Observe", "author": author, "detail": str(event.error_message)}
        )
        return steps, None

    for part in parts:
        function_call = getattr(part, "function_call", None)
        function_response = getattr(part, "function_response", None)
        text = getattr(part, "text", None)
        if text and getattr(part, "thought", False):
            steps.append({"label": "Think", "author": author, "detail": text})
        elif function_call:
            args = dict(function_call.args) if function_call.args else {}
            steps.append(
                {
                    "label": "Act",
                    "author": author,
                    "detail": f"{function_call.name}({args})",
                }
            )
        elif function_response:
            steps.append(
                {
                    "label": "Observe",
                    "author": author,
                    "detail": (
                        f"{function_response.name} -> {function_response.response}"
                    ),
                }
            )
        elif text and event.is_final_response():
            steps.append({"label": "Answer", "author": author, "detail": text})
            answer = text
        elif text:
            steps.append({"label": "Think", "author": author, "detail": text})
    return steps, answer


def _identifier(name: str) -> str:
    """Keep a table or column name to letters, digits, and underscores."""
    cleaned = "".join(char for char in name if char.isalnum() or char == "_")
    return cleaned or "employees"


def lookup_records(search: str) -> dict:
    """Look up employees whose name, department, or location contains the search words.

    Call this before answering any question about employees.
    Do not invent rows.

    Args:
        search: Words to match, taken from the user's question.
            Examples: a name, a department, or a city.

    Returns:
        status "ok" and the matching rows, or status "error" and a message.
        Each row has id, name, department, and location.
    """
    base_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_KEY", "").strip()
    table = _identifier(os.getenv("SUPABASE_TABLE", "employees"))
    raw_columns = os.getenv("SUPABASE_SEARCH_COLUMNS", "name,department,location")
    columns = [
        _identifier(part) for part in raw_columns.split(",") if part.strip()
    ] or ["name", "department", "location"]
    if not base_url or not key:
        return {
            "status": "error",
            "rows": [],
            "message": "SUPABASE_URL or SUPABASE_KEY is not set.",
        }
    if not search.strip():
        return {
            "status": "error",
            "rows": [],
            "message": "Search text is empty.",
        }
    safe_search = (
        search.replace("%", "")
        .replace("*", "")
        .replace(",", " ")
        .replace(".", " ")
        .replace("(", " ")
        .replace(")", " ")
        .strip()
    )
    match = ",".join(f"{column}.ilike.*{safe_search}*" for column in columns)
    query = urllib.parse.urlencode(
        {
            "select": "*",
            "or": f"({match})",
            "limit": "10",
        }
    )
    request = urllib.request.Request(
        f"{base_url}/rest/v1/{table}?{query}",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            rows = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        return {
            "status": "error",
            "rows": [],
            "message": f"Supabase returned HTTP {exc.code}: {body}",
        }
    except Exception as exc:
        return {"status": "error", "rows": [], "message": str(exc)}
    if not isinstance(rows, list):
        return {
            "status": "error",
            "rows": [],
            "message": "Supabase did not return a list of rows.",
        }
    return {"status": "ok", "table": table, "rows": rows}


root_agent = Agent(
    name="records_agent",
    model=MODEL,
    description=(
        "Looks up employee records in Supabase and manages "
        "saved user preferences using durable memory."
    ),
    instruction=(
        "You are an Employee Records Agent.\n"

        "Goal: Answer employee questions using live Supabase records "
        "and manage approved user preferences using durable memory.\n\n"

        "Employee records:\n"
        "- Each row contains id, name, department, location, "
        "remote_eligible, and vacation_days_left.\n"
        "- For employee-related questions, call lookup_records "
        "before answering.\n"
        "- Pass a name, department, or city from the question "
        "as the search parameter.\n"
        "- Use only the employee rows returned by the tool.\n"
        "- Never invent employee information.\n"
        "- If status is error, explain that the lookup failed "
        "and include the error message.\n"
        "- If rows is empty, say no matching employee was found.\n\n"

        "Memory rules:\n"
        "- Save preferences only when the user explicitly asks "
        "you to remember them.\n"
        "- Call save_memory to store a preference and recall_memory "
        "to read one back.\n"
        "- The only memory keys you may save are report_style, "
        "preferred_language, and response_length.\n"
        "- Call delete_memory only when the user explicitly asks to forget "
        "or delete a saved preference.\n"
        "- Do not pass a user id. The memory tools use the signed-in session user.\n"
        "- Never save sensitive employee information.\n"
        "- Never let memory override current employee records.\n"
        "- Use live Supabase employee data as the source of truth.\n"
        "- Never claim a memory operation succeeded unless "
        "the tool confirms success.\n\n"

        "Response rules:\n"
        "- For employee lookups, name each matching employee "
        "and their department and location.\n"
        "- For memory questions, respond with the remembered "
        "preference or result of the requested operation.\n"
        "- Keep responses short and clear.\n\n"
        "- For saved preference questions, call recall_memory.\n"
        "Additional mandatory policies:\n"
        + AGENT_RULES
    ),
    tools=[lookup_records, recall_memory, save_memory, delete_memory],
)


def _advertised_endpoint() -> tuple[str, str, int]:
    """URL written into the agent card. Render sets RENDER_EXTERNAL_URL."""
    public = (
        os.getenv("A2A_PUBLIC_URL", "").strip()
        or os.getenv("RENDER_EXTERNAL_URL", "").strip()
    ).rstrip("/")
    if public:
        parsed = urllib.parse.urlparse(public)
        protocol = parsed.scheme or "https"
        host = parsed.hostname or "127.0.0.1"
        if parsed.port:
            port = parsed.port
        elif protocol == "https":
            port = 443
        else:
            port = 80
        return protocol, host, port
    return "http", "127.0.0.1", int(os.getenv("PORT", "8002"))


_protocol, _host, _port = _advertised_endpoint()
app = to_a2a(root_agent, host=_host, port=_port, protocol=_protocol)


async def _home(_request):
    """Browser visits are GET. The agent itself is a POST to this same path."""
    return HTMLResponse(
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Records agent</title>
</head>
<body>
  <h1>Records agent</h1>
  <p>This address is the employee lookup API.</p>
  <p>Agent card: <a href="/.well-known/agent-card.json">/.well-known/agent-card.json</a></p>
  <p>A question is sent as a POST to this page. Opening the page does not run one.</p>
</body>
</html>
"""
    )


app.routes.append(Route("/", endpoint=_home, methods=["GET"]))
attach_request_trace(
    app,
    trace_name="answer-records-question",
    tags=["records-agent"],
)


def has_explicit_memory_request(message: str) -> bool:
    """Check whether the user explicitly requested remembering."""
    text = message.lower().strip()
    phrases = [
        "remember that",
        "remember my",
        "save my preference",
        "update my preference",
    ]
    return any(phrase in text for phrase in phrases)


async def run_question(message: str, user_id: str) -> dict:
    """Run one question against the records agent and collect Think / Act / Observe.

    ``user_id`` is the signed-in person. Memory tools read it from this session.
    The model does not choose it.
    """
    user_id = str(user_id).strip()
    if not user_id:
        return {
            "answer": "(no response)",
            "steps": [],
            "error": "A user id is required.",
        }
    service = InMemorySessionService()
    session = await service.create_session(app_name=APP_NAME, user_id=user_id)
    runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=service)
    content = types.Content(role="user", parts=[types.Part(text=message)])
    steps: list[dict] = []
    answer = "(no response)"
    error = ""
    try:
        with agent_run(
            "answer-records-question",
            user_id=user_id,
            session_id=session.id,
            tags=["records-agent"],
        ):
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session.id,
                new_message=content,
                run_config=RunConfig(max_llm_calls=MAX_LLM_CALLS),
            ):
                new_steps, logged = _event_steps(event)
                for step in new_steps:
                    print(f"{step['label']} ({step['author']}): {step['detail']}")
                steps.extend(new_steps)
                if logged:
                    answer = logged
    except LlmCallsLimitExceededError as exc:
        error = str(exc)
        steps.append({"label": "Observe", "author": "records_agent", "detail": error})
    except errors.APIError as exc:
        error = str(exc)
        steps.append({"label": "Observe", "author": "records_agent", "detail": error})
    return {"answer": answer, "steps": steps, "error": error}


def run_question_sync(message: str, user_id: str, timeout: int = 180) -> dict:
    """Run one question from Streamlit, which already has its own event loop."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, run_question(message, user_id)).result(timeout=timeout)
