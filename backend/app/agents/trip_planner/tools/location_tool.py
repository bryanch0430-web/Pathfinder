"""
Location and origin tools for Pathfinder conversational trip planning.
"""
from typing import Any


def get_user_location() -> dict[str, Any]:
    """
    Request the user's origin/location through the frontend client.

    This tool instructs the client application to trigger browser geolocation
    or present the origin location selection UI. It does not fabricate coordinates
    or make client assumptions.

    Returns:
        Structured action payload instructing the frontend client to request location.
    """
    return {
        "type": "request_user_location",
        "action": "use_current_location",
        "message": "Requesting device location to set trip origin departure point.",
    }
