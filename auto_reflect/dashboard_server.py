#!/usr/bin/env python3
"""Local dashboard server with approve/reject actions.

Usage:
    python3 dashboard_server.py              # Start on port 7700
    python3 dashboard_server.py --port 8080  # Custom port

Opens browser automatically. Ctrl+C to stop.
"""

import http.server
import json
import os
import subprocess
import sys
import threading
import webbrowser
from urllib.parse import urlparse, parse_qs

from auto_reflect.config import AUTO_REFLECT_DIR

BASE = AUTO_REFLECT_DIR
DASHBOARD = os.path.join(BASE, "dashboard.html")
PORT = 7700


def regenerate_dashboard():
    """Regenerate dashboard HTML from live data."""
    subprocess.run(
        [sys.executable, "-m", "auto_reflect.generate_dashboard", "--no-open"],
        capture_output=True,
    )


def run_proposals_command(*args):
    """Run proposals.py with given args, return stdout."""
    # Longer timeout for approve (claude -p can take up to 2 min)
    timeout = 180 if any(a.startswith("--approve") for a in args) else 30
    result = subprocess.run(
        [sys.executable, "-m", "auto_reflect.proposals", *args],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout, result.stderr, result.returncode


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default logging

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path in ("/", "/dashboard"):
            # Regenerate fresh dashboard on each load
            regenerate_dashboard()
            try:
                with open(DASHBOARD) as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(content.encode())
            except FileNotFoundError:
                self.send_error(404, "Dashboard not generated yet")

        elif parsed.path == "/api/proposals":
            # Return current proposals list
            stdout, _, _ = run_proposals_command("--list")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                proposals = json.loads(stdout)
                self.wfile.write(json.dumps(proposals).encode())
            except json.JSONDecodeError:
                self.wfile.write(b"[]")

        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode() if content_length else ""

        if parsed.path == "/api/approve":
            self._handle_action(body, "--approve-id")

        elif parsed.path == "/api/reject":
            self._handle_action(body, "--reject-id")

        elif parsed.path == "/api/approve-all":
            # Approve all pending proposals by fingerprint (one call each, sequential)
            try:
                data = json.loads(body) if body else {}
                fingerprints = data.get("fingerprints", [])
                combined_output = []
                all_ok = True
                for fp in fingerprints:
                    stdout, stderr, code = run_proposals_command("--approve-id", fp)
                    combined_output.append(stdout.strip())
                    if code != 0:
                        all_ok = False
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": all_ok,
                    "output": "\n".join(combined_output),
                }).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())

        elif parsed.path == "/api/reject-all":
            stdout, stderr, code = run_proposals_command("--reject-all")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": code == 0,
                "output": stdout.strip(),
            }).encode())

        else:
            self.send_error(404)

    def _handle_action(self, body, flag):
        """Handle single approve/reject by fingerprint."""
        try:
            data = json.loads(body)
            fingerprint = data.get("fingerprint", "")
            if not fingerprint:
                raise ValueError("Missing fingerprint in request body")
            stdout, stderr, code = run_proposals_command(flag, fingerprint)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": code == 0,
                "output": stdout.strip(),
                "error": stderr.strip() if code != 0 else "",
            }).encode())
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())

    def do_OPTIONS(self):
        """Handle CORS preflight — only allow same-origin (localhost)."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "http://localhost:{0}".format(PORT))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    global PORT
    port = PORT
    args = sys.argv[1:]
    if "--port" in args:
        idx = args.index("--port")
        if idx + 1 >= len(args):
            print("Error: --port requires a value", file=sys.stderr)
            sys.exit(1)
        try:
            port = int(args[idx + 1])
        except ValueError:
            print(f"Error: invalid port number: {args[idx + 1]}", file=sys.stderr)
            sys.exit(1)
    PORT = port

    # Generate dashboard first
    regenerate_dashboard()

    try:
        server = http.server.HTTPServer(("127.0.0.1", port), DashboardHandler)
    except OSError as e:
        print(f"Error: cannot start server on port {port}: {e}", file=sys.stderr)
        print(f"Try: python3 -m auto_reflect.dashboard_server --port {port + 1}", file=sys.stderr)
        sys.exit(1)

    url = f"http://localhost:{port}"
    print(f"Auto-Reflect Dashboard: {url}")
    print("Ctrl+C to stop\n")

    # Open browser after short delay
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
