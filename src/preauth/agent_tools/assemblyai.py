"""AssemblyAI stored-agent tool definitions generated from the provider-neutral toolbox.

The realtime session bridge executes these client-side tools inside this process. Stored-agent tools omit both the
inline-session ``type`` discriminator and the server-side ``http`` configuration, so AssemblyAI emits ``tool.call``
events; credentials and business endpoints are never exposed to the browser or stored on the voice platform.
"""

from copy import deepcopy
from typing import Any

from pydantic import BaseModel

from preauth.agent_tools.toolbox import TOOLS, Tool


class UnsupportedToolSchemaError(ValueError):
    pass


def _flatten(node: Any, definitions: dict[str, Any]) -> Any:
    """Resolve Pydantic references and nullable unions into the JSON Schema subset used by Voice Agent tools."""
    if isinstance(node, list):
        return [_flatten(item, definitions) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        if name not in definitions:
            raise UnsupportedToolSchemaError(f"Unknown schema reference {name!r}")
        resolved = deepcopy(definitions[name])
        resolved.update({key: value for key, value in node.items() if key != "$ref"})
        return _flatten(resolved, definitions)
    if "anyOf" in node:
        choices = [choice for choice in node["anyOf"] if choice.get("type") != "null"]
        if len(choices) != 1:
            raise UnsupportedToolSchemaError(f"Only nullable single-type unions are supported: {node}")
        resolved = deepcopy(choices[0])
        resolved.update({key: value for key, value in node.items() if key != "anyOf"})
        return _flatten(resolved, definitions)
    return {
        key: _flatten(value, definitions)
        for key, value in node.items()
        if key not in {"$defs", "default", "title"}
    }


def parameter_schema(model: type[BaseModel]) -> dict[str, Any]:
    raw = model.model_json_schema()
    schema = _flatten(raw, raw.get("$defs", {}))
    if schema.get("type") != "object":
        raise UnsupportedToolSchemaError(f"Tool input must be an object: {schema}")
    for name, prop in schema.get("properties", {}).items():
        if not prop.get("description"):
            raise UnsupportedToolSchemaError(f"Property {name!r} has no description")
    return schema


def function_tool_config(tool: Tool, *, timeout_seconds: int = 30) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": parameter_schema(tool.input_model),
        "execution_mode": "interactive",
        "timeout_seconds": timeout_seconds,
    }


def all_function_tool_configs() -> list[dict[str, Any]]:
    return [function_tool_config(tool) for tool in TOOLS]
