"""Local streaming web interface for the deep research agent."""
from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import httpx

import agent


ROOT = Path(__file__).parent.resolve()
WEB_ROOT = ROOT / "web"
MAX_REQUEST_BYTES = 64 * 1024


class ResearchHandler(BaseHTTPRequestHandler):
    server_version = "ResearchStudio/1.0"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            missing = [
                name
                for name, value in (
                    ("LITELLM_PROXY_URL", agent.PROXY_URL),
                    ("LITELLM_PROXY_KEY", agent.PROXY_KEY),
                )
                if not value
            ]
            self._json(200, {"ok": not missing, "missing": missing, "model": agent.MODEL})
            return

        relative = "index.html" if path == "/" else path.lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT not in target.parents or not target.is_file():
            self._json(404, {"error": "Not found"})
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/research":
            self._json(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("Request body is empty or too large")
            payload = json.loads(self.rfile.read(length))
            question = str(payload.get("question") or "").strip()
            if not question:
                raise ValueError("Enter a research question")
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
            return

        missing = [
            name
            for name, value in (
                ("LITELLM_PROXY_URL", agent.PROXY_URL),
                ("LITELLM_PROXY_KEY", agent.PROXY_KEY),
            )
            if not value
        ]
        if missing:
            self._json(503, {"error": f"Configure {' and '.join(missing)} in .env first"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.end_headers()

        def send(event: dict) -> None:
            self.wfile.write(json.dumps(event, ensure_ascii=False).encode() + b"\n")
            self.wfile.flush()

        try:
            send({"event": "run_started", "question": question, "model": agent.MODEL})
            report, trace, usage, session = agent.research(question, on_progress=send)
            send({
                "event": "result",
                "report": report,
                "sources": session.manifest(),
                "tokens": sum(usage) if usage else None,
                "research": {
                    "searches": session.search_count,
                    "distinct_searches": session.distinct_search_count,
                    "sources_read": session.read_count,
                    "source_domains": session.source_domain_count,
                    "authoritative_sources": session.authoritative_source_count,
                    "tool_calls": len(trace),
                    "model": agent.MODEL,
                },
            })
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            try:
                send({"event": "error", "message": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass
        except (BrokenPipeError, ConnectionResetError):
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the deep research web studio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), ResearchHandler)
    print(f"Deep Research Studio: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
