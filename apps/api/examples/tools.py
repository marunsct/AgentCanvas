from langchain_core.tools import tool


@tool
def send_reply(text: str) -> str:
    """Send the drafted reply to the customer."""
    return f"sent: {text}"


@tool
def lookup_order(order_id: str) -> str:
    """Look up an order's status."""
    return f"order {order_id}: shipped"
