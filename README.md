# AWS Builder Loft agents

This repository contains one SIA command-adapter agent: the deep research agent.
It reads one JSON object from stdin and writes one JSON object to stdout.

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Fill in `LITELLM_PROXY_URL` and `LITELLM_PROXY_KEY`.
`RESEARCH_MODEL` selects the model and falls back to `AGENT_MODEL` when omitted.
The agent loads `.env` automatically.

## Deep research agent

```bash
printf '%s\n' '{"input":"Compare Python 3.13 with 3.12."}' \
  | .venv/bin/python agent.py
```

The research agent:

1. Plans and runs at least three distinctly labelled live-web research angles.
2. Reads at least four public HTML, text, or PDF sources across three domains,
   including at least two provisionally classified as primary or authoritative.
3. Blocks local/private network targets and manually validates redirects.
4. Treats retrieved content as untrusted evidence.
5. Cross-checks claims and runs a separate evidence-editing pass that removes or
   qualifies unsupported claims before accepting the report.
6. Appends a verified source manifest with evidentiary roles and returns the tool
   trace, token total, research counters, and structured source list.

Run the local unit tests with:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Local research studio

The browser interface streams the agent's plan, searches, source reads, depth
gates, evidence audit, and final cited report as they happen.

```bash
.venv/bin/python web_server.py
```

Open [http://localhost:8000](http://localhost:8000). The studio uses the same
`.env` and research agent as the command-line adapter.
