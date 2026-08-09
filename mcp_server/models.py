"""Small protocol models shared by the MCP adapters."""
from __future__ import annotations

from typing import Any

API_VERSION = "1"
STRUCTURED_SCRIPT_VERSION = "3.0"

CAPABILITIES = [
    "structured_script_validation",
    "project_creation",
    "project_listing",
    "project_inspection",
    "voice_asset_catalog",
    "character_roster",
    "voice_cast",
    "chapter_role_resolution",
    "production_jobs",
]


def server_info() -> dict[str, Any]:
    """Return the stable Audiobook Studio MCP capability contract."""
    return {
        "name": "Audiobook Studio",
        "api_version": API_VERSION,
        "structured_script_version": STRUCTURED_SCRIPT_VERSION,
        "capabilities": list(CAPABILITIES),
    }
