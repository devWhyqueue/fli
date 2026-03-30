"""Prompt registration for the MCP server."""

from datetime import datetime, timedelta, timezone

from mcp.types import PromptArgument, PromptMessage, TextContent

from .app import mcp


def _build_search_prompt(args: dict[str, str]) -> list[PromptMessage]:
    """Create a helper prompt to guide flight searches."""
    origin = args.get("origin", "JFK").upper()
    destination = args.get("destination", "LHR").upper()
    date = args.get("date") or datetime.now(timezone.utc).date().isoformat()
    prefer_non_stop = args.get("prefer_non_stop", "true").lower()
    max_stops_hint = "NON_STOP" if prefer_non_stop in {"true", "1", "yes"} else "ANY"
    text = (
        "Use the `search_flights` tool to look for flights from "
        f"{origin} to {destination} departing on {date}. "
        f"Set `max_stops` to '{max_stops_hint}' and highlight the three most affordable options."
    )
    return [PromptMessage(role="user", content=TextContent(type="text", text=text))]


def _build_budget_prompt(args: dict[str, str]) -> list[PromptMessage]:
    """Create a helper prompt to guide flexible date searches."""
    origin = args.get("origin", "SFO").upper()
    destination = args.get("destination", "NRT").upper()
    today = datetime.now(timezone.utc).date()
    start_date = args.get("start_date") or (today + timedelta(days=30)).isoformat()
    end_date = args.get("end_date") or (today + timedelta(days=90)).isoformat()
    duration_hint = _build_duration_hint(args.get("duration"))
    text = (
        "Use the `search_dates` tool to find the lowest fares for an itinerary that starts at "
        f"{origin}, reaches {destination}, and departs between {start_date} and {end_date}. "
        "Represent the trip with `segments` and use day offsets for later legs when needed."
        f"{duration_hint}"
    )
    return [PromptMessage(role="user", content=TextContent(type="text", text=text))]


def _build_duration_hint(duration: str | None) -> str:
    """Build the optional duration guidance sentence for prompt text."""
    if not duration:
        return ""
    return (
        f" If the traveler wants a {duration}-day trip, model the return or later "
        "leg with the matching `day_offset`."
    )


mcp.add_prompt(
    name="search-direct-flight",
    description="Generate a tool call to find direct flights between two airports on a target date.",
    arguments=[
        PromptArgument(name="origin", description="Departure airport IATA code", required=True),
        PromptArgument(name="destination", description="Arrival airport IATA code", required=True),
        PromptArgument(name="date", description="Departure date (YYYY-MM-DD)", required=False),
        PromptArgument(
            name="prefer_non_stop",
            description="Set to true to prefer nonstop itineraries",
            required=False,
        ),
    ],
    build_messages=_build_search_prompt,
)

mcp.add_prompt(
    name="find-budget-window",
    description="Suggest the cheapest travel dates for a route within a flexible window.",
    arguments=[
        PromptArgument(name="origin", description="Departure airport IATA code", required=True),
        PromptArgument(name="destination", description="Arrival airport IATA code", required=True),
        PromptArgument(
            name="start_date",
            description="Start of the travel window (YYYY-MM-DD)",
            required=False,
        ),
        PromptArgument(
            name="end_date",
            description="End of the travel window (YYYY-MM-DD)",
            required=False,
        ),
        PromptArgument(name="duration", description="Desired trip length in days", required=False),
    ],
    build_messages=_build_budget_prompt,
)
