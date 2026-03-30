"""Tests for MCP server functionality."""

import asyncio
from datetime import datetime, timedelta

import fli.mcp.server as server
from fli.mcp.server import (
    DateSearchParams,
    FlightSearchParams,
    configuration_resource,
    mcp,
    search_dates,
    search_flights,
    search_flights_batch,
)
from fli.models import Airline, Airport, FlightLeg, FlightResult


def get_future_date(days: int = 30) -> str:
    """Generate a future date string in YYYY-MM-DD format."""
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def make_flight(departure_airport: Airport, arrival_airport: Airport, price: float) -> FlightResult:
    """Create a simple flight result for MCP tests."""
    departure_time = datetime.now() + timedelta(days=30)
    return FlightResult(
        price=price,
        duration=180,
        stops=0,
        legs=[
            FlightLeg(
                airline=Airline.AA,
                flight_number="AA100",
                departure_airport=departure_airport,
                arrival_airport=arrival_airport,
                departure_datetime=departure_time,
                arrival_datetime=departure_time + timedelta(hours=3),
                duration=180,
            )
        ],
    )


class FakeSearchFlights:
    """Deterministic search stub for MCP tests."""

    def search(self, filters):
        if filters.trip_type == server.TripType.ROUND_TRIP:
            return [
                (
                    make_flight(Airport.LAX, Airport.JFK, 450.0),
                    make_flight(Airport.JFK, Airport.LAX, 450.0),
                )
            ]
        return [make_flight(Airport.JFK, Airport.LHR, 300.0)]

    def search_multi_city_native(self, filters):
        return server.NativeMultiCityResult(
            selected_segments=[
                make_flight(Airport.JFK, Airport.LAX, 200.0),
                make_flight(Airport.LAX, Airport.SFO, 350.0),
                make_flight(Airport.SFO, Airport.JFK, 500.0),
            ],
            completed_itinerary=FlightResult(
                price=500.0,
                duration=540,
                stops=0,
                legs=[
                    *make_flight(Airport.JFK, Airport.LAX, 200.0).legs,
                    *make_flight(Airport.LAX, Airport.SFO, 350.0).legs,
                    *make_flight(Airport.SFO, Airport.JFK, 500.0).legs,
                ],
            ),
            final_price=500.0,
            step_trace=[
                server.NativeMultiCityStep(
                    step_index=0,
                    selected_option_rank=0,
                    displayed_price=200.0,
                    legs=make_flight(Airport.JFK, Airport.LAX, 200.0).legs,
                ),
                server.NativeMultiCityStep(
                    step_index=1,
                    selected_option_rank=0,
                    displayed_price=350.0,
                    legs=make_flight(Airport.LAX, Airport.SFO, 350.0).legs,
                ),
                server.NativeMultiCityStep(
                    step_index=2,
                    selected_option_rank=0,
                    displayed_price=500.0,
                    legs=make_flight(Airport.SFO, Airport.JFK, 500.0).legs,
                ),
            ],
            segment_prices=None,
        )


class TestMCPServer:
    """Test suite for MCP server tools."""

    def test_search_flights_one_way(self, monkeypatch):
        """Test one-way flight search."""
        monkeypatch.setattr(server, "SearchFlights", FakeSearchFlights)
        params = FlightSearchParams(
            segments=[
                server.FlightSearchSegmentParams(
                    origin="JFK",
                    destination="LHR",
                    date=get_future_date(30),
                )
            ],
            cabin_class="ECONOMY",
            max_stops="ANY",
            sort_by="CHEAPEST",
        )

        result = search_flights.fn(params)

        assert result["success"] is True
        assert result["trip_type"] == "ONE_WAY"
        assert result["count"] == 1
        assert isinstance(result["flights"], list)

    def test_search_flights_round_trip(self, monkeypatch):
        """Test round-trip flight search."""
        monkeypatch.setattr(server, "SearchFlights", FakeSearchFlights)
        params = FlightSearchParams(
            segments=[
                server.FlightSearchSegmentParams(
                    origin="LAX",
                    destination="JFK",
                    date=get_future_date(30),
                ),
                server.FlightSearchSegmentParams(
                    origin="JFK",
                    destination="LAX",
                    date=get_future_date(37),
                ),
            ],
            departure_window="8-20",
            airlines=["AA"],
            cabin_class="BUSINESS",
            max_stops="NON_STOP",
            sort_by="DURATION",
        )

        result = search_flights.fn(params)

        assert result["success"] is True
        assert result["trip_type"] == "ROUND_TRIP"
        assert result["count"] == 1
        assert len(result["flights"][0]["legs"]) == 2

    def test_search_flights_multicity(self, monkeypatch):
        """search_flights should normalize exact-date multi-city results."""
        monkeypatch.setattr(server, "SearchFlights", FakeSearchFlights)
        params = FlightSearchParams(
            segments=[
                server.FlightSearchSegmentParams(
                    origin="JFK",
                    destination="LAX",
                    date=get_future_date(30),
                ),
                server.FlightSearchSegmentParams(
                    origin="LAX",
                    destination="SFO",
                    date=get_future_date(33),
                ),
                server.FlightSearchSegmentParams(
                    origin="SFO",
                    destination="JFK",
                    date=get_future_date(36),
                ),
            ]
        )

        result = search_flights.fn(params)

        assert result["success"] is True
        assert result["trip_type"] == "MULTI_CITY"
        assert result["count"] == 1
        assert len(result["flights"]) == 1
        assert result["flights"][0]["price"] == 500.0
        assert len(result["flights"][0]["legs"]) == 3

    def test_search_flights_accepts_max_layover_time(self, monkeypatch):
        """search_flights should accept max_layover_time and keep the response shape."""
        monkeypatch.setattr(server, "SearchFlights", FakeSearchFlights)
        params = FlightSearchParams(
            segments=[
                server.FlightSearchSegmentParams(
                    origin="JFK",
                    destination="LHR",
                    date=get_future_date(30),
                )
            ],
            max_layover_time=180,
        )

        result = search_flights.fn(params)

        assert result["success"] is True
        assert result["count"] == 1

    def test_build_flight_filters_sets_layover_restrictions(self):
        """max_layover_time should populate layover restrictions in built filters."""
        params = FlightSearchParams(
            segments=[
                server.FlightSearchSegmentParams(
                    origin="JFK",
                    destination="LHR",
                    date=get_future_date(30),
                )
            ],
            max_layover_time=180,
        )

        filters, trip_type = server._build_flight_filters(params)

        assert trip_type == server.TripType.ONE_WAY
        assert filters.layover_restrictions is not None
        assert filters.layover_restrictions.max_duration == 180

    def test_build_flight_filters_sets_multicity_layover_restrictions(self):
        """max_layover_time should apply to each segment in multi-city searches."""
        params = FlightSearchParams(
            segments=[
                server.FlightSearchSegmentParams(
                    origin="JFK",
                    destination="LAX",
                    date=get_future_date(30),
                ),
                server.FlightSearchSegmentParams(
                    origin="LAX",
                    destination="SFO",
                    date=get_future_date(33),
                ),
                server.FlightSearchSegmentParams(
                    origin="SFO",
                    destination="JFK",
                    date=get_future_date(36),
                ),
            ],
            max_layover_time=120,
        )

        filters, trip_type = server._build_flight_filters(params)

        assert trip_type == server.TripType.MULTI_CITY
        assert filters.layover_restrictions is not None
        assert filters.layover_restrictions.max_duration == 120
        assert len(filters.flight_segments) == 3

    def test_search_flights_serializes_json_safe_leg_values(self, monkeypatch):
        """search_flights should serialize leg enums and datetimes as strings."""
        monkeypatch.setattr(server, "SearchFlights", FakeSearchFlights)
        params = FlightSearchParams(
            segments=[
                server.FlightSearchSegmentParams(
                    origin="JFK",
                    destination="LHR",
                    date=get_future_date(30),
                )
            ]
        )

        result = search_flights.fn(params)
        leg = result["flights"][0]["legs"][0]

        assert leg["departure_airport"] == "JFK"
        assert leg["arrival_airport"] == "LHR"
        assert leg["airline"] == "AA"
        assert isinstance(leg["departure_time"], str)
        assert isinstance(leg["arrival_time"], str)
        assert "T" in leg["departure_time"]
        assert "T" in leg["arrival_time"]

    def test_search_flights_multicity_none_result(self, monkeypatch):
        """search_flights should return an empty list when native multi-city has no result."""

        class EmptyMultiCitySearch(FakeSearchFlights):
            def search_multi_city_native(self, filters):
                return None

        monkeypatch.setattr(server, "SearchFlights", EmptyMultiCitySearch)
        params = FlightSearchParams(
            segments=[
                server.FlightSearchSegmentParams(
                    origin="JFK",
                    destination="LAX",
                    date=get_future_date(30),
                ),
                server.FlightSearchSegmentParams(
                    origin="LAX",
                    destination="SFO",
                    date=get_future_date(33),
                ),
                server.FlightSearchSegmentParams(
                    origin="SFO",
                    destination="JFK",
                    date=get_future_date(36),
                ),
            ]
        )

        result = search_flights.fn(params)

        assert result["success"] is True
        assert result["trip_type"] == "MULTI_CITY"
        assert result["count"] == 0
        assert result["flights"] == []

    def test_search_dates_multicity(self, monkeypatch):
        """Test flexible multi-city date search orchestration."""
        start_date = get_future_date(30)
        end_date = get_future_date(32)

        def fake_execute_flight_batch(queries, parallelism):
            assert len(queries) == 3
            assert queries[0][1].segments[1].date == get_future_date(33)
            return {
                "success": True,
                "results": [
                    {"index": 0, "success": True, "flights": [{"price": 900.0}, {"price": 850.0}]},
                    {"index": 1, "success": True, "flights": [{"price": 700.0}]},
                    {"index": 2, "success": True, "flights": []},
                ],
                "count": 3,
                "failed": 0,
                "parallelism": parallelism,
            }

        monkeypatch.setattr(server, "_execute_flight_batch", fake_execute_flight_batch)
        params = DateSearchParams(
            segments=[
                server.DateSearchSegmentParams(origin="JFK", destination="LAX"),
                server.DateSearchSegmentParams(origin="LAX", destination="SFO", day_offset=3),
                server.DateSearchSegmentParams(origin="SFO", destination="JFK", day_offset=6),
            ],
            start_date=start_date,
            end_date=end_date,
            sort_by_price=True,
        )

        result = search_dates.fn(params)

        assert result["success"] is True
        assert result["trip_type"] == "MULTI_CITY"
        assert result["count"] == 2
        assert result["dates"][0]["price"] == 700.0
        assert len(result["dates"][0]["segment_dates"]) == 3

    def test_invalid_airport_code(self):
        """Test error handling for invalid airport code."""
        params = FlightSearchParams(
            segments=[
                server.FlightSearchSegmentParams(
                    origin="INVALID",
                    destination="LHR",
                    date=get_future_date(30),
                )
            ]
        )

        result = search_flights.fn(params)

        assert result["success"] is False
        assert "Invalid airport code" in result["error"]
        assert result["flights"] == []

    def test_invalid_departure_window(self):
        """Test error handling for invalid departure window."""
        params = FlightSearchParams(
            segments=[
                server.FlightSearchSegmentParams(
                    origin="JFK",
                    destination="LHR",
                    date=get_future_date(30),
                )
            ],
            departure_window="invalid-time",
        )

        result = search_flights.fn(params)

        assert result["success"] is False
        assert "time range" in result["error"].lower()
        assert result["flights"] == []

    def test_flight_search_params_validation(self):
        """Test FlightSearchParams validation."""
        future_date = get_future_date(30)
        params = FlightSearchParams(
            segments=[
                server.FlightSearchSegmentParams(
                    origin="JFK",
                    destination="LHR",
                    date=future_date,
                )
            ]
        )
        assert params.segments[0].origin == "JFK"
        assert params.segments[0].destination == "LHR"
        assert params.segments[0].date == future_date
        assert params.cabin_class == "ECONOMY"
        assert params.max_stops == "ANY"
        assert params.sort_by == "CHEAPEST"
        assert params.num_cabin_luggage is None
        assert params.duration is None
        assert params.max_layover_time is None

    def test_batch_search(self, monkeypatch):
        """Test batch search interface and result shape."""
        monkeypatch.setattr(server, "SearchFlights", FakeSearchFlights)
        future_date = get_future_date(30)
        result = search_flights_batch(
            queries=[
                {
                    "segments": [
                        {
                            "origin": "JFK",
                            "destination": "LHR",
                            "date": future_date,
                        }
                    ],
                    "departure_time_window": "6-20",
                    "arrival_time_window": "8-22",
                    "num_cabin_luggage": 1,
                    "duration": 900,
                },
                {
                    "segments": [
                        {
                            "origin": "JFK",
                            "destination": "LAX",
                            "date": future_date,
                        },
                        {
                            "origin": "LAX",
                            "destination": "SFO",
                            "date": get_future_date(33),
                        },
                        {
                            "origin": "SFO",
                            "destination": "JFK",
                            "date": get_future_date(36),
                        },
                    ]
                },
                {
                    "segments": [
                        {
                            "origin": "INVALID",
                            "destination": "LHR",
                            "date": future_date,
                        }
                    ]
                },
            ]
        )

        assert result["results"][0]["index"] == 0
        assert result["results"][1]["index"] == 1
        assert result["results"][1]["trip_type"] == "MULTI_CITY"
        assert result["results"][1]["count"] == 1
        assert len(result["results"][1]["flights"][0]["legs"]) == 3
        assert result["results"][2]["index"] == 2
        assert result["count"] == 3

    def test_date_search_params_validation(self):
        """Test DateSearchParams validation."""
        start_date = get_future_date(30)
        end_date = get_future_date(60)
        params = DateSearchParams(
            segments=[
                server.DateSearchSegmentParams(origin="JFK", destination="LHR"),
                server.DateSearchSegmentParams(origin="LHR", destination="JFK", day_offset=7),
            ],
            start_date=start_date,
            end_date=end_date,
        )
        assert params.segments[0].origin == "JFK"
        assert params.segments[1].day_offset == 7
        assert params.start_date == start_date
        assert params.end_date == end_date
        assert params.cabin_class == "ECONOMY"
        assert params.max_stops == "ANY"
        assert params.sort_by_price is False

    def test_prompts_exposed_and_duration_hint_used(self):
        """Prompt metadata should be listed and duration should affect the generated text."""
        prompts = asyncio.run(mcp.list_prompts()).prompts

        assert {prompt.name for prompt in prompts} == {
            "search-direct-flight",
            "find-budget-window",
        }

        result = asyncio.run(
            mcp.get_prompt(
                "find-budget-window",
                {
                    "origin": "SFO",
                    "destination": "NRT",
                    "start_date": "2026-06-01",
                    "end_date": "2026-06-30",
                    "duration": "7",
                },
            )
        )

        text = result.messages[0].content.text
        assert "7-day trip" in text
        assert "`day_offset`" in text

    def test_configuration_resource_exposes_schema(self):
        """Configuration resource should return defaults, schema, and environment metadata."""
        payload = configuration_resource.fn()

        assert "defaults" in payload
        assert "schema" in payload
        assert "FLI_MCP_MAX_RESULTS" in payload
