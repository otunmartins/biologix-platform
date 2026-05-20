"""
Shared service layer for the biologics platform.

All business logic lives here. MCP tools and FastAPI routes are thin
wrappers that delegate to these services. No service imports MCP or
FastAPI — they are framework-agnostic.
"""
