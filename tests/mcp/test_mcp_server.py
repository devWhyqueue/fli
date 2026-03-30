"""Tests for MCP server functionality."""

from datetime import datetime, timedelta

import fli.mcp.server as server
from fli.mcp.server import (
    DateSearchParams,
    FlightSearchParams,
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
        if filters.trip_type == server.TripType.MULTI_CITY:
            return [make_flight(Airport.JFK, Airport.SFO, 650.0)]
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
        """Test multi-city flight search."""
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
        assert result["count"] == 2

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
