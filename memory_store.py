import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


def _session_user_id(tool_context):
    """Read the signed-in user from the ADK session, not from the model."""
    user_id = getattr(tool_context, "user_id", None)
    if not user_id and tool_context is not None:
        session = getattr(tool_context, "session", None)
        user_id = getattr(session, "user_id", None)
    if not str(user_id or "").strip():
        return ""
    return str(user_id).strip()


ALLOWED_MEMORY_KEYS = {
    "report_style",
    "preferred_language",
    "response_length",
}


def validate_memory(memory_key: str, memory_value: str) -> dict:
    """Validate a memory before allowing it to be saved."""
    if memory_key not in ALLOWED_MEMORY_KEYS:
        return {
            "allowed": False,
            "reason": "Memory key is not allowed.",
        }
    if not isinstance(memory_value, str) or not memory_value.strip():
        return {
            "allowed": False,
            "reason": "Memory value must be non-empty text.",
        }
    if len(memory_value) > 200:
        return {
            "allowed": False,
            "reason": "Memory value is too long.",
        }
    return {
        "allowed": True,
        "reason": "Memory passed basic validation.",
    }


def save_memory(memory_key: str, memory_value: str, tool_context) -> dict:
    """Insert or update one preference for the signed-in user.

    The user id comes from the session. Do not pass a user id.

    Args:
        memory_key: Name of the preference, such as report_style.
        memory_value: The preference text the user asked to remember.
    """
    check = validate_memory(memory_key, memory_value)
    if not check["allowed"]:
        return {
            "status": "error",
            "rows": [],
            "message": check["reason"],
        }

    base_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_KEY", "").strip()
    user_id = _session_user_id(tool_context)
    if not base_url or not key:
        return {
            "status": "error",
            "rows": [],
            "message": "SUPABASE_URL or SUPABASE_KEY is not set.",
        }
    if not user_id or not str(memory_key).strip():
        return {
            "status": "error",
            "rows": [],
            "message": "A session user and memory_key are required.",
        }

    data = {
        "user_id": user_id,
        "memory_key": memory_key,
        "memory_value": memory_value,
        "source": "unverified_agent_input",
        "trust_level": "unverified",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    query = urllib.parse.urlencode({"on_conflict": "user_id,memory_key"})
    request = urllib.request.Request(
        f"{base_url}/rest/v1/employee_agent_memory?{query}",
        data=json.dumps(data).encode("utf-8"),
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Prefer": "resolution=merge-duplicates,return=representation",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        return {
            "status": "error",
            "rows": [],
            "message": f"Supabase returned HTTP {exc.code}: {detail}",
        }
    except Exception as exc:
        return {"status": "error", "rows": [], "message": str(exc)}

    rows = json.loads(body) if body.strip() else []
    if not isinstance(rows, list):
        return {
            "status": "error",
            "rows": [],
            "message": "Supabase did not return the saved row.",
        }
    return {"status": "ok", "rows": rows}

def recall_memory(memory_key: str, tool_context) -> dict:
    """Retrieve one saved preference for the signed-in user.

    The user id comes from the session. Do not pass a user id.

    Args:
        memory_key: Name of the preference, such as report_style.
    """
    base_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_KEY", "").strip()
    user_id = _session_user_id(tool_context)
    if not base_url or not key:
        return {
            "status": "error",
            "rows": [],
            "message": "SUPABASE_URL or SUPABASE_KEY is not set.",
        }
    if not user_id or not str(memory_key).strip():
        return {
            "status": "error",
            "rows": [],
            "message": "A session user and memory_key are required.",
        }

    query = urllib.parse.urlencode({
        "user_id": f"eq.{user_id}",
        "memory_key": f"eq.{memory_key}",
        "select": "*",
    })
    request = urllib.request.Request(
        f"{base_url}/rest/v1/employee_agent_memory?{query}",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            rows = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        return {
            "status": "error",
            "rows": [],
            "message": f"Supabase returned HTTP {exc.code}: {detail}",
        }
    except Exception as exc:
        return {"status": "error", "rows": [], "message": str(exc)}

    if not isinstance(rows, list):
        return {
            "status": "error",
            "rows": [],
            "message": "Supabase did not return a list of rows.",
        }
    if not rows:
        return {
            "status": "not_found",
            "rows": [],
            "message": "No saved preference found.",
        }
    return {"status": "ok", "rows": rows}


def delete_memory(memory_key: str, tool_context) -> dict:
    """Delete one saved preference for the signed-in user.

    The user id comes from the session. Do not pass a user id.
    Call this only when the user explicitly asks to forget a preference.

    Args:
        memory_key: Name of the preference to delete, such as report_style.
    """
    base_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    key = os.getenv("SUPABASE_KEY", "").strip()
    user_id = _session_user_id(tool_context)
    if not base_url or not key:
        return {
            "status": "error",
            "rows": [],
            "message": "SUPABASE_URL or SUPABASE_KEY is not set.",
        }
    if not user_id or not str(memory_key).strip():
        return {
            "status": "error",
            "rows": [],
            "message": "A session user and memory_key are required.",
        }

    query = urllib.parse.urlencode({
        "user_id": f"eq.{user_id}",
        "memory_key": f"eq.{memory_key}",
    })
    request = urllib.request.Request(
        f"{base_url}/rest/v1/employee_agent_memory?{query}",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Prefer": "return=representation",
        },
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        return {
            "status": "error",
            "rows": [],
            "message": f"Supabase returned HTTP {exc.code}: {detail}",
        }
    except Exception as exc:
        return {"status": "error", "rows": [], "message": str(exc)}

    rows = json.loads(body) if body.strip() else []
    if not isinstance(rows, list):
        return {
            "status": "error",
            "rows": [],
            "message": "Supabase did not return the deleted row.",
        }
    if not rows:
        return {
            "status": "not_found",
            "rows": [],
            "message": "No saved preference found.",
        }
    return {"status": "ok", "rows": rows}
