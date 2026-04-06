"""Tests for MCP server functionality."""

import asyncio
import json
import time
from datetime import datetime, timedelta

import fli.mcp.execution as execution
import fli.mcp.server as server
from fli.mcp.server import (
    DateSearchParams,
    FlightSearchParams,
    _effective_batch_parallelism,
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

    def __init__(self, request_params=None):
        self.request_params = request_params

    def search(self, filters):
        if filters.trip_type == server.TripType.ROUND_TRIP:
            return [
                (
                    make_flight(Airport.LAX, Airport.JFK, 450.0),
                    make_flight(Airport.JFK, Airport.LAX, 450.0),
                )
            ]
        if filters.trip_type == server.TripType.MULTI_CITY:
            return [
                FlightResult(
                    price=500.0,
                    duration=540,
                    stops=0,
                    legs=[
                        *make_flight(Airport.JFK, Airport.LAX, 200.0).legs,
                        *make_flight(Airport.LAX, Airport.SFO, 350.0).legs,
                        *make_flight(Airport.SFO, Airport.JFK, 500.0).legs,
                    ],
                ),
                FlightResult(
                    price=650.0,
                    duration=600,
                    stops=1,
                    legs=[
                        *make_flight(Airport.JFK, Airport.LAX, 250.0).legs,
                        *make_flight(Airport.LAX, Airport.SFO, 400.0).legs,
                        *make_flight(Airport.SFO, Airport.JFK, 650.0).legs,
                    ],
                ),
            ]
        return [make_flight(Airport.JFK, Airport.LHR, 300.0)]


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
        assert result["count"] == 2
        assert len(result["flights"]) == 2
        assert result["flights"][0]["price"] == 500.0
        assert len(result["flights"][0]["legs"]) == 3

    def test_search_flights_multicity_uses_direct_search(self, monkeypatch):
        """Exact-date multi-city searches should use the direct search path only."""

        class SearchOnlyFake(FakeSearchFlights):
            def search(self, filters):
                if filters.trip_type == server.TripType.MULTI_CITY:
                    return [
                        FlightResult(
                            price=420.0,
                            duration=360,
                            stops=0,
                            legs=[
                                *make_flight(Airport.JFK, Airport.LAX, 120.0).legs,
                                *make_flight(Airport.LAX, Airport.SFO, 240.0).legs,
                            ],
                        )
                    ]
                return super().search(filters)

            def __getattr__(self, name):
                if name == "search_multi_city_native":
                    raise AssertionError("search_multi_city_native should not be used")
                raise AttributeError(name)

        monkeypatch.setattr(server, "SearchFlights", SearchOnlyFake)
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
            ]
        )

        result = search_flights.fn(params)

        assert result["success"] is True
        assert result["trip_type"] == "MULTI_CITY"
        assert result["count"] == 1

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
        """search_flights should return an empty list when direct multi-city search has no result."""

        class EmptyMultiCitySearch(FakeSearchFlights):
            def search(self, filters):
                if filters.trip_type == server.TripType.MULTI_CITY:
                    return None
                return super().search(filters)

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
                FlightSearchParams(
                    segments=[
                        server.FlightSearchSegmentParams(
                            origin="JFK",
                            destination="LHR",
                            date=future_date,
                        )
                    ],
                    departure_time_window="6-20",
                    arrival_time_window="8-22",
                    num_cabin_luggage=1,
                    duration=900,
                ),
                FlightSearchParams(
                    segments=[
                        server.FlightSearchSegmentParams(
                            origin="JFK",
                            destination="LAX",
                            date=future_date,
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
                ),
                FlightSearchParams(
                    segments=[
                        server.FlightSearchSegmentParams(
                            origin="INVALID",
                            destination="LHR",
                            date=future_date,
                        )
                    ]
                ),
            ]
        )

        assert result["results"][0]["index"] == 0
        assert result["results"][1]["index"] == 1
        assert result["results"][1]["trip_type"] == "MULTI_CITY"
        assert result["results"][1]["count"] == 2
        assert len(result["results"][1]["flights"][0]["legs"]) == 3
        assert result["results"][2]["index"] == 2
        assert result["count"] == 3

    def test_batch_search_accepts_typed_query_models(self, monkeypatch):
        """Typed batch items should execute without reshaping the response."""
        monkeypatch.setattr(server, "SearchFlights", FakeSearchFlights)
        future_date = get_future_date(30)

        result = search_flights_batch(
            queries=[
                FlightSearchParams(
                    segments=[
                        server.FlightSearchSegmentParams(
                            origin="JFK",
                            destination="LHR",
                            date=future_date,
                        )
                    ],
                    num_cabin_luggage=1,
                ),
                FlightSearchParams(
                    segments=[
                        server.FlightSearchSegmentParams(
                            origin="JFK",
                            destination="LAX",
                            date=future_date,
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
                ),
            ]
        )

        assert result["success"] is True
        assert result["failed"] == 0
        assert result["count"] == 2
        assert result["results"][0]["index"] == 0
        assert result["results"][1]["trip_type"] == "MULTI_CITY"

    def test_search_flights_batch_schema_exposes_nested_query_fields(self):
        """Batch tool schema should expose FlightSearchParams, not untyped objects."""
        tools = asyncio.run(mcp.list_tools())
        batch = next(tool for tool in tools if tool.name == "search_flights_batch")

        serialized_schema = json.dumps(batch.inputSchema)
        assert "FlightSearchParams" in serialized_schema
        assert "Num Cabin Luggage" in serialized_schema
        assert '"additionalProperties": true' not in serialized_schema

    def test_effective_batch_parallelism_caps_round_trip_queries_only(self):
        """Only round-trip batches should have reduced parallelism."""
        one_way = FlightSearchParams(
            segments=[
                server.FlightSearchSegmentParams(
                    origin="JFK",
                    destination="LHR",
                    date=get_future_date(30),
                )
            ]
        )
        multi_city = FlightSearchParams(
            segments=[
                server.FlightSearchSegmentParams(
                    origin="JFK",
                    destination="LHR",
                    date=get_future_date(30),
                ),
                server.FlightSearchSegmentParams(
                    origin="LHR",
                    destination="CDG",
                    date=get_future_date(33),
                ),
                server.FlightSearchSegmentParams(
                    origin="CDG",
                    destination="JFK",
                    date=get_future_date(36),
                ),
            ]
        )
        round_trip = FlightSearchParams(
            segments=[
                server.FlightSearchSegmentParams(
                    origin="JFK",
                    destination="LHR",
                    date=get_future_date(30),
                ),
                server.FlightSearchSegmentParams(
                    origin="LHR",
                    destination="JFK",
                    date=get_future_date(37),
                ),
            ]
        )

        assert _effective_batch_parallelism([(0, one_way)], 8) == 8
        assert _effective_batch_parallelism([(0, multi_city)], 8) == 8
        assert _effective_batch_parallelism([(0, round_trip)], 8) == 3

    def test_batch_search_handles_100_multi_city_queries_under_parallel_execution(
        self, monkeypatch
    ):
        """Exact-date multi-city batches should retain requested parallelism."""

        def fake_execute_flight_search(params):
            time.sleep(0.02)
            return {
                "success": True,
                "flights": [{"price": float(len(params.segments)), "legs": []}],
                "count": 1,
                "trip_type": server._determine_trip_type(params.segments).name,
            }

        monkeypatch.setattr(execution, "_execute_flight_search", fake_execute_flight_search)
        future_date = get_future_date(30)
        queries = [
            FlightSearchParams(
                segments=[
                    server.FlightSearchSegmentParams(
                        origin="JFK",
                        destination="LAX",
                        date=future_date,
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
            for _ in range(100)
        ]

        start = time.perf_counter()
        result = search_flights_batch(queries=queries, parallelism=8)
        elapsed = time.perf_counter() - start

        assert result["success"] is True
        assert result["failed"] == 0
        assert result["count"] == 100
        assert result["parallelism"] == 8
        assert result["results"][0]["index"] == 0
        assert result["results"][-1]["index"] == 99
        assert elapsed < 60
        assert elapsed < 1.5

    def test_batch_search_handles_rome_style_journey_matrix(self, monkeypatch):
        """Rome-style itinerary matrices should stay in one fast, strongly typed batch."""
        captured_params: list[FlightSearchParams] = []

        def fake_execute_flight_search(params):
            captured_params.append(params)
            first_segment = params.segments[0]
            second_segment = params.segments[1]
            return {
                "success": True,
                "flights": [
                    {
                        "price": float(len(captured_params)),
                        "legs": [],
                        "segment_prices": [
                            {
                                "origin": first_segment.origin,
                                "destination": first_segment.destination,
                            },
                            {
                                "origin": second_segment.origin,
                                "destination": second_segment.destination,
                            },
                        ],
                    }
                ],
                "count": 1,
                "trip_type": server._determine_trip_type(params.segments).name,
            }

        monkeypatch.setattr(execution, "_execute_flight_search", fake_execute_flight_search)
        queries = [
            FlightSearchParams(
                segments=[
                    server.FlightSearchSegmentParams(
                        origin=origin,
                        destination="FCO",
                        date=outbound_date,
                    ),
                    server.FlightSearchSegmentParams(
                        origin="FCO",
                        destination=italy_destination,
                        date=onward_date,
                    ),
                    server.FlightSearchSegmentParams(
                        origin=italy_destination,
                        destination="BER",
                        date="2026-05-18",
                    ),
                ],
                max_stops="ANY",
                sort_by="CHEAPEST",
                num_cabin_luggage=1,
            )
            for origin in ("DUS", "BER", "HAM", "CGN")
            for outbound_date in ("2026-05-08", "2026-05-09", "2026-05-10")
            for italy_destination in ("TRS", "VCE", "TSF")
            for onward_date in ("2026-05-12", "2026-05-13")
        ]

        result = search_flights_batch(queries=queries, parallelism=8)

        assert len(queries) == 72
        assert result["success"] is True
        assert result["failed"] == 0
        assert result["count"] == 72
        assert result["parallelism"] == 8
        assert len(captured_params) == 72
        assert all(params.num_cabin_luggage == 1 for params in captured_params)
        assert all(server._determine_trip_type(params.segments) == server.TripType.MULTI_CITY for params in captured_params)
        assert result["results"][0]["index"] == 0
        assert result["results"][-1]["index"] == 71

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
