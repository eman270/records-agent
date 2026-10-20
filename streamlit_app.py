"""Streamlit UI for the employee records agent.

Local:

    streamlit run streamlit_app.py --server.port 8502
"""

import os

import streamlit as st
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from agent import run_question_sync

st.set_page_config(page_title="Records agent", layout="centered")
st.title("Records agent")
st.write(
    "Ask about an employee. The agent looks up name, department, "
    "and location in the Supabase employees table."
)

question = st.text_area(
    "Question",
    height=120,
    placeholder="Who works in Atlanta?",
)

if st.button("Run", type="primary"):
    if not question.strip():
        st.warning("Type a question first.")
        st.stop()
    missing = [
        name
        for name in ("GOOGLE_API_KEY", "SUPABASE_URL", "SUPABASE_KEY")
        if not os.getenv(name)
    ]
    if missing:
        st.error("Missing environment variables: " + ", ".join(missing))
    else:
        with st.spinner("Running the agent..."):
            try:
                result = run_question_sync(question.strip())
            except Exception as exc:
                result = {
                    "answer": "(no response)",
                    "steps": [
                        {
                            "label": "Observe",
                            "author": "records_agent",
                            "detail": str(exc),
                        }
                    ],
                    "error": str(exc),
                }
        st.session_state["records_result"] = result

result = st.session_state.get("records_result")
if result:
    st.subheader("Steps")
    for step in result["steps"]:
        label = step["label"]
        detail = step["detail"]
        if label == "Think":
            st.info(f"**Think.** {detail}")
        elif label == "Act":
            st.warning(f"**Act.** {detail}")
        elif label == "Observe":
            with st.expander("Observe", expanded=True):
                st.code(detail, language="text")
        elif label == "Answer":
            continue
        else:
            st.write(f"**{label}.** {detail}")

    st.subheader("Final answer")
    if result.get("error") and result["answer"] == "(no response)":
        st.error(result["error"])
    else:
        st.success(result["answer"])
