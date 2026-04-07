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
    """Fetch recent messages from a named Seatalk group (on-demand only).

    Args:
        group: Name of the group as defined in config.json
        limit: Number of recent messages to retrieve (default 10)
    """
    return await seatalk_client.fetch_messages(config, group, limit)


@mcp.tool()
async def get_group_info(group: str) -> str:
    """Fetch live info for a named Seatalk group (name, member count).

    Args:
        group: Name of the group as defined in config.json
    """
    return await seatalk_client.get_group_info(config, group)


if __name__ == "__main__":
    mcp.run()
