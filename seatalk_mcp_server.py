from mcp.server.fastmcp import FastMCP
import seatalk_client
from config import load_config

config = load_config()
mcp = FastMCP("seatalk")


@mcp.tool()
async def send_message(group: str, message: str) -> str:
    """Send a text message to a named Seatalk group.

    Args:
        group: Name of the group as defined in config.json (e.g. "my-team")
        message: Text content to send
    """
    return await seatalk_client.send_message(config, group, message)


@mcp.tool()
async def list_groups() -> str:
    """List all configured Seatalk groups."""
    return await seatalk_client.list_groups(config)


@mcp.tool()
async def fetch_messages(group: str, limit: int = 10) -> str:
    """Fetch recent messages from a Seatalk group (requires Get Chat History API permission).

    Args:
        group: Name of the group as defined in config.json
        limit: Number of recent messages to retrieve (default 10, max 100)
    """
    return await seatalk_client.fetch_messages(config, group, limit)


@mcp.tool()
async def fetch_message_by_id(message_id: str) -> str:
    """Fetch a single Seatalk message by its message_id.

    Args:
        message_id: The message_id returned by send_message or an event callback
    """
    return await seatalk_client.fetch_message_by_id(config, message_id)


@mcp.tool()
async def get_group_info(group: str) -> str:
    """Fetch live info for a named Seatalk group (name, member count).

    Args:
        group: Name of the group as defined in config.json
    """
    return await seatalk_client.get_group_info(config, group)


@mcp.tool()
async def list_joined_groups() -> str:
    """List all Seatalk groups the bot has joined (fetched live from the API)."""
    return await seatalk_client.list_joined_groups(config)


@mcp.tool()
async def get_employee_code(email: str) -> str:
    """Look up a Seatalk employee code from an email address.

    Args:
        email: The colleague's work email address
    """
    result = await seatalk_client.get_employee_code(config, email)
    if isinstance(result, tuple):
        code, name = result
        return f"{name}: {code}"
    return result


@mcp.tool()
async def send_dm(email: str, message: str) -> str:
    """Send a direct message to a colleague by their work email address.

    Args:
        email: The colleague's work email address
        message: Text content to send
    """
    return await seatalk_client.send_dm(config, email, message)


if __name__ == "__main__":
    mcp.run()
