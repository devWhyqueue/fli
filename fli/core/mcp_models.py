"""MCP request models."""

from datetime import datetime

from pydantic import BaseModel, Field


class FlightSearchSegmentParams(BaseModel):
    """A single exact-date itinerary segment."""

    origin: str = Field(description="Departure airport IATA code (e.g., 'JFK')")
    destination: str = Field(description="Arrival airport IATA code (e.g., 'LHR')")
    date: str = Field(description="Travel date in YYYY-MM-DD format")


class JourneySearchSegmentParams(BaseModel):
    """A segment with one or many exact-date option values."""

    origin: str | list[str] = Field(
        description="Departure airport IATA code or codes (e.g., 'JFK' or ['JFK', 'EWR'])"
    )
    destination: str | list[str] = Field(
        description="Arrival airport IATA code or codes (e.g., 'LHR' or ['LHR', 'LGW'])"
    )
    date: str | list[str] = Field(
        description=(
            "Travel date or dates in YYYY-MM-DD format "
            "(e.g., '2026-05-10' or ['2026-05-10', '2026-05-11'])"
        )
    )

    def model_post_init(self, _context: object) -> None:
        """Validate option lists and date formats."""
        _validate_option_values(self.origin, "origin")
        _validate_option_values(self.destination, "destination")
        for value in _coerce_to_list(self.date):
            datetime.strptime(value, "%Y-%m-%d")


class DateSearchSegmentParams(BaseModel):
    """A segment template for flexible date scans."""

    origin: str = Field(description="Departure airport IATA code (e.g., 'JFK')")
    destination: str = Field(description="Arrival airport IATA code (e.g., 'LHR')")
    day_offset: int | None = Field(
        None,
        ge=0,
        description="Days after the first segment's departure date; omit or use 0 for segment 1",
    )


class FlightSearchParams(BaseModel):
    """Parameters for searching flights on a specific date."""

    segments: list[FlightSearchSegmentParams] = Field(
        description="Ordered itinerary segments with explicit IATA airports and dates",
        min_length=1,
        max_length=6,
    )
    departure_window: str | None = Field(
        None,
        description=(
            "Deprecated alias for departure_time_window in 'HH-HH' 24h format (e.g., '6-20')"
        ),
    )
    departure_time_window: str | None = Field(
        None, description="Preferred departure time window in 'HH-HH' 24h format (e.g., '6-20')"
    )
    arrival_time_window: str | None = Field(
        None, description="Preferred arrival time window in 'HH-HH' 24h format (e.g., '8-22')"
    )
    airlines: list[str] | None = Field(
        None, description="Filter by airline IATA codes (e.g., ['BA', 'AA'])"
    )
    cabin_class: str = Field(
        "ECONOMY",
        description="Cabin class: ECONOMY, PREMIUM_ECONOMY, BUSINESS, or FIRST",
    )
    max_stops: str = Field(
        "ANY", description="Maximum stops: ANY, NON_STOP, ONE_STOP, or TWO_PLUS_STOPS"
    )
    sort_by: str = Field(
        "CHEAPEST",
        description="Sort results by: CHEAPEST, DURATION, DEPARTURE_TIME, or ARRIVAL_TIME",
    )
    passengers: int = Field(1, ge=1, description="Number of adult passengers")
    num_cabin_luggage: int | None = Field(
        None, ge=0, le=2, description="Number of cabin luggage pieces to include in fare pricing"
    )
    duration: int | None = Field(
        None, ge=1, description="Maximum total itinerary duration in minutes"
    )
    max_layover_time: int | None = Field(
        None,
        ge=1,
        description="Maximum layover duration in minutes within each searched segment",
    )

    def model_post_init(self, _context: object) -> None:
        """Validate exact-date segment ordering."""
        _validate_segment_count(len(self.segments))
        travel_dates = [
            datetime.strptime(segment.date, "%Y-%m-%d").date() for segment in self.segments
        ]
        if travel_dates != sorted(travel_dates):
            raise ValueError("Flight-search segment dates must be non-decreasing")


class JourneySearchParams(BaseModel):
    """Parameters for finding the cheapest journey across exact option sets."""

    segments: list[JourneySearchSegmentParams] = Field(
        description="Ordered itinerary segments with exact airport/date option sets",
        min_length=1,
        max_length=6,
    )
    departure_window: str | None = Field(
        None,
        description=(
            "Deprecated alias for departure_time_window in 'HH-HH' 24h format (e.g., '6-20')"
        ),
    )
    departure_time_window: str | None = Field(
        None, description="Preferred departure time window in 'HH-HH' 24h format (e.g., '6-20')"
    )
    arrival_time_window: str | None = Field(
        None, description="Preferred arrival time window in 'HH-HH' 24h format (e.g., '8-22')"
    )
    airlines: list[str] | None = Field(
        None, description="Filter by airline IATA codes (e.g., ['BA', 'AA'])"
    )
    cabin_class: str = Field(
        "ECONOMY",
        description="Cabin class: ECONOMY, PREMIUM_ECONOMY, BUSINESS, or FIRST",
    )
    max_stops: str = Field(
        "ANY", description="Maximum stops: ANY, NON_STOP, ONE_STOP, or TWO_PLUS_STOPS"
    )
    sort_by: str = Field(
        "CHEAPEST",
        description="Sort by: CHEAPEST, DURATION, DEPARTURE_TIME, or ARRIVAL_TIME",
    )
    passengers: int = Field(1, ge=1, description="Number of adult passengers")
    num_cabin_luggage: int | None = Field(
        None, ge=0, le=2, description="Number of cabin luggage pieces to include in fare pricing"
    )
    duration: int | None = Field(
        None, ge=1, description="Maximum total itinerary duration in minutes"
    )
    max_layover_time: int | None = Field(
        None,
        ge=1,
        description="Maximum layover duration in minutes within each searched segment",
    )
    top_n: int = Field(10, ge=1, description="Number of cheapest complete journeys to return")

    def model_post_init(self, _context: object) -> None:
        """Validate supported itinerary lengths."""
        _validate_segment_count(len(self.segments))


class DateSearchParams(BaseModel):
    """Parameters for finding the cheapest travel dates within a range."""

    segments: list[DateSearchSegmentParams] = Field(
        description=(
            "Ordered itinerary segments; segment 1 uses the scanned date and later "
            "segments use day_offset"
        ),
        min_length=1,
        max_length=6,
    )
    start_date: str = Field(description="Start of date range in YYYY-MM-DD format")
    end_date: str = Field(description="End of date range in YYYY-MM-DD format")
    airlines: list[str] | None = Field(
        None, description="Filter by airline IATA codes (e.g., ['BA', 'AA'])"
    )
    cabin_class: str = Field(
        "ECONOMY",
        description="Cabin class: ECONOMY, PREMIUM_ECONOMY, BUSINESS, or FIRST",
    )
    max_stops: str = Field(
        "ANY", description="Maximum stops: ANY, NON_STOP, ONE_STOP, or TWO_PLUS_STOPS"
    )
    departure_window: str | None = Field(
        None, description="Preferred departure time window in 'HH-HH' 24h format (e.g., '6-20')"
    )
    arrival_time_window: str | None = Field(
        None, description="Preferred arrival time window in 'HH-HH' 24h format (e.g., '8-22')"
    )
    sort_by_price: bool = Field(False, description="Sort results by price (lowest first)")
    passengers: int = Field(1, ge=1, description="Number of adult passengers")

    def model_post_init(self, _context: object) -> None:
        """Validate the segment template shape."""
        _validate_segment_count(len(self.segments))
        if self.segments[0].day_offset not in (None, 0):
            raise ValueError("First date-search segment cannot define a non-zero day_offset")
        offsets = [0]
        offsets.extend(
            _require_day_offset(index, segment)
            for index, segment in enumerate(self.segments[1:], start=1)
        )
        if offsets != sorted(offsets):
            raise ValueError("Date-search segment day_offset values must be non-decreasing")


def _require_day_offset(index: int, segment: DateSearchSegmentParams) -> int:
    """Return a required day_offset value for non-anchor date-search segments."""
    if segment.day_offset is None:
        raise ValueError(f"Segment {index + 1} must define day_offset")
    return segment.day_offset


def _coerce_to_list(value: str | list[str]) -> list[str]:
    """Normalize a scalar or list field into a non-empty list."""
    return [value] if isinstance(value, str) else value


def _validate_option_values(value: str | list[str], field_name: str) -> None:
    """Validate that an option-set field contains at least one non-empty value."""
    values = _coerce_to_list(value)
    if not values:
        raise ValueError(f"Journey-search {field_name} options cannot be empty")
    if any(not item for item in values):
        raise ValueError(f"Journey-search {field_name} options cannot contain empty values")


def _validate_segment_count(segment_count: int) -> None:
    """Validate supported itinerary lengths."""
    if segment_count < 1:
        raise ValueError("At least one segment is required")
    if segment_count > 6:
        raise ValueError("No more than 6 segments are supported")
