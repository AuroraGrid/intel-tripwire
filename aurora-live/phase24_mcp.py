from __future__ import annotations

import json
from typing import Any


class AuroraMCPServer:
    """Small, offline-capable MCP JSON-RPC server over existing AURORA services."""

    PROTOCOL_VERSION = "2025-06-18"

    def __init__(
        self,
        command_center,
        detection,
        routes,
        autonomous_forecasts,
        integrity,
        mesh,
        fabric,
    ):
        self.command_center = command_center
        self.detection = detection
        self.routes = routes
        self.autonomous_forecasts = autonomous_forecasts
        self.integrity = integrity
        self.mesh = mesh
        self.fabric = fabric

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [
            self._tool(
                "search_intelligence",
                "Search detections, claims, routes, and forecast candidates.",
                {"query": {"type": "string"}, "limit": {"type": "integer"}},
                ["query"],
            ),
            self._tool(
                "list_detections",
                "List correlated detections in the current workspace.",
                {
                    "state": {"type": "string"},
                    "domain": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            ),
            self._tool(
                "get_detection",
                "Retrieve one detection with observation and claim lineage.",
                {"detection_id": {"type": "string"}},
                ["detection_id"],
            ),
            self._tool(
                "list_routes",
                "List route plans ordered by risk.",
                {
                    "status": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            ),
            self._tool(
                "get_route",
                "Retrieve a route plan, alternatives, constraints, and exposure.",
                {"plan_id": {"type": "string"}},
                ["plan_id"],
            ),
            self._tool(
                "list_forecast_candidates",
                "List deterministic forecast candidates and approval state.",
                {
                    "state": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            ),
            self._tool(
                "get_forecast_candidate",
                "Retrieve a forecast candidate, triggers, falsifiers, and costs.",
                {"candidate_id": {"type": "string"}},
                ["candidate_id"],
            ),
            self._tool(
                "list_contradictions",
                "List evidence contradictions requiring review.",
                {
                    "state": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            ),
            self._tool(
                "get_source_health",
                "Retrieve sensor health and coverage.",
                {},
            ),
            self._tool(
                "stream_events",
                "Read the durable event stream from a numeric cursor.",
                {
                    "after": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "event_type": {"type": "string"},
                },
            ),
            self._tool(
                "get_command_center",
                "Retrieve unified priority queues and scorecards.",
                {},
            ),
        ]

    @staticmethod
    def _tool(
        name: str,
        description: str,
        properties: dict[str, Any],
        required: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        }

    def manifest(self) -> dict[str, Any]:
        return {
            "name": "aurora-live",
            "title": "AURORA LIVE Intelligence Tools",
            "version": "24.0.0",
            "protocolVersion": self.PROTOCOL_VERSION,
            "transport": {"type": "streamable-http", "endpoint": "/mcp"},
            "authentication": {
                "bearer": True,
                "api_key_header": "X-AURORA-API-KEY",
            },
            "tool_count": len(self.tool_definitions()),
            "tools": self.tool_definitions(),
            "safety": {
                "read_only_tools": True,
                "workspace_isolated": True,
                "external_ai_required": False,
            },
        }

    def call(
        self, actor: dict[str, Any], name: str, arguments: dict[str, Any]
    ) -> Any:
        args = arguments or {}
        if name == "search_intelligence":
            return {
                "results": self.command_center.search(
                    actor, str(args.get("query") or ""), int(args.get("limit", 50))
                )
            }
        if name == "list_detections":
            return {
                "detections": self.detection.detections(
                    actor,
                    str(args.get("state") or ""),
                    str(args.get("domain") or ""),
                    int(args.get("limit", 100)),
                )
            }
        if name == "get_detection":
            return self.detection.detection(
                actor, str(args.get("detection_id") or "")
            )
        if name == "list_routes":
            return {
                "routes": self.routes.plans(
                    actor,
                    str(args.get("status") or "ACTIVE"),
                    int(args.get("limit", 100)),
                )
            }
        if name == "get_route":
            return self.routes.plan(actor, str(args.get("plan_id") or ""))
        if name == "list_forecast_candidates":
            return {
                "candidates": self.autonomous_forecasts.candidates(
                    actor,
                    str(args.get("state") or ""),
                    int(args.get("limit", 100)),
                )
            }
        if name == "get_forecast_candidate":
            return self.autonomous_forecasts.candidate(
                actor, str(args.get("candidate_id") or "")
            )
        if name == "list_contradictions":
            return {
                "contradictions": self.integrity.contradictions(
                    actor,
                    str(args.get("state") or "OPEN"),
                    int(args.get("limit", 100)),
                )
            }
        if name == "get_source_health":
            return {
                "health": self.mesh.health(actor),
                "coverage": self.mesh.coverage(actor),
            }
        if name == "stream_events":
            return self.fabric.stream(
                actor,
                int(args.get("after", 0)),
                int(args.get("limit", 100)),
                str(args.get("event_type") or ""),
            )
        if name == "get_command_center":
            return self.command_center.overview(actor)
        raise ValueError("unknown MCP tool")

    def handle(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        if payload.get("jsonrpc") != "2.0":
            raise ValueError("jsonrpc must be 2.0")
        method = str(payload.get("method") or "")
        request_id = payload.get("id")
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        if method == "initialize":
            result = {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "aurora-live",
                    "version": "24.0.0",
                },
                "instructions": (
                    "Use AURORA records as evidence-linked intelligence. "
                    "Preserve uncertainty and do not treat forecasts as facts."
                ),
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": self.tool_definitions()}
        elif method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be an object")
            output = self.call(actor, name, arguments)
            result = {
                "content": [{
                    "type": "text",
                    "text": json.dumps(
                        output,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ),
                }],
                "structuredContent": output,
                "isError": False,
            }
        elif method == "notifications/initialized":
            result = {}
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
