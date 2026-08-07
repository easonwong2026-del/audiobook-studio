"""Audiobook Studio MCP V1 stdio server.

The package is deliberately independent from the Gradio application.  MCP
adapters call the same services used by the UI and never call UI callbacks.
"""

from .models import API_VERSION, STRUCTURED_SCRIPT_VERSION

__all__ = ["API_VERSION", "STRUCTURED_SCRIPT_VERSION"]
