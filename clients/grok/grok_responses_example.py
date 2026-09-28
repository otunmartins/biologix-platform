"""Run one Biologix turn with Grok through the xAI Responses API (OpenAI-compatible).

    pip install openai
    XAI_API_KEY=... BIOLOGIX_MCP_TOKEN=... python grok_responses_example.py "semaglutide, suggest"
"""

import os
import sys
from pathlib import Path

from openai import OpenAI

client = OpenAI(api_key=os.environ["XAI_API_KEY"], base_url="https://api.x.ai/v1")
protocol = (Path(__file__).resolve().parent.parent / "AGENTS.md").read_text(encoding="utf-8")
response = client.responses.create(
    model=os.environ.get("XAI_MODEL", "grok-4"),
    instructions=protocol,
    input=" ".join(sys.argv[1:]) or "I want to stabilize insulin; suggest polymers.",
    tools=[
        {
            "type": "mcp",
            "server_url": os.environ.get("BIOLOGIX_MCP_URL", "https://otunmartins--biologix-mcp-serve.modal.run/mcp"),
            "server_label": "biologix",
            "authorization": "Bearer " + os.environ["BIOLOGIX_MCP_TOKEN"],
        }
    ],
)
print(response.output_text)
