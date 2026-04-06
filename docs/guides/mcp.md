# MCP Server Guide

This project exposes flight search tools via a FastMCP server. You can run it over STDIO (default) or the streamable HTTP transport.

## Installation

```bash
# Install with pipx (recommended)
pipx install flights

# Or with pip
pip install flights
```

## Running the Server

### Run over STDIO

Use the console script for Claude Desktop and other MCP clients:

```bash
fli-mcp
```

### Run over HTTP (streamable)

Use the HTTP entrypoint for web-based integrations. By default it binds to `127.0.0.1:8000`.

```bash
fli-mcp-http
```

You can override host/port by calling the function directly in Python:

```python
from fli.mcp import run_http

run_http(host="0.0.0.0", port=8000)
```

Once running, the MCP endpoint is served at `/mcp/`, for example: `http://127.0.0.1:8000/mcp/`.

## Claude Desktop Configuration

Add this configuration to your `claude_desktop_config.json`:

**Location**: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)

```json
{
  "mcpServers": {
    "fli": {
      "command": "fli-mcp"
    }
  }
}
```

> **Tip**: Run `which fli-mcp` to find the full path if needed.

## Available Tools

### `search_flights`

Search for exact-date one-way, round-trip, and multi-city itineraries.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `segments` | list | Yes | - | Ordered itinerary segments with `origin`, `destination`, and `date` |
| `cabin_class` | string | No | ECONOMY | ECONOMY, PREMIUM_ECONOMY, BUSINESS, or FIRST |
| `max_stops` | string | No | ANY | ANY, NON_STOP, ONE_STOP, or TWO_PLUS_STOPS |
| `departure_window` | string | No | null | Time window in 'HH-HH' format (e.g., '6-20') |
| `arrival_time_window` | string | No | null | Arrival time window in 'HH-HH' format (e.g., '8-22') |
| `airlines` | list | No | null | Filter by airline codes (e.g., ['BA', 'AA']) |
| `sort_by` | string | No | CHEAPEST | CHEAPEST, DURATION, DEPARTURE_TIME, or ARRIVAL_TIME |
| `passengers` | int | No | 1 | Number of adult passengers |
| `num_cabin_luggage` | int | No | null | Cabin baggage count used when pricing fares |
| `duration` | int | No | null | Maximum itinerary duration in minutes |

**Example Response:**

```json
{
  "success": true,
  "flights": [
    {
      "price": 680.0,
      "currency": "USD",
      "legs": [
        {
          "departure_airport": "JFK",
          "arrival_airport": "LAX",
          "departure_time": "2026-03-15T18:00:00",
          "arrival_time": "2026-03-15T21:00:00",
          "duration": 180,
          "airline": "AA",
          "flight_number": "AA100"
        }
      ]
    }
  ],
  "count": 1,
  "trip_type": "MULTI_CITY"
}
```

### `search_dates`

Find the cheapest itinerary dates within a date range by scanning exact-date searches.

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `segments` | list | Yes | - | Ordered itinerary templates; segment 1 uses the scanned date and later segments use `day_offset` |
| `start_date` | string | Yes | - | Start of date range in YYYY-MM-DD format |
| `end_date` | string | Yes | - | End of date range in YYYY-MM-DD format |
| `cabin_class` | string | No | ECONOMY | ECONOMY, PREMIUM_ECONOMY, BUSINESS, or FIRST |
| `max_stops` | string | No | ANY | ANY, NON_STOP, ONE_STOP, or TWO_PLUS_STOPS |
| `departure_window` | string | No | null | Time window in 'HH-HH' format (e.g., '6-20') |
| `arrival_time_window` | string | No | null | Arrival time window in 'HH-HH' format (e.g., '8-22') |
| `airlines` | list | No | null | Filter by airline codes (e.g., ['BA', 'AA']) |
| `sort_by_price` | bool | No | false | Sort results by price (lowest first) |
| `passengers` | int | No | 1 | Number of adult passengers |

**Example Response:**

```json
{
  "success": true,
  "dates": [
    {
      "date": "2026-03-15",
      "segment_dates": ["2026-03-15", "2026-03-22"],
      "price": 350.00,
      "currency": "USD",
      "return_date": "2026-03-22"
    },
    {
      "date": "2026-03-18",
      "segment_dates": ["2026-03-18", "2026-03-25"],
      "price": 375.00,
      "currency": "USD",
      "return_date": "2026-03-25"
    }
  ],
  "count": 30,
  "trip_type": "ROUND_TRIP",
  "date_range": "2026-03-01 to 2026-03-31"
}
```

## Available Prompts

The MCP server also provides prompt templates to help guide searches:

### `search-direct-flight`

Generates a tool call to find direct flights between two airports.

**Arguments:**
- `origin` - Departure airport IATA code (required)
- `destination` - Arrival airport IATA code (required)
- `date` - Departure date in YYYY-MM-DD format (optional)
- `prefer_non_stop` - Set to true to prefer nonstop flights (optional)

### `find-budget-window`

Suggests the cheapest travel dates for a route within a flexible window.

**Arguments:**
- `origin` - Departure airport IATA code (required)
- `destination` - Arrival airport IATA code (required)
- `start_date` - Start of the travel window (optional)
- `end_date` - End of the travel window (optional)
- `duration` - Desired trip length in days; used as a hint for `day_offset` values (optional)

## Available Resources

### `resource://fli-mcp/configuration`

Returns:
- current MCP default values
- the JSON schema for those settings
- the supported `FLI_MCP_*` environment variables

## Configuration

The MCP server can be configured via environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `FLI_MCP_DEFAULT_PASSENGERS` | Default number of adult passengers | 1 |
| `FLI_MCP_DEFAULT_CURRENCY` | Currency code attached to serialized results | EUR |
| `FLI_MCP_DEFAULT_MARKET` | Google Flights market query param (`gl`) used for pricing/results | de |
| `FLI_MCP_DEFAULT_LANGUAGE` | Optional Google Flights language query param (`hl`) | null |
| `FLI_MCP_DEFAULT_CABIN_CLASS` | Default cabin class | ECONOMY |
| `FLI_MCP_DEFAULT_SORT_BY` | Default sorting strategy | CHEAPEST |
| `FLI_MCP_DEFAULT_DEPARTURE_WINDOW` | Default departure window (HH-HH) | null |
| `FLI_MCP_MAX_RESULTS` | Maximum results returned | null (no limit) |

## Example Conversations

Once configured with Claude Desktop, you can have natural conversations:

> **User**: "Find me flights from New York to London next month"
> 
> **Claude**: *Uses `search_flights` with one dated segment from JFK to LHR*

> **User**: "What are the cheapest dates to fly to Tokyo from San Francisco in April?"
> 
> **Claude**: *Uses `search_dates` with a segment template starting at SFO, ending at NRT, and an April date window*

> **User**: "Search for business class, non-stop flights from LAX to Paris on March 15th"
> 
> **Claude**: *Uses `search_flights` with cabin_class=BUSINESS, max_stops=NON_STOP*
