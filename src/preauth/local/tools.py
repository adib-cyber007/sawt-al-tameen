"""Presenting the existing agent tools to a local model.

No tool is defined here. The list, the names, the descriptions and the argument schemas all come from
``preauth.agent_tools.toolbox``, the same source every hosted adapter reads, so the local agent can never
acquire a capability the hosted agent does not have. This module only reshapes those schemas into the flat form
small local models handle reliably (references resolved, optional types collapsed, enums listed inline).
"""

from typing import Any

KEEP = ("type", "enum", "format", "description", "items", "properties", "required")


def _resolve(schema: dict[str, Any], defs: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    if depth > 8:  # pragma: no cover - the tool inputs are flat; this only bounds a pathological schema
        return {"type": "string"}
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        target = _resolve(dict(defs.get(name, {})), defs, depth + 1)
        return {**target, **{k: v for k, v in schema.items() if k != "$ref"}}
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            branches = [b for b in schema[key] if b.get("type") != "null"]
            resolved = _resolve(dict(branches[0]), defs, depth + 1) if branches else {"type": "string"}
            return {**resolved, **{k: v for k, v in schema.items() if k != key}}
    return schema


def flatten_input_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """A self-contained JSON Schema object: no ``$ref``, no ``$defs``, no null-union types."""
    defs = schema.get("$defs", {})
    properties = {}
    for name, raw in schema.get("properties", {}).items():
        resolved = _resolve(dict(raw), defs)
        properties[name] = {k: v for k, v in resolved.items() if k in KEEP}
    return {
        "type": "object",
        "properties": properties,
        "required": [r for r in schema.get("required", []) if r in properties],
    }


def llm_tool_definitions(described_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The OpenAI-style ``tools`` array that Ollama's /api/chat expects, built from the toolbox's own output."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": flatten_input_schema(tool["input_schema"]),
            },
        }
        for tool in described_tools
    ]
