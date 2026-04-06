"""Minimal FastMCP shim for environments where importing fastmcp fails."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast


@dataclass
class Tool:
    """Minimal tool wrapper used by the MCP shim."""

    fn: Any
    name: str
    description: str | None
    parameters: dict[str, Any]

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., object],
        name: str | None = None,
        description: str | None = None,
    ) -> "Tool":
        """Create a minimal tool descriptor from a function."""
        return cls(
            fn=fn,
            name=name or fn.__name__,
            description=description or fn.__doc__,
            parameters={},
        )


class _ToolManager:
    """Store tool registrations for the MCP shim."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add_tool(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    async def get_tools(self) -> dict[str, Tool]:
        """Return all registered tools."""
        return self._tools


class _HookRegistrar:
    """Return no-op decorators for protocol hook registration."""

    def __call__(self) -> Callable[[Callable[..., object]], Callable[..., object]]:
        """Return a decorator that leaves the function untouched."""

        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            """Return the registered function unchanged."""
            return func

        return decorator


class _MCPServerHooks:
    """Expose hook registration methods expected by FliMCP."""

    list_tools = _HookRegistrar()
    list_prompts = _HookRegistrar()
    get_prompt = _HookRegistrar()


class FastMCP:
    """Small FastMCP-compatible runtime used only as a fallback."""

    def __init__(self, name: str | None = None, **_settings: Any) -> None:
        self.name = name
        self._tool_manager = _ToolManager()
        self._mcp_server = _MCPServerHooks()

    def _setup_handlers(self) -> None:
        """No-op shim hook setup."""

    def resource(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        """Register a resource via a no-op decorator."""

        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            """Attach `.fn` to the wrapped resource function."""
            cast(Any, func).fn = func
            return func

        return decorator

    def run(self, **_kwargs: Any) -> None:
        """No-op shim server runner."""
