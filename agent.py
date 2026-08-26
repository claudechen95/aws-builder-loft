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
from typing import Callable

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
MIN_SOURCE_DOMAINS = 3
MIN_AUTHORITATIVE_SOURCES = 2
TIMEOUT_S = 120
ProgressCallback = Callable[[dict], None]


def _emit(callback: ProgressCallback | None, event: str, **details: object) -> None:
    if callback:
        callback({"event": event, **details})


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
            "focus": {
                "type": "string",
                "description": (
                    "Short label for this search's materially distinct research "
                    "angle, such as official data, historical record, or criticism."
                ),
            },
            "max_results": {"type": "integer", "minimum": 1, "maximum": 8},
        },
        ["query", "focus"],
    ),
    _fn(
        "read_url",
        "Read a public HTML, text, or PDF source and return citable content.",
        {
            "url": {"type": "string"},
            "source_role": {
                "type": "string",
                "enum": [
                    "primary",
                    "authoritative_reference",
                    "independent_secondary",
                    "context",
                ],
                "description": (
                    "Expected evidentiary role. Primary is an original record or "
                    "first-party document; authoritative_reference is a recognized "
                    "scholarly, government, standards, or reference work."
                ),
            },
        },
        ["url", "source_role"],
    ),
]


LOCAL_NETWORK_MENTION = re.compile(
    r"""
    \b(?:
        localhost |
        127\.\d{1,3}\.\d{1,3}\.\d{1,3} |
        0\.0\.0\.0 |
        ::1 |
        10\.\d{1,3}\.\d{1,3}\.\d{1,3} |
        172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3} |
        192\.168\.\d{1,3}\.\d{1,3} |
        169\.254\.\d{1,3}\.\d{1,3} |
        [a-z0-9-]+\.local(?:host)? |
        [a-z0-9-]+\.internal
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _mentions_local_network(question: str) -> bool:
    """Return whether a request explicitly names a private or local target."""
    return bool(LOCAL_NETWORK_MENTION.search(question or ""))


def _depth_gaps(session: ResearchSession) -> list[str]:
    gaps = []
    if session.distinct_search_count < MIN_SEARCHES:
        gaps.append(
            f"distinct research angles {session.distinct_search_count}/{MIN_SEARCHES}"
        )
    if session.read_count < MIN_READS:
        gaps.append(f"successful source reads {session.read_count}/{MIN_READS}")
    if session.source_domain_count < MIN_SOURCE_DOMAINS:
        gaps.append(f"source domains {session.source_domain_count}/{MIN_SOURCE_DOMAINS}")
    if session.authoritative_source_count < MIN_AUTHORITATIVE_SOURCES:
        gaps.append(
            "primary or authoritative sources "
            f"{session.authoritative_source_count}/{MIN_AUTHORITATIVE_SOURCES}"
        )
    return gaps


def _valid_citations(report: str, session: ResearchSession) -> tuple[bool, str]:
    valid_ids = {item["source_id"] for item in session.manifest()}
    cited_ids = set(re.findall(r"\[(S\d+)\]", report))
    if not cited_ids:
        return False, "The draft has no inline source citations."
    invented = cited_ids - valid_ids
    if invented:
        return False, (
            "The draft cites IDs that were not successfully read: "
            + ", ".join(sorted(invented))
        )
    return True, ""


def _audit_report(messages: list[dict], draft: str, usage: list[int]) -> str:
    audit_messages = messages + [
        {"role": "assistant", "content": draft},
        {
            "role": "user",
            "content": (
                "Act as a strict evidence editor. Return only a revised final report. "
                "Check every factual claim against the successfully read source text "
                "in this conversation. Remove or qualify claims that the evidence does "
                "not directly support, especially reconstructed causes, chronology, "
                "pronunciation, or causal explanations. Do not infer authority merely "
                "from the submitted source_role. Attribute sources accurately, label "
                "inference and uncertainty, and put inline source IDs on every material "
                "claim. Cite only IDs from successful read_url calls."
            ),
        },
    ]
    audited = call_model(audit_messages, usage, tools=False)
    return (audited.get("content") or draft).strip()


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


def research(
    question: str,
    on_progress: ProgressCallback | None = None,
) -> tuple[str, list[dict], list[int], ResearchSession]:
    session = ResearchSession()
    messages = [
        {"role": "system", "content": build_system()},
        {"role": "user", "content": question},
    ]
    trace: list[dict] = []
    usage: list[int] = []
    last_draft = ""
    challenged_local_network_clarify = False
    _emit(on_progress, "phase", phase="planning", message="Mapping the research terrain")

    for round_number in range(1, MAX_ROUNDS + 1):
        _emit(
            on_progress,
            "model_round",
            round=round_number,
            message="Choosing the next evidence move",
        )
        message = call_model(messages, usage)
        calls = message.get("tool_calls") or []
        if not calls:
            last_draft = (message.get("content") or "").strip()
            if session.search_count == 0 and last_draft.startswith("CLARIFY:"):
                if (
                    not challenged_local_network_clarify
                    and _mentions_local_network(question)
                ):
                    challenged_local_network_clarify = True
                    messages.append(message)
                    messages.append({
                        "role": "user",
                        "content": (
                            "Do not ask for clarification merely because this request "
                            "names a localhost, loopback, or private-network address. "
                            "State that you cannot fetch that target, then identify and "
                            "fully research any independently scoped public-web part of "
                            "the request using web_search and read_url, per policy "
                            "section 5. Only reply CLARIFY: again if the remaining part "
                            "is itself impossible to scope without more information."
                        ),
                    })
                    _emit(
                        on_progress,
                        "phase",
                        phase="planning",
                        message="Separating the blocked local target from the public research",
                    )
                    continue
                _emit(on_progress, "clarification", message="The question needs a sharper scope")
                return last_draft.removeprefix("CLARIFY:").strip(), trace, usage, session
            gaps = _depth_gaps(session)
            if not gaps:
                _emit(
                    on_progress,
                    "phase",
                    phase="audit",
                    message="Stress-testing every material claim",
                )
                audited_draft = _audit_report(messages, last_draft, usage)
                citations_ok, citation_problem = _valid_citations(
                    audited_draft, session
                )
                if citations_ok:
                    _emit(on_progress, "phase", phase="complete", message="Evidence audit passed")
                    return audited_draft, trace, usage, session
                messages.append(message)
                messages.append({
                    "role": "user",
                    "content": (
                        f"{citation_problem} Rewrite the report with inline citations "
                        "using only source IDs returned by successful read_url calls."
                    ),
                })
                continue
            _emit(
                on_progress,
                "depth_check",
                gaps=gaps,
                message="Deepening coverage before synthesis",
            )
            messages.append(message)
            messages.append({
                "role": "user",
                "content": (
                    "The research is not deep enough yet. Continue using tools. "
                    "Remaining gates: " + "; ".join(gaps) + ". Use materially distinct "
                    "focus labels and queries, diversify source domains, read primary "
                    "or authoritative sources, and cross-check central claims."
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
                _emit(
                    on_progress,
                    "tool_started",
                    tool=name,
                    query=args.get("query"),
                    focus=args.get("focus"),
                    url=args.get("url"),
                    source_role=args.get("source_role"),
                )
                if name == "web_search":
                    result = session.web_search(**args)
                elif name == "read_url":
                    result = session.read_url(**args)
                else:
                    result = {"error": f"No tool {name!r}."}
            except (httpx.HTTPError, TypeError, ValueError) as exc:
                result = {"error": str(exc)}
            trace.append({"name": name, "args": args, "result": result})
            _emit(
                on_progress,
                "tool_completed",
                tool=name,
                ok="error" not in result,
                error=result.get("error"),
                source_id=result.get("source_id"),
                title=result.get("title"),
                url=result.get("url") or args.get("url"),
                source_role=result.get("source_role") or args.get("source_role"),
                result_count=len(result.get("results") or []),
                searches=session.search_count,
                distinct_searches=session.distinct_search_count,
                sources_read=session.read_count,
                domains=session.source_domain_count,
                authoritative=session.authoritative_source_count,
            )
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
    _emit(on_progress, "phase", phase="synthesis", message="Writing the best-supported answer")
    final = call_model(messages, usage, tools=False)
    report = (final.get("content") or last_draft or "Research could not be completed.").strip()
    _emit(on_progress, "phase", phase="complete", message="Research complete with noted limitations")
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
            "distinct_searches": session.distinct_search_count,
            "sources_read": session.read_count,
            "source_domains": session.source_domain_count,
            "authoritative_sources": session.authoritative_source_count,
            "model": MODEL,
        },
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
