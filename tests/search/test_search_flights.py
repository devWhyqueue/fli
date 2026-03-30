"""Tests for Search class."""

import json
from datetime import datetime, timedelta

import pytest
from tenacity import retry, stop_after_attempt, wait_exponential

from fli.models import (
    Airline,
    Airport,
    FlightLeg,
    FlightResult,
    FlightSearchFilters,
    FlightSegment,
    MaxStops,
    PassengerInfo,
    SeatType,
    SortBy,
)
from fli.models.google_flights.base import TripType
from fli.search import SearchFlights

pytestmark_live = pytest.mark.live


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
def search_with_retry(search: SearchFlights, search_params):
    """Search with retry logic for flaky API responses."""
    results = search.search(search_params)
    if not results:
        raise ValueError("Empty results, retrying...")
    return results


@pytest.fixture
def search():
    """Create a reusable Search instance."""
    return SearchFlights()


@pytest.fixture
def basic_search_params():
    """Create basic search params for testing."""
    today = datetime.now()
    future_date = today + timedelta(days=30)
    return FlightSearchFilters(
        passenger_info=PassengerInfo(
            adults=1,
            children=0,
            infants_in_seat=0,
            infants_on_lap=0,
        ),
        flight_segments=[
            FlightSegment(
                departure_airport=[[Airport.PHX, 0]],
                arrival_airport=[[Airport.SFO, 0]],
                travel_date=future_date.strftime("%Y-%m-%d"),
            )
        ],
        stops=MaxStops.NON_STOP,
        seat_type=SeatType.ECONOMY,
        sort_by=SortBy.CHEAPEST,
    )


@pytest.fixture
def complex_search_params():
    """Create more complex search params for testing."""
    today = datetime.now()
    future_date = today + timedelta(days=60)
    return FlightSearchFilters(
        passenger_info=PassengerInfo(
            adults=2,
            children=1,
            infants_in_seat=0,
            infants_on_lap=1,
        ),
        flight_segments=[
            FlightSegment(
                departure_airport=[[Airport.JFK, 0]],
                arrival_airport=[[Airport.LAX, 0]],
                travel_date=future_date.strftime("%Y-%m-%d"),
            )
        ],
        stops=MaxStops.ONE_STOP_OR_FEWER,
        seat_type=SeatType.FIRST,
        sort_by=SortBy.TOP_FLIGHTS,
    )


@pytest.fixture
def round_trip_search_params():
    """Create basic round trip search params for testing."""
    today = datetime.now()
    outbound_date = today + timedelta(days=30)
    return_date = outbound_date + timedelta(days=7)

    return FlightSearchFilters(
        passenger_info=PassengerInfo(
            adults=1,
            children=0,
            infants_in_seat=0,
            infants_on_lap=0,
        ),
        flight_segments=[
            FlightSegment(
                departure_airport=[[Airport.SFO, 0]],
                arrival_airport=[[Airport.JFK, 0]],
                travel_date=outbound_date.strftime("%Y-%m-%d"),
            ),
            FlightSegment(
                departure_airport=[[Airport.JFK, 0]],
                arrival_airport=[[Airport.SFO, 0]],
                travel_date=return_date.strftime("%Y-%m-%d"),
            ),
        ],
        stops=MaxStops.NON_STOP,
        seat_type=SeatType.ECONOMY,
        sort_by=SortBy.CHEAPEST,
        trip_type=TripType.ROUND_TRIP,
    )


@pytest.fixture
def complex_round_trip_params():
    """Create more complex round trip search params for testing."""
    today = datetime.now()
    outbound_date = today + timedelta(days=60)
    return_date = outbound_date + timedelta(days=14)

    return FlightSearchFilters(
        passenger_info=PassengerInfo(
            adults=2,
            children=1,
            infants_in_seat=0,
            infants_on_lap=1,
        ),
        flight_segments=[
            FlightSegment(
                departure_airport=[[Airport.LAX, 0]],
                arrival_airport=[[Airport.ORD, 0]],
                travel_date=outbound_date.strftime("%Y-%m-%d"),
            ),
            FlightSegment(
                departure_airport=[[Airport.ORD, 0]],
                arrival_airport=[[Airport.LAX, 0]],
                travel_date=return_date.strftime("%Y-%m-%d"),
            ),
        ],
        stops=MaxStops.ONE_STOP_OR_FEWER,
        seat_type=SeatType.BUSINESS,
        sort_by=SortBy.TOP_FLIGHTS,
        trip_type=TripType.ROUND_TRIP,
    )


@pytest.mark.parametrize(
    "search_params_fixture",
    [
        "basic_search_params",
        "complex_search_params",
    ],
)
@pytestmark_live
def test_search_functionality(search, search_params_fixture, request):
    """Test flight search functionality with different data sets."""
    search_params = request.getfixturevalue(search_params_fixture)
    results = search.search(search_params)
    assert isinstance(results, list)


@pytestmark_live
def test_multiple_searches(search, basic_search_params, complex_search_params):
    """Test performing multiple searches with the same Search instance."""
    # First search
    results1 = search.search(basic_search_params)
    assert isinstance(results1, list)

    # Second search with different data
    results2 = search.search(complex_search_params)
    assert isinstance(results2, list)

    # Third search reusing first search data
    results3 = search.search(basic_search_params)
    assert isinstance(results3, list)


@pytestmark_live
def test_basic_round_trip_search(search, round_trip_search_params):
    """Test basic round trip flight search functionality."""
    results = search.search(round_trip_search_params)
    assert isinstance(results, list)
    assert len(results) > 0

    # Check that results contain tuples of outbound and return flights
    for outbound, return_flight in results:
        # Verify outbound flight
        assert outbound.legs[0].departure_airport == Airport.SFO
        assert outbound.legs[-1].arrival_airport == Airport.JFK

        # Verify return flight
        assert return_flight.legs[0].departure_airport == Airport.JFK
        assert return_flight.legs[-1].arrival_airport == Airport.SFO


@pytestmark_live
def test_complex_round_trip_search(search, complex_round_trip_params):
    """Test complex round trip flight search with multiple passengers and stops."""
    results = search.search(complex_round_trip_params)
    assert isinstance(results, list)
    assert len(results) > 0

    # Check that results contain tuples of outbound and return flights
    for outbound, return_flight in results:
        # Verify outbound flight
        assert outbound.legs[0].departure_airport == Airport.LAX
        assert outbound.legs[-1].arrival_airport == Airport.ORD
        assert outbound.stops <= MaxStops.ONE_STOP_OR_FEWER.value

        # Verify return flight
        assert return_flight.legs[0].departure_airport == Airport.ORD
        assert return_flight.legs[-1].arrival_airport == Airport.LAX
        assert return_flight.stops <= MaxStops.ONE_STOP_OR_FEWER.value


@pytestmark_live
def test_round_trip_with_selected_outbound(search, round_trip_search_params):
    """Test round trip search with a pre-selected outbound flight."""
    # First get outbound flights
    initial_results = search.search(round_trip_search_params)
    assert len(initial_results) > 0

    # Select first outbound flight and search for returns
    selected_outbound = initial_results[0][0]  # Get first outbound flight
    round_trip_search_params.flight_segments[0].selected_flight = selected_outbound

    return_results = search.search(round_trip_search_params)
    assert isinstance(return_results, list)
    assert len(return_results) > 0

    # Verify all return flights match the selected outbound
    for return_flight in return_results:
        assert return_flight.legs[0].departure_airport == Airport.JFK
        assert return_flight.legs[-1].arrival_airport == Airport.SFO


def test_multicity_search_returns_direct_results_without_round_trip_chaining(monkeypatch):
    """Test multi-city searches use one native request and return direct results."""
    future_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    search = SearchFlights()
    filters = FlightSearchFilters(
        trip_type=TripType.MULTI_CITY,
        passenger_info=PassengerInfo(adults=1),
        flight_segments=[
            FlightSegment(
                departure_airport=[[Airport.JFK, 0]],
                arrival_airport=[[Airport.LAX, 0]],
                travel_date=future_date,
            ),
            FlightSegment(
                departure_airport=[[Airport.LAX, 0]],
                arrival_airport=[[Airport.SFO, 0]],
                travel_date=(datetime.now() + timedelta(days=33)).strftime("%Y-%m-%d"),
            ),
            FlightSegment(
                departure_airport=[[Airport.SFO, 0]],
                arrival_airport=[[Airport.JFK, 0]],
                travel_date=(datetime.now() + timedelta(days=36)).strftime("%Y-%m-%d"),
            ),
        ],
        stops=MaxStops.ANY,
        seat_type=SeatType.ECONOMY,
        sort_by=SortBy.CHEAPEST,
    )
    post_calls = 0

    class FakeResponse:
        text = ")]}'" + json.dumps([[None, None, json.dumps([None, None, [[["flight"]]], None])]])

        @staticmethod
        def raise_for_status():
            return None

    def fake_post(**kwargs):
        nonlocal post_calls
        post_calls += 1
        return FakeResponse()

    monkeypatch.setattr(search.client, "post", fake_post)
    monkeypatch.setattr(
        SearchFlights,
        "_parse_flights_data",
        staticmethod(
            lambda data: FlightResult(
                price=123.0,
                duration=180,
                stops=0,
                legs=[
                    FlightLeg(
                        airline=Airline.AA,
                        flight_number="AA100",
                        departure_airport=Airport.JFK,
                        arrival_airport=Airport.SFO,
                        departure_datetime=datetime.now() + timedelta(days=30),
                        arrival_datetime=datetime.now() + timedelta(days=30, hours=5),
                        duration=300,
                    )
                ],
            )
        ),
    )

    results = search.search(filters)

    assert isinstance(results, list)
    assert len(results) == 1
    assert not isinstance(results[0], tuple)
    assert post_calls == 1


def test_multicity_search_uses_native_results_without_recombining(monkeypatch):
    """Test multi-city searches delegate once to the native one-way request path."""
    search = SearchFlights()
    base_time = datetime.now() + timedelta(days=30)
    filters = FlightSearchFilters(
        trip_type=TripType.MULTI_CITY,
        passenger_info=PassengerInfo(adults=1),
        flight_segments=[
            FlightSegment(
                departure_airport=[[Airport.JFK, 0]],
                arrival_airport=[[Airport.LAX, 0]],
                travel_date=(base_time).strftime("%Y-%m-%d"),
            ),
            FlightSegment(
                departure_airport=[[Airport.LAX, 0]],
                arrival_airport=[[Airport.SFO, 0]],
                travel_date=(base_time + timedelta(days=3)).strftime("%Y-%m-%d"),
            ),
            FlightSegment(
                departure_airport=[[Airport.SFO, 0]],
                arrival_airport=[[Airport.JFK, 0]],
                travel_date=(base_time + timedelta(days=6)).strftime("%Y-%m-%d"),
            ),
        ],
        stops=MaxStops.ANY,
        seat_type=SeatType.ECONOMY,
        sort_by=SortBy.CHEAPEST,
    )
    native_result = FlightResult(
        price=360.0,
        duration=540,
        stops=0,
        legs=[
            FlightLeg(
                airline=Airline.AA,
                flight_number="AA100",
                departure_airport=Airport.JFK,
                arrival_airport=Airport.LAX,
                departure_datetime=base_time,
                arrival_datetime=base_time + timedelta(hours=3),
                duration=180,
            ),
            FlightLeg(
                airline=Airline.AA,
                flight_number="AA101",
                departure_airport=Airport.LAX,
                arrival_airport=Airport.SFO,
                departure_datetime=base_time + timedelta(days=3),
                arrival_datetime=base_time + timedelta(days=3, hours=3),
                duration=180,
            ),
            FlightLeg(
                airline=Airline.AA,
                flight_number="AA102",
                departure_airport=Airport.SFO,
                arrival_airport=Airport.JFK,
                departure_datetime=base_time + timedelta(days=6),
                arrival_datetime=base_time + timedelta(days=6, hours=3),
                duration=180,
            ),
        ],
        segment_prices=[100.0, 120.0, 140.0],
    )
    call_filters: list[FlightSearchFilters] = []

    def fake_search_one_way(received_filters):
        call_filters.append(received_filters)
        return [native_result]

    monkeypatch.setattr(search, "_search_one_way", fake_search_one_way)

    results = search.search(filters)

    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0] is native_result
    assert len(call_filters) == 1
    assert call_filters[0] is filters


def test_native_multi_city_workflow_auto_picks_cheapest(monkeypatch):
    """Native multi-city workflow should pick the cheapest option at each step."""
    search = SearchFlights()
    base_time = datetime.now() + timedelta(days=30)
    filters = FlightSearchFilters(
        trip_type=TripType.MULTI_CITY,
        passenger_info=PassengerInfo(adults=1),
        flight_segments=[
            FlightSegment(
                departure_airport=[[Airport.JFK, 0]],
                arrival_airport=[[Airport.LAX, 0]],
                travel_date=base_time.strftime("%Y-%m-%d"),
            ),
            FlightSegment(
                departure_airport=[[Airport.LAX, 0]],
                arrival_airport=[[Airport.SFO, 0]],
                travel_date=(base_time + timedelta(days=3)).strftime("%Y-%m-%d"),
            ),
            FlightSegment(
                departure_airport=[[Airport.SFO, 0]],
                arrival_airport=[[Airport.JFK, 0]],
                travel_date=(base_time + timedelta(days=6)).strftime("%Y-%m-%d"),
            ),
        ],
        stops=MaxStops.ANY,
        seat_type=SeatType.ECONOMY,
        sort_by=SortBy.CHEAPEST,
    )

    def make_result(
        departure_airport: Airport,
        arrival_airport: Airport,
        departure_time: datetime,
        price: float,
        flight_number: str,
    ) -> FlightResult:
        return FlightResult(
            price=price,
            duration=180,
            stops=0,
            legs=[
                FlightLeg(
                    airline=Airline.AA,
                    flight_number=flight_number,
                    departure_airport=departure_airport,
                    arrival_airport=arrival_airport,
                    departure_datetime=departure_time,
                    arrival_datetime=departure_time + timedelta(hours=3),
                    duration=180,
                )
            ],
        )

    sequence = [
        [
            make_result(Airport.JFK, Airport.LAX, base_time, 300.0, "AA300"),
            make_result(Airport.JFK, Airport.LAX, base_time, 200.0, "AA200"),
        ],
        [
            make_result(Airport.LAX, Airport.SFO, base_time + timedelta(days=3), 450.0, "AA450"),
            make_result(Airport.LAX, Airport.SFO, base_time + timedelta(days=3), 350.0, "AA350"),
        ],
        [
            make_result(Airport.SFO, Airport.JFK, base_time + timedelta(days=6), 600.0, "AA600"),
            make_result(Airport.SFO, Airport.JFK, base_time + timedelta(days=6), 500.0, "AA500"),
        ],
    ]
    observed_selected_flights: list[list[FlightResult | None]] = []

    def fake_search_one_way(received_filters):
        observed_selected_flights.append(
            [segment.selected_flight for segment in received_filters.flight_segments]
        )
        return sequence[len(observed_selected_flights) - 1]

    monkeypatch.setattr(search, "_search_one_way", fake_search_one_way)

    result = search.search_multi_city_native(filters)

    assert result is not None
    assert result.final_price == 500.0
    assert result.segment_prices is None
    assert [step.displayed_price for step in result.step_trace] == [200.0, 350.0, 500.0]
    assert len(result.completed_itinerary.legs) == 3
    assert observed_selected_flights[0] == [None, None, None]
    assert observed_selected_flights[1][0] is not None
    assert observed_selected_flights[1][1] is None
    assert observed_selected_flights[2][1] is not None


@pytest.mark.parametrize(
    "search_params_fixture",
    [
        "round_trip_search_params",
        "complex_round_trip_params",
    ],
)
@pytestmark_live
def test_round_trip_result_structure(search, search_params_fixture, request):
    """Test the structure of round trip search results with different parameters."""
    search_params = request.getfixturevalue(search_params_fixture)
    results = search_with_retry(search, search_params)

    assert isinstance(results, list)
    assert len(results) > 0

    for result in results:
        assert isinstance(result, tuple)
        assert len(result) == 2
        outbound, return_flight = result

        # Verify both flights have the expected structure
        for flight in (outbound, return_flight):
            assert hasattr(flight, "price")
            assert hasattr(flight, "duration")
            assert hasattr(flight, "stops")
            assert hasattr(flight, "legs")
            assert len(flight.legs) > 0


class TestParsePrice:
    """Tests for _parse_price method handling missing/malformed price data."""

    def test_parse_price_valid_data(self):
        """Test _parse_price with valid price data."""
        data = [None, [[100, 200, 299.99]]]
        assert SearchFlights._parse_price(data) == 299.99

    def test_parse_price_empty_inner_list(self):
        """Test _parse_price returns 0.0 when inner price list is empty."""
        data = [None, [[]]]
        assert SearchFlights._parse_price(data) == 0.0

    def test_parse_price_empty_outer_list(self):
        """Test _parse_price returns 0.0 when outer price list is empty."""
        data = [None, []]
        assert SearchFlights._parse_price(data) == 0.0

    def test_parse_price_none_price_section(self):
        """Test _parse_price returns 0.0 when price section is None."""
        data = [None, None]
        assert SearchFlights._parse_price(data) == 0.0

    def test_parse_price_missing_price_section(self):
        """Test _parse_price returns 0.0 when data has no price section."""
        data = [None]
        assert SearchFlights._parse_price(data) == 0.0

    def test_parse_price_inner_list_none(self):
        """Test _parse_price returns 0.0 when inner list is None."""
        data = [None, [None]]
        assert SearchFlights._parse_price(data) == 0.0


class TestSelectionToken:
    """Tests for native multi-city selection token extraction and serialization."""

    def test_parse_selection_token_valid_data(self):
        """The hidden per-option token should be decoded from the option payload."""
        data = [
            None,
            [
                [None, 1400],
                "CjRIWm1NTWdvWUNkSlFBRFlxNVFCRy0tLS0tLS0tLS1lZmcyNEFBQUFBR25LUG5NS3lQSzhBEg1TTjI1OTJ8U04yMDkzGgsI1sUIEAIaA0VVUjgccI7pCQ==",
            ],
        ]

        assert (
            SearchFlights._parse_selection_token(data)
            == "HZmMMgoYCdJQADYq5QBG----------efg24AAAAAGnKPnMKyPK8A"
        )

    def test_parse_selection_token_missing_data(self):
        """Malformed option payloads should not crash token extraction."""
        assert SearchFlights._parse_selection_token([None, None]) is None

    def test_selected_flight_serialization_includes_selection_token(self):
        """Stepwise follow-up requests should preserve the opaque selection token."""
        departure_time = datetime.now() + timedelta(days=30)
        filters = FlightSearchFilters(
            trip_type=TripType.MULTI_CITY,
            passenger_info=PassengerInfo(adults=1),
            flight_segments=[
                FlightSegment(
                    departure_airport=[[Airport.JFK, 0]],
                    arrival_airport=[[Airport.LAX, 0]],
                    travel_date=departure_time.strftime("%Y-%m-%d"),
                    selected_flight=FlightResult(
                        price=200.0,
                        duration=180,
                        stops=0,
                        selection_token="opaque-token",
                        legs=[
                            FlightLeg(
                                airline=Airline.AA,
                                flight_number="AA200",
                                departure_airport=Airport.JFK,
                                arrival_airport=Airport.LAX,
                                departure_datetime=departure_time,
                                arrival_datetime=departure_time + timedelta(hours=3),
                                duration=180,
                            )
                        ],
                    ),
                ),
                FlightSegment(
                    departure_airport=[[Airport.LAX, 0]],
                    arrival_airport=[[Airport.SFO, 0]],
                    travel_date=(departure_time + timedelta(days=3)).strftime("%Y-%m-%d"),
                ),
            ],
        )

        formatted = filters.format()

        assert formatted[1][13][0][8][0][3] == "opaque-token"
