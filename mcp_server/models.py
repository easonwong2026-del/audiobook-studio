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
    "project_outline",
    "segment_listing",
    "voice_asset_catalog",
    "character_roster",
    "voice_cast",
    "chapter_role_resolution",
    "production_jobs",
    "production_task_control",
    "production_performance_trace",
    "runtime_health",
    "engine_self_healing",
    "workflow_state",
    "agent_action_contract",
    "repair_jobs",
    "export_jobs",
    "delivery_manifests",
]


def server_info() -> dict[str, Any]:
    """Return the stable Audiobook Studio MCP capability contract."""
    return {
        "name": "Audiobook Studio",
        "api_version": API_VERSION,
        "structured_script_version": STRUCTURED_SCRIPT_VERSION,
        "capabilities": list(CAPABILITIES),
    }
