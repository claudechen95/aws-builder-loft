# AWS Builder Loft agents

This repository contains two SIA command-adapter agents. Each reads one JSON
object from stdin and writes one JSON object to stdout.

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

Fill in `LITELLM_PROXY_URL` and `LITELLM_PROXY_KEY`. `AGENT_MODEL` configures
the expense agent. `RESEARCH_MODEL` configures the research agent and falls
back to `AGENT_MODEL` when omitted. The research agent loads `.env`
automatically; `agent.py` expects the variables to be exported by its launcher.

## Expense agent

```bash
set -a; . ./.env; set +a
printf '%s\n' '{"input":"Review E-220 for Lin."}' | .venv/bin/python agent.py
```

The expense agent applies `policy.md` to the pristine in-memory ledger in
`expenses.py` and returns the reply, tool trace, and total token usage.

## Deep research agent

```bash
printf '%s\n' '{"input":"Compare Python 3.13 with 3.12."}' \
  | .venv/bin/python research_agent.py
```

The research agent:

1. Plans and runs at least three distinct live-web searches.
2. Reads at least four public HTML, text, or PDF sources.
3. Blocks local/private network targets and manually validates redirects.
4. Treats retrieved content as untrusted evidence.
5. Cross-checks claims and writes a report with inline source IDs.
6. Appends a verified source manifest and returns the tool trace, token total,
   research counters, and structured source list.

Run the local unit tests with:

```bash
.venv/bin/python -m unittest discover -s tests -v
```
