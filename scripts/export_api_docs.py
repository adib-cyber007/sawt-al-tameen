"""Regenerate docs/openapi.json and docs/API.md from the application itself.

    uv run python scripts/export_api_docs.py          # write
    uv run python scripts/export_api_docs.py --check  # exit 1 if docs are stale
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

from preauth.api.app import create_app
from preauth.infrastructure.settings import RuntimeMode, Settings

DOCS = Path(__file__).resolve().parents[1] / "docs"


def _schema_name(schema: dict | None) -> str:
    if not schema:
        return "—"
    if "$ref" in schema:
        return f"`{schema['$ref'].rsplit('/', 1)[-1]}`"
    if schema.get("type") == "array":
        return f"array of {_schema_name(schema.get('items'))}"
    if schema.get("type") == "object":
        return "JSON object (see tool `input_schema` / `output_schema`)"
    return f"`{schema.get('type', 'unknown')}`"


def _spec() -> dict:
    """Both channels in one document: the service is the same service whichever one is mounted."""
    return create_app(
        MagicMock(), Settings(runtime_mode=RuntimeMode.LOCAL), local_runtime=MagicMock()
    ).openapi()


def render_markdown(spec: dict) -> str:
    out = [
        "# API Reference",
        "",
        "_Generated from the OpenAPI specification by `scripts/export_api_docs.py`. Do not edit by hand._",
        "",
        spec["info"]["description"].strip(),
        "",
        "Schemas referenced below are defined in [`openapi.json`](openapi.json) under `components.schemas`.",
        "",
    ]
    by_tag: dict[str, list[tuple[str, str, dict]]] = {}
    for path, operations in spec["paths"].items():
        for method, op in operations.items():
            by_tag.setdefault(op.get("tags", ["Other"])[0], []).append((method.upper(), path, op))

    for tag, ops in by_tag.items():
        out += [f"## {tag}", ""]
        for method, path, op in ops:
            out += [f"### `{method} {path}` — {op['summary']}", ""]
            if op.get("description"):
                out += [op["description"].strip(), ""]
            params = [p for p in op.get("parameters", []) if p["in"] in ("path", "query")]
            if params:
                out.append("**Parameters:** " + ", ".join(f"`{p['name']}` ({p['in']})" for p in params))
                out.append("")
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            out += [f"**Request body:** {_schema_name(body)}", ""]
            out += ["| Status | Response | Error codes / meaning |", "|---|---|---|"]
            for status, resp in sorted(op["responses"].items()):
                schema = resp.get("content", {}).get("application/json", {}).get("schema")
                out.append(f"| {status} | {_schema_name(schema)} | {resp.get('description', '')} |")
            out.append("")
    return "\n".join(out)


def main() -> int:
    spec = _spec()
    rendered = {
        DOCS / "openapi.json": json.dumps(spec, indent=2, sort_keys=True) + "\n",
        DOCS / "API.md": render_markdown(spec) + "\n",
    }
    if "--check" in sys.argv:
        stale = [
            p.name for p, content in rendered.items()
            if not p.exists() or p.read_text(encoding="utf-8") != content
        ]
        if stale:
            print(f"Stale API docs: {stale}. Run scripts/export_api_docs.py", file=sys.stderr)
            return 1
        return 0
    for path, content in rendered.items():
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(DOCS.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
