"""Acme Deep Research — a SIA-compatible live-web research agent.

Input:  {"input": "Research question"} on stdin.
Output: {"output": "Report", "tool_calls": [...], "tokens": 123,
         "sources": [...], "research": {...}} on stdout.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

from research import ResearchSession
from research_prompts import build_system

load_dotenv(Path(__file__).parent / ".env")

MODEL = os.environ.get("RESEARCH_MODEL") or os.environ.get(
    "AGENT_MODEL", "azure_ai/gpt-5.4-mini"
)
PROXY_URL = os.environ.get("LITELLM_PROXY_URL", "").strip().rstrip("/")
PROXY_KEY = os.environ.get("LITELLM_PROXY_KEY", "").strip()
MAX_ROUNDS = 14
MIN_SEARCHES = 3
MIN_READS = 4
TIMEOUT_S = 120


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }}


TOOLS = [
    _fn(
        "web_search",
        "Search the live public web. Results are leads; read a URL before citing it.",
        {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 8},
        },
        ["query"],
    ),
    _fn(
        "read_url",
        "Read a public HTML, text, or PDF source and return citable content.",
        {"url": {"type": "string"}},
        ["url"],
    ),
]


def call_model(messages: list[dict], usage: list[int], tools: bool = True) -> dict:
    request = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": 2400,
    }
    if tools:
        request["tools"] = TOOLS
    response = httpx.post(
        f"{PROXY_URL}/v1/chat/completions",
        headers={"Authorization": f"Bearer {PROXY_KEY}"},
        json=request,
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    body = response.json()
    spent = (body.get("usage") or {}).get("total_tokens")
    if isinstance(spent, int):
        usage.append(spent)
    return body["choices"][0]["message"]


def research(question: str) -> tuple[str, list[dict], list[int], ResearchSession]:
    session = ResearchSession()
    messages = [
        {"role": "system", "content": build_system()},
        {"role": "user", "content": question},
    ]
    trace: list[dict] = []
    usage: list[int] = []
    last_draft = ""

    for _ in range(MAX_ROUNDS):
        message = call_model(messages, usage)
        calls = message.get("tool_calls") or []
        if not calls:
            last_draft = (message.get("content") or "").strip()
            if session.search_count == 0 and last_draft.startswith("CLARIFY:"):
                return last_draft.removeprefix("CLARIFY:").strip(), trace, usage, session
            if session.search_count >= MIN_SEARCHES and session.read_count >= MIN_READS:
                valid_ids = {item["source_id"] for item in session.manifest()}
                cited_ids = set(re.findall(r"\[(S\d+)\]", last_draft))
                if cited_ids and cited_ids <= valid_ids:
                    return last_draft, trace, usage, session
                citation_problem = (
                    "The draft has no inline source citations."
                    if not cited_ids
                    else "The draft cites IDs that were not successfully read: "
                         + ", ".join(sorted(cited_ids - valid_ids))
                )
                messages.append(message)
                messages.append({
                    "role": "user",
                    "content": (
                        f"{citation_problem} Rewrite the report with inline citations "
                        "using only source IDs returned by successful read_url calls."
                    ),
                })
                continue
            messages.append(message)
            messages.append({
                "role": "user",
                "content": (
                    "The research is not deep enough yet. Continue using tools. "
                    f"Completed {session.search_count}/{MIN_SEARCHES} searches and "
                    f"{session.read_count}/{MIN_READS} successful source reads. "
                    "Use distinct queries, read authoritative sources, and cross-check "
                    "the central claims before writing the report."
                ),
            })
            continue

        messages.append(message)
        for call in calls:
            name = call.get("function", {}).get("name", "")
            try:
                args = json.loads(call.get("function", {}).get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            try:
                if name == "web_search":
                    result = session.web_search(**args)
                elif name == "read_url":
                    result = session.read_url(**args)
                else:
                    result = {"error": f"No tool {name!r}."}
            except (httpx.HTTPError, TypeError, ValueError) as exc:
                result = {"error": str(exc)}
            trace.append({"name": name, "args": args, "result": result})
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(result),
            })

    messages.append({
        "role": "user",
        "content": (
            "Stop researching and write the best final report supported by the "
            "sources successfully read. Cite only their source IDs and clearly state "
            "any limitations."
        ),
    })
    final = call_model(messages, usage, tools=False)
    report = (final.get("content") or last_draft or "Research could not be completed.").strip()
    return report, trace, usage, session


def main() -> int:
    missing = [
        name for name, value in (
            ("LITELLM_PROXY_URL", PROXY_URL),
            ("LITELLM_PROXY_KEY", PROXY_KEY),
        ) if not value
    ]
    if missing:
        print(json.dumps({"error": f"{' and '.join(missing)} is not set."}))
        return 1
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        print(json.dumps({"error": "stdin was not JSON"}))
        return 1
    question = str(payload.get("input") or "").strip()
    if not question:
        print(json.dumps({"error": "input must contain a research question"}))
        return 1
    try:
        report, trace, usage, session = research(question)
    except httpx.HTTPError as exc:
        print(json.dumps({"error": f"model call failed: {exc}"}))
        return 1
    output = f"{report}\n\n{session.source_block()}"
    print(json.dumps({
        "output": output,
        "tool_calls": trace,
        "tokens": sum(usage) if usage else None,
        "sources": session.manifest(),
        "research": {
            "searches": session.search_count,
            "sources_read": session.read_count,
            "model": MODEL,
        },
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
