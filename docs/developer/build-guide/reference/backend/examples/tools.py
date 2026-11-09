from langchain_core.tools import tool


@tool
def send_reply(text: str) -> str:
    """Send the drafted reply to the customer."""
    return f"sent: {text}"
