from dotenv import load_dotenv
from memory_store import save_memory, recall_memory

# Load our Supabase settings from .env
load_dotenv()


class SessionUser:
    """Stand-in for the ADK tool context. The agent passes the real session."""

    def __init__(self, user_id):
        self.user_id = user_id


session = SessionUser("demo_user")


def main():
    # Save a memory
    result = save_memory(
        memory_key="report_style",
        memory_value="I prefer detailed employee reports",
        tool_context=session,
    )

    print("Memory saved!")
    print(result)

    # Retrieve the preference saved earlier
    result = recall_memory(
        memory_key="report_style",
        tool_context=session,
    )

    print("Memory retrieved:")
    print(result)

    result2 = save_memory(
        memory_key="employee_salary",
        memory_value="100000",
        tool_context=SessionUser("Elena"),
    )
    print("Rejected memory:")
    print(result2)


if __name__ == "__main__":
    main()
