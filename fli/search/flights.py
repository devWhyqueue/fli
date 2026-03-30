"""Flight search implementation.

This module provides the core flight search functionality, interfacing directly
with Google Flights' API to find available flights and their details.
"""

import json
from itertools import product
from copy import deepcopy
from datetime import datetime

from fli.models import (
    Airline,
    Airport,
    FlightLeg,
    FlightResult,
    FlightSearchFilters,
    SortBy,
)
from fli.models.google_flights.base import TripType
from fli.search.client import get_client


class SearchFlights:
    """Flight search implementation using Google Flights' API.

    This class handles searching for specific flights with detailed filters,
    parsing the results into structured data models.
    """

    BASE_URL = "https://www.google.com/_/FlightsFrontendUi/data/travel.frontend.flights.FlightsFrontendService/GetShoppingResults"
    DEFAULT_HEADERS = {
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
    }

    def __init__(self):
        """Initialize the search client for flight searches."""
        self.client = get_client()

    def search(
        self, filters: FlightSearchFilters, top_n: int = 5
    ) -> list[FlightResult | tuple[FlightResult, FlightResult]] | None:
        """Search for flights using the given FlightSearchFilters.

        Args:
            filters: Full flight search object including airports, dates, and preferences
            top_n: Number of flights to limit the return flight search to

        Returns:
            List of FlightResult objects containing flight details, or None if no results

        Raises:
            Exception: If the search fails or returns invalid data

        """
        try:
            if filters.trip_type == TripType.MULTI_CITY:
                return self._search_multi_city(filters, top_n=top_n)

            flights = self._search_one_way(filters)

            if (
                filters.trip_type != TripType.ROUND_TRIP
                or filters.flight_segments[0].selected_flight is not None
            ):
                return flights

            # Get the return flights if round-trip
            flight_pairs = []
            # Call the search again with the return flight data
            for selected_flight in flights[:top_n]:
                selected_flight_filters = deepcopy(filters)
                selected_flight_filters.flight_segments[0].selected_flight = selected_flight
                return_flights = self.search(selected_flight_filters, top_n=top_n)
                if return_flights is not None:
                    flight_pairs.extend(
                        (selected_flight, return_flight) for return_flight in return_flights
                    )

            return flight_pairs

        except Exception as e:
            raise Exception(f"Search failed: {str(e)}") from e

    def _search_one_way(self, filters: FlightSearchFilters) -> list[FlightResult] | None:
        """Execute a single shopping request and parse the returned itineraries."""
        encoded_filters = filters.encode()
        response = self.client.post(
            url=self.BASE_URL,
            data=f"f.req={encoded_filters}",
            impersonate="chrome",
            allow_redirects=True,
        )
        response.raise_for_status()

        parsed = json.loads(response.text.lstrip(")]}'"))[0][2]
        if not parsed:
            return None

        encoded_filters = json.loads(parsed)
        flights_data = [
            item
            for i in [2, 3]
            if isinstance(encoded_filters[i], list)
            for item in encoded_filters[i][0]
        ]
        return [self._parse_flights_data(flight) for flight in flights_data]

    def _search_multi_city(
        self, filters: FlightSearchFilters, top_n: int = 5
    ) -> list[FlightResult] | None:
        """Search each segment independently and combine results into full itineraries."""
        segment_results: list[list[FlightResult]] = []
        for segment in filters.flight_segments:
            segment_filters = deepcopy(filters)
            segment_filters.trip_type = TripType.ONE_WAY
            segment_filters.flight_segments = [segment]
            results = self._search_one_way(segment_filters)
            if not results:
                return None
            segment_results.append(results[:top_n])

        combined_results = [
            self._combine_segment_results(result_group)
            for result_group in product(*segment_results)
        ]
        self._sort_combined_results(combined_results, filters)
        return combined_results[:top_n]

    @staticmethod
    def _combine_segment_results(results: tuple[FlightResult, ...]) -> FlightResult:
        """Combine segment-level results into a single itinerary."""
        return FlightResult(
            legs=[leg for result in results for leg in result.legs],
            price=sum(result.price for result in results),
            duration=sum(result.duration for result in results),
            stops=sum(result.stops for result in results),
            segment_prices=[result.price for result in results],
        )

    @staticmethod
    def _sort_combined_results(results: list[FlightResult], filters: FlightSearchFilters) -> None:
        """Sort combined multi-city itineraries using the requested ordering."""
        if filters.sort_by == SortBy.DURATION:
            results.sort(key=lambda item: item.duration)
            return
        if filters.sort_by == SortBy.DEPARTURE_TIME:
            results.sort(key=lambda item: item.legs[0].departure_datetime)
            return
        if filters.sort_by == SortBy.ARRIVAL_TIME:
            results.sort(key=lambda item: item.legs[-1].arrival_datetime)
            return
        results.sort(key=lambda item: item.price)

    @staticmethod
    def _parse_flights_data(data: list) -> FlightResult:
        """Parse raw flight data into a structured FlightResult.

        Args:
            data: Raw flight data from the API response

        Returns:
            Structured FlightResult object with all flight details

        """
        flight = FlightResult(
            price=SearchFlights._parse_price(data),
            duration=data[0][9],
            stops=len(data[0][2]) - 1,
            legs=[
                FlightLeg(
                    airline=SearchFlights._parse_airline(fl[22][0]),
                    flight_number=fl[22][1],
                    departure_airport=SearchFlights._parse_airport(fl[3]),
                    arrival_airport=SearchFlights._parse_airport(fl[6]),
                    departure_datetime=SearchFlights._parse_datetime(fl[20], fl[8]),
                    arrival_datetime=SearchFlights._parse_datetime(fl[21], fl[10]),
                    duration=fl[11],
                )
                for fl in data[0][2]
            ],
        )
        return flight

    @staticmethod
    def _parse_price(data: list) -> float:
        """Extract price from raw flight data.

        Args:
            data: Raw flight data from the API response

        Returns:
            Flight price, or 0.0 if price data is unavailable

        """
        try:
            if data[1] and data[1][0]:
                return data[1][0][-1]
        except (IndexError, TypeError):
            pass
        return 0.0

    @staticmethod
    def _parse_datetime(date_arr: list[int], time_arr: list[int]) -> datetime:
        """Convert date and time arrays to datetime.

        Args:
            date_arr: List of integers [year, month, day]
            time_arr: List of integers [hour, minute]

        Returns:
            Parsed datetime object

        Raises:
            ValueError: If arrays contain only None values

        """
        if not any(x is not None for x in date_arr) or not any(x is not None for x in time_arr):
            raise ValueError("Date and time arrays must contain at least one non-None value")

        return datetime(*(x or 0 for x in date_arr), *(x or 0 for x in time_arr))

    @staticmethod
    def _parse_airline(airline_code: str) -> Airline:
        """Convert airline code to Airline enum.

        Args:
            airline_code: Raw airline code from API

        Returns:
            Corresponding Airline enum value

        """
        if airline_code[0].isdigit():
            airline_code = f"_{airline_code}"
        return getattr(Airline, airline_code)

    @staticmethod
    def _parse_airport(airport_code: str) -> Airport:
        """Convert airport code to Airport enum.

        Args:
            airport_code: Raw airport code from API

        Returns:
            Corresponding Airport enum value

        """
        return getattr(Airport, airport_code)
