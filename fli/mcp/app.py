"""Shared MCP app, configuration, and resource objects."""

import importlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp.types import (
    GetPromptResult,
    ListPromptsResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    Tool,
    ToolAnnotations,
)
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

try:
    FastMCP = importlib.import_module("fastmcp").FastMCP
    FastMCPTool = importlib.import_module("fastmcp.tools").Tool
except ImportError as exc:
    if "PyO3 modules compiled for CPython 3.8 or older" not in str(exc):
        raise
    FastMCP = importlib.import_module("fli.core.fastmcp_shim").FastMCP
    FastMCPTool = importlib.import_module("fli.core.fastmcp_shim").Tool


class FlightSearchConfig(BaseSettings):
    """Optional configuration for the Flight Search MCP server."""

    model_config = SettingsConfigDict(env_prefix="FLI_MCP_")

    default_passengers: int = Field(
        1,
        ge=1,
        description="Default number of adult passengers to include in searches.",
    )
    default_currency: str = Field(
        "USD",
        min_length=3,
        max_length=3,
        description="Three-letter currency code returned with search results.",
    )
    default_cabin_class: str = Field(
        "ECONOMY",
        description="Default cabin class used when none is provided.",
    )
    default_sort_by: str = Field(
        "CHEAPEST",
        description="Default sorting strategy for flight results.",
    )
    default_departure_window: str | None = Field(
        None,
        description="Optional default departure window in 'HH-HH' 24-hour format.",
    )
    max_results: int | None = Field(
        None,
        gt=0,
        description="Optional maximum number of results returned by each tool.",
    )


CONFIG = FlightSearchConfig()  # pyright: ignore[reportCallIssue]
CONFIG_SCHEMA = CONFIG.model_json_schema()


@dataclass
class PromptSpec:
    """Container for prompt metadata and builder."""

    description: str
    build_messages: Callable[[dict[str, str]], list[PromptMessage]]
    arguments: list[PromptArgument] | None = None


class FliMCP(FastMCP):  # pyright: ignore[reportGeneralTypeIssues]
    """Extended FastMCP server with prompt and annotation support."""

    def __init__(self, name: str | None = None, **settings: Any):
        """Initialize the MCP server with metadata tracking for tools and prompts."""
        self._tool_annotations: dict[str, ToolAnnotations] = {}
        self._prompts: dict[str, PromptSpec] = {}
        super().__init__(name=name, **settings)

    def _setup_handlers(self) -> None:
        """Register MCP protocol handlers including prompts."""
        super()._setup_handlers()
        self._mcp_server.list_tools()(self.list_tools)
        self._mcp_server.list_prompts()(self.list_prompts)  # pyright: ignore[reportArgumentType]
        self._mcp_server.get_prompt()(self.get_prompt)

    def add_tool(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        func: Callable,
        name: str | None = None,
        description: str | None = None,
        annotations: dict[str, Any] | ToolAnnotations | None = None,
    ) -> None:
        """Register a tool with optional annotations."""
        tool = FastMCPTool.from_function(fn=func, name=name, description=description)
        self._tool_manager.add_tool(tool)
        tool_name = name or func.__name__
        if annotations:
            self._tool_annotations[tool_name] = (
                annotations
                if isinstance(annotations, ToolAnnotations)
                else ToolAnnotations(**annotations)
            )

    def tool(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        name: str | None = None,
        description: str | None = None,
        annotations: dict[str, Any] | ToolAnnotations | None = None,
    ) -> Callable:
        """Register a tool with optional annotations."""
        if callable(name):
            raise TypeError(
                "The @tool decorator was used incorrectly. "
                "Did you forget to call it? Use @tool() instead of @tool"
            )

        def decorator(func: Callable) -> Callable:
            """Register the wrapped function as an MCP tool."""
            self.add_tool(func, name=name, description=description, annotations=annotations)
            return func

        return decorator

    async def list_tools(self) -> list[Tool]:
        """List all available tools with annotations."""
        tools = list((await self._tool_manager.get_tools()).values())
        return [
            Tool(
                name=info.name,
                description=info.description,
                inputSchema=info.parameters,
                annotations=self._tool_annotations.get(info.name),
            )
            for info in tools
        ]

    def add_prompt(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        name: str,
        description: str,
        *,
        arguments: list[PromptArgument] | None = None,
        build_messages: Callable[[dict[str, str]], list[PromptMessage]],
    ) -> None:
        """Register a prompt template that can be listed and fetched."""
        self._prompts[name] = PromptSpec(
            description=description,
            arguments=arguments,
            build_messages=build_messages,
        )

    async def list_prompts(self) -> ListPromptsResult:
        """Return all registered prompts."""
        prompts = [
            Prompt(
                name=name,
                description=spec.description,
                arguments=spec.arguments,
            )
            for name, spec in self._prompts.items()
        ]
        return ListPromptsResult(prompts=prompts)

    async def get_prompt(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        name: str,
        arguments: dict[str, str] | None = None,
    ) -> GetPromptResult:
        """Generate prompt content by name."""
        spec = self._prompts.get(name)
        if not spec:
            raise ValueError(f"Unknown prompt: {name}")
        messages = spec.build_messages(arguments or {})
        return GetPromptResult(description=spec.description, messages=messages)


mcp = FliMCP("Flight Search MCP Server")


@mcp.resource(
    "resource://fli-mcp/configuration",
    name="Fli MCP Configuration",
    description=(
        "Optional configuration defaults and environment variables for the Flight "
        "Search MCP server."
    ),
    mime_type="application/json",
)
def configuration_resource() -> str:
    """Expose configuration defaults and schema as a resource."""
    payload = {
        "defaults": CONFIG.model_dump(),
        "schema": CONFIG_SCHEMA,
        "environment": {
            "prefix": "FLI_MCP_",
            "variables": {
                "FLI_MCP_DEFAULT_PASSENGERS": "Adjust the default passenger count.",
                "FLI_MCP_DEFAULT_CURRENCY": "Override the currency code returned with results.",
                "FLI_MCP_DEFAULT_CABIN_CLASS": "Set a default cabin class.",
                "FLI_MCP_DEFAULT_SORT_BY": "Set the default result sorting strategy.",
                "FLI_MCP_DEFAULT_DEPARTURE_WINDOW": "Provide a default departure window (HH-HH).",
                "FLI_MCP_MAX_RESULTS": "Limit the maximum number of results returned by tools.",
            },
        },
    }
    return json.dumps(payload, indent=2)
