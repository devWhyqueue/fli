"""Helpers for MCP configuration resource payloads."""

from __future__ import annotations

_DEFAULT_VARIABLES = {
    "FLI_MCP_DEFAULT_PASSENGERS": "Adjust the default passenger count.",
    "FLI_MCP_DEFAULT_CURRENCY": "Override the currency code returned with results.",
    "FLI_MCP_DEFAULT_MARKET": "Set the Google Flights market (`gl`) used for pricing.",
    "FLI_MCP_DEFAULT_LANGUAGE": "Set the Google Flights language (`hl`) used for requests.",
    "FLI_MCP_DEFAULT_CABIN_CLASS": "Set a default cabin class.",
    "FLI_MCP_DEFAULT_SORT_BY": "Set the default result sorting strategy.",
    "FLI_MCP_DEFAULT_DEPARTURE_WINDOW": "Provide a default departure window (HH-HH).",
    "FLI_MCP_MAX_RESULTS": "Limit the maximum number of results returned by tools.",
}


def build_configuration_payload(
    defaults: dict[str, object], schema: dict[str, object]
) -> dict[str, object]:
    """Build the JSON payload exposed by the MCP configuration resource."""
    return {
        "defaults": defaults,
        "schema": schema,
        "environment": {
            "prefix": "FLI_MCP_",
            "variables": _DEFAULT_VARIABLES,
        },
    }

