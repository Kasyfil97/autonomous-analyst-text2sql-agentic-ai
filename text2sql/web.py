"""Small web UI for the Text-to-SQL agent.

Run with:

    python -m text2sql.web

The server uses only the Python standard library. It serves a single-page chat UI and a
JSON API that delegates to the existing orchestrator. Generated SQL remains a draft and
is never executed by this process.
"""
from __future__ import annotations

import argparse
import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urlparse

from text2sql.agent import Text2SQLResult, get_session
from text2sql.audit_log import get_logger
from text2sql.orchestrator import handle as orchestrate
from text2sql.search_agent import SearchResult

_log = get_logger("web")
_session = None
_session_lock = threading.Lock()


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Text-to-SQL Agent</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f7fb;
      --panel: #ffffff;
      --panel-2: #eef3f8;
      --ink: #15202b;
      --muted: #64748b;
      --line: #d8e0e8;
      --accent: #0f766e;
      --accent-2: #2563eb;
      --danger: #b42318;
      --warn: #a15c07;
      --code: #0d1720;
      --shadow: 0 18px 50px rgba(15, 23, 42, 0.12);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, rgba(15, 118, 110, 0.14), transparent 34rem),
        linear-gradient(135deg, #f7fafc 0%, var(--bg) 52%, #edf4f7 100%);
      color: var(--ink);
    }

    .app {
      display: grid;
      grid-template-columns: 320px minmax(0, 1fr);
      min-height: 100vh;
    }

    .sidebar {
      padding: 32px 28px;
      border-right: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.72);
      backdrop-filter: blur(18px);
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 32px;
    }

    .mark {
      display: grid;
      width: 42px;
      height: 42px;
      place-items: center;
      border-radius: 12px;
      background: var(--ink);
      color: white;
      font-weight: 800;
      letter-spacing: 0;
    }

    h1 {
      margin: 0;
      font-size: 1.15rem;
      line-height: 1.2;
    }

    .brand span, .hint, .meta, .footer {
      color: var(--muted);
      font-size: 0.92rem;
      line-height: 1.55;
    }

    .status {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin: 10px 0 26px;
      padding: 8px 11px;
      border: 1px solid rgba(15, 118, 110, 0.22);
      border-radius: 999px;
      background: rgba(15, 118, 110, 0.08);
      color: #0b625c;
      font-size: 0.82rem;
      font-weight: 700;
    }

    .dot {
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 0 0 4px rgba(15, 118, 110, 0.14);
    }

    .examples {
      display: grid;
      gap: 10px;
      margin: 22px 0;
    }

    .example {
      width: 100%;
      padding: 12px 13px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      color: var(--ink);
      text-align: left;
      font: inherit;
      line-height: 1.4;
      cursor: pointer;
      transition: transform 120ms ease, border-color 120ms ease, box-shadow 120ms ease;
    }

    .example:hover {
      transform: translateY(-1px);
      border-color: rgba(15, 118, 110, 0.38);
      box-shadow: 0 8px 20px rgba(15, 23, 42, 0.08);
    }

    .footer {
      margin-top: 28px;
      padding-top: 18px;
      border-top: 1px solid var(--line);
    }

    .main {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      min-width: 0;
      min-height: 100vh;
    }

    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 24px 32px 18px;
    }

    .topbar h2 {
      margin: 0;
      font-size: clamp(1.45rem, 3vw, 2.2rem);
      letter-spacing: 0;
    }

    .badge {
      flex: 0 0 auto;
      padding: 8px 10px;
      border-radius: 8px;
      background: #e6f0ff;
      color: #1d4ed8;
      font-size: 0.78rem;
      font-weight: 800;
      text-transform: uppercase;
    }

    .messages {
      min-height: 0;
      overflow-y: auto;
      padding: 8px 32px 24px;
    }

    .thread {
      display: grid;
      gap: 16px;
      max-width: 1040px;
      margin: 0 auto;
    }

    .message {
      display: grid;
      gap: 10px;
      max-width: 860px;
      animation: rise 160ms ease-out;
    }

    .message.user {
      justify-self: end;
      justify-items: end;
    }

    .bubble {
      padding: 15px 16px;
      border-radius: 8px;
      background: var(--panel);
      border: 1px solid var(--line);
      box-shadow: 0 8px 26px rgba(15, 23, 42, 0.06);
      white-space: pre-wrap;
      line-height: 1.55;
    }

    .rich {
      white-space: normal;
    }

    .rich p {
      margin: 0;
      white-space: pre-wrap;
    }

    .rich p + p,
    .rich p + .table-scroll,
    .rich .table-scroll + p {
      margin-top: 12px;
    }

    .table-scroll {
      max-width: 100%;
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }

    .data-table {
      width: 100%;
      min-width: 560px;
      border-collapse: collapse;
      font-size: 0.9rem;
      line-height: 1.45;
      white-space: normal;
    }

    .data-table th,
    .data-table td {
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }

    .data-table th {
      background: #eef3f8;
      color: #334155;
      font-size: 0.78rem;
      font-weight: 800;
      text-transform: uppercase;
    }

    .data-table tr:last-child td {
      border-bottom: 0;
    }

    .user .bubble {
      background: var(--ink);
      color: white;
      border-color: var(--ink);
    }

    .label {
      color: var(--muted);
      font-size: 0.78rem;
      font-weight: 800;
      text-transform: uppercase;
    }

    .result {
      display: grid;
      gap: 12px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.9);
      box-shadow: var(--shadow);
    }

    .result-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }

    .result-title {
      margin: 0;
      font-size: 1rem;
    }

    .pill-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 5px 9px;
      border-radius: 999px;
      background: var(--panel-2);
      color: #334155;
      font-size: 0.82rem;
      font-weight: 700;
    }

    .warning {
      padding: 11px 12px;
      border-radius: 8px;
      border: 1px solid #f3c27b;
      background: #fff7ed;
      color: var(--warn);
      line-height: 1.45;
    }

    .decline {
      border-color: #f0aaa4;
      background: #fff4f2;
      color: var(--danger);
    }

    .sql-wrap {
      overflow: hidden;
      border-radius: 8px;
      border: 1px solid #223244;
      background: var(--code);
    }

    .sql-toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 9px 12px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.1);
      color: #b8c7d9;
      font-size: 0.78rem;
      font-weight: 800;
      text-transform: uppercase;
    }

    .copy {
      border: 1px solid rgba(255, 255, 255, 0.2);
      background: rgba(255, 255, 255, 0.08);
      color: white;
      border-radius: 7px;
      padding: 6px 9px;
      cursor: pointer;
      font: inherit;
      font-size: 0.78rem;
      font-weight: 800;
    }

    pre {
      margin: 0;
      padding: 16px;
      overflow-x: auto;
      color: #e8f1fb;
      font: 0.92rem/1.55 "Cascadia Code", Consolas, "SFMono-Regular", monospace;
    }

    .composer {
      padding: 18px 32px 28px;
      background: linear-gradient(180deg, rgba(244, 247, 251, 0), rgba(244, 247, 251, 0.96) 24%);
    }

    .composer-inner {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      max-width: 1040px;
      margin: 0 auto;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: rgba(255, 255, 255, 0.92);
      box-shadow: var(--shadow);
    }

    textarea {
      width: 100%;
      min-height: 54px;
      max-height: 170px;
      resize: vertical;
      border: 0;
      outline: 0;
      padding: 13px 14px;
      color: var(--ink);
      background: transparent;
      font: inherit;
      line-height: 1.45;
    }

    .send {
      align-self: end;
      min-width: 112px;
      min-height: 48px;
      border: 0;
      border-radius: 8px;
      background: var(--accent);
      color: white;
      cursor: pointer;
      font: inherit;
      font-weight: 800;
      transition: transform 120ms ease, background 120ms ease;
    }

    .send:hover { transform: translateY(-1px); background: #0b625c; }
    .send:disabled { cursor: wait; opacity: 0.72; transform: none; }

    .typing {
      display: inline-flex;
      gap: 5px;
      align-items: center;
      padding: 15px 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }

    .typing span {
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--muted);
      animation: pulse 950ms infinite ease-in-out;
    }

    .typing span:nth-child(2) { animation-delay: 120ms; }
    .typing span:nth-child(3) { animation-delay: 240ms; }

    @keyframes pulse {
      0%, 80%, 100% { opacity: 0.25; transform: translateY(0); }
      40% { opacity: 1; transform: translateY(-3px); }
    }

    @keyframes rise {
      from { opacity: 0; transform: translateY(5px); }
      to { opacity: 1; transform: translateY(0); }
    }

    @media (max-width: 860px) {
      .app { grid-template-columns: 1fr; }
      .sidebar {
        display: none;
      }
      .topbar, .messages, .composer {
        padding-left: 16px;
        padding-right: 16px;
      }
      .topbar {
        align-items: flex-start;
        flex-direction: column;
      }
      .composer-inner {
        grid-template-columns: 1fr;
      }
      .send {
        width: 100%;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">
        <div class="mark">SQL</div>
        <div>
          <h1>Text-to-SQL Agent</h1>
          <span>Draft SQL and KB search</span>
        </div>
      </div>
      <div class="status"><span class="dot"></span>Read-only draft mode</div>
      <p class="hint">
        Ask in Indonesian or English. The agent retrieves ERA precedents and schema
        metadata, then returns an unverified SQL draft or a grounded KB answer.
      </p>
      <div class="examples">
        <button class="example" type="button">Buat query untuk total transaksi per bulan selama 2025.</button>
        <button class="example" type="button">Tabel apa yang punya kolom customer id?</button>
        <button class="example" type="button">Find columns related to card transaction amount.</button>
      </div>
      <div class="footer">
        SQL is never executed by this UI. Review warnings, assumptions, and source tables
        before using any generated query.
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div>
          <h2>Ask for a query or explore the knowledge base</h2>
          <div class="meta">Grounded by schema catalog and ERA precedents.</div>
        </div>
        <div class="badge">Unverified SQL</div>
      </header>

      <section class="messages" id="messages">
        <div class="thread" id="thread">
          <div class="message assistant">
            <div class="label">Agent</div>
            <div class="bubble">What would you like to analyze? Ask for a draft SQL query or ask which tables and columns exist in the knowledge base.</div>
          </div>
        </div>
      </section>

      <form class="composer" id="form">
        <div class="composer-inner">
          <textarea id="question" autocomplete="off" placeholder="Ask a question, for example: buat query nasabah aktif per region..." required></textarea>
          <button class="send" id="send" type="submit">Send</button>
        </div>
      </form>
    </main>
  </div>

  <script>
    const form = document.getElementById("form");
    const input = document.getElementById("question");
    const send = document.getElementById("send");
    const thread = document.getElementById("thread");
    const messages = document.getElementById("messages");

    function scrollBottom() {
      messages.scrollTop = messages.scrollHeight;
    }

    function el(name, className, text) {
      const node = document.createElement(name);
      if (className) node.className = className;
      if (text !== undefined && text !== null) node.textContent = text;
      return node;
    }

    function splitTableRow(line) {
      const trimmed = line.trim();
      if (!trimmed.includes("|")) return null;
      const inner = trimmed.replace(/^\|/, "").replace(/\|$/, "");
      const cells = inner.split("|").map((cell) => cell.trim());
      return cells.length >= 2 ? cells : null;
    }

    function isSeparatorRow(line) {
      const cells = splitTableRow(line);
      return Boolean(cells && cells.every((cell) => /^:?-{3,}:?$/.test(cell)));
    }

    function appendParagraph(parent, lines) {
      const text = lines.join("\n").trim();
      if (text) parent.append(el("p", null, text));
    }

    function appendTable(parent, rows) {
      if (rows.length < 2) return false;
      const header = splitTableRow(rows[0]);
      const hasSeparator = isSeparatorRow(rows[1]);
      const bodyStart = hasSeparator ? 2 : 1;
      if (!header || rows.length <= bodyStart) return false;

      const scroll = el("div", "table-scroll");
      const table = el("table", "data-table");
      const thead = el("thead");
      const headRow = el("tr");
      header.forEach((cell) => headRow.append(el("th", null, cell)));
      thead.append(headRow);
      table.append(thead);

      const tbody = el("tbody");
      rows.slice(bodyStart).forEach((line) => {
        const cells = splitTableRow(line);
        if (!cells) return;
        const row = el("tr");
        header.forEach((_, index) => row.append(el("td", null, cells[index] || "")));
        tbody.append(row);
      });
      table.append(tbody);
      scroll.append(table);
      parent.append(scroll);
      return true;
    }

    function richText(text) {
      const container = el("div", "bubble rich");
      const lines = String(text || "").split(/\r?\n/);
      let paragraph = [];
      let index = 0;

      while (index < lines.length) {
        const line = lines[index];
        if (splitTableRow(line)) {
          const tableLines = [];
          while (index < lines.length && splitTableRow(lines[index])) {
            tableLines.push(lines[index]);
            index += 1;
          }
          appendParagraph(container, paragraph);
          paragraph = [];
          if (!appendTable(container, tableLines)) {
            paragraph.push(...tableLines);
          }
          continue;
        }
        paragraph.push(line);
        index += 1;
      }

      appendParagraph(container, paragraph);
      return container;
    }

    function appendUser(text) {
      const wrap = el("div", "message user");
      wrap.append(el("div", "label", "You"));
      wrap.append(el("div", "bubble", text));
      thread.append(wrap);
      scrollBottom();
    }

    function appendTyping() {
      const wrap = el("div", "message assistant");
      wrap.id = "typing";
      wrap.append(el("div", "label", "Agent"));
      const dots = el("div", "typing");
      dots.append(el("span"), el("span"), el("span"));
      wrap.append(dots);
      thread.append(wrap);
      scrollBottom();
    }

    function removeTyping() {
      const typing = document.getElementById("typing");
      if (typing) typing.remove();
    }

    function appendPills(parent, items, prefix) {
      if (!items || !items.length) return;
      const row = el("div", "pill-row");
      items.forEach((item) => row.append(el("span", "pill", prefix ? `${prefix}: ${item}` : item)));
      parent.append(row);
    }

    function appendWarnings(parent, warnings) {
      (warnings || []).forEach((warning) => parent.append(el("div", "warning", warning)));
    }

    function appendSql(parent, sql) {
      if (!sql) return;
      const wrap = el("div", "sql-wrap");
      const bar = el("div", "sql-toolbar");
      bar.append(el("span", null, "SQL draft"));
      const copy = el("button", "copy", "Copy");
      copy.type = "button";
      copy.addEventListener("click", async () => {
        await navigator.clipboard.writeText(sql);
        copy.textContent = "Copied";
        setTimeout(() => copy.textContent = "Copy", 1200);
      });
      bar.append(copy);
      const pre = el("pre");
      const code = el("code", null, sql);
      pre.append(code);
      wrap.append(bar, pre);
      parent.append(wrap);
    }

    function appendResult(data) {
      const wrap = el("div", "message assistant");
      wrap.append(el("div", "label", "Agent"));
      const result = el("div", "result");
      const head = el("div", "result-head");
      head.append(el("h3", "result-title", data.kind === "search" ? "Knowledge base answer" : "SQL draft"));
      if (data.dialect) head.append(el("span", "pill", data.dialect));
      result.append(head);

      if (data.declined) {
        result.append(el("div", "warning decline", data.missing || "The agent declined this request."));
      }
      if (data.answer) result.append(richText(data.answer));
      if (data.explanation) result.append(richText(data.explanation));
      appendPills(result, data.tables_used || [], "table");
      appendPills(result, data.precedent_ids || [], "ERA");
      appendPills(result, data.assumptions || [], "assumption");
      appendPills(result, (data.sources && data.sources.tables) || [], "source table");
      appendPills(result, (data.sources && data.sources.era_precedents) || [], "source ERA");
      appendWarnings(result, data.warnings);
      appendSql(result, data.sql);
      wrap.append(result);
      thread.append(wrap);
      scrollBottom();
    }

    async function submitQuestion(question) {
      appendUser(question);
      input.value = "";
      send.disabled = true;
      send.textContent = "Working";
      appendTyping();
      try {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question }),
        });
        const data = await response.json();
        removeTyping();
        if (!response.ok) throw new Error(data.error || "Request failed");
        appendResult(data);
      } catch (error) {
        removeTyping();
        appendResult({ kind: "error", declined: true, missing: error.message });
      } finally {
        send.disabled = false;
        send.textContent = "Send";
        input.focus();
      }
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const question = input.value.trim();
      if (question) submitQuestion(question);
    });

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });

    document.querySelectorAll(".example").forEach((button) => {
      button.addEventListener("click", () => {
        input.value = button.textContent;
        input.focus();
      });
    });
  </script>
</body>
</html>
"""


def _shared_session():
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                _session = get_session()
    return _session


def generate(question: str) -> Text2SQLResult | SearchResult:
    """Run the existing orchestrator with a process-wide Bedrock session."""
    return orchestrate(question, session=_shared_session())


def result_to_payload(result: Text2SQLResult | SearchResult) -> dict:
    """Convert agent result models into browser-friendly JSON."""
    if isinstance(result, SearchResult):
        return {
            "kind": "search",
            "answer": result.answer,
            "sources": result.sources,
            "found": result.found,
            "warnings": result.warnings,
            "declined": result.declined,
            "missing": result.missing,
        }
    return {
        "kind": "sql",
        "sql": result.sql,
        "explanation": result.explanation,
        "tables_used": result.tables_used,
        "columns_used": result.columns_used,
        "assumptions": result.assumptions,
        "precedent_ids": result.precedent_ids,
        "dialect": result.dialect,
        "declined": result.declined,
        "missing": result.missing,
        "warnings": result.warnings,
    }


def create_handler(
    generate_fn: Callable[[str], Text2SQLResult | SearchResult] = generate,
) -> type[BaseHTTPRequestHandler]:
    """Build a request handler with an injectable generator for tests."""

    class ChatHandler(BaseHTTPRequestHandler):
        server_version = "Text2SQLWeb/1.0"

        def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self) -> None:
            body = INDEX_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = urlparse(self.path).path
            if path == "/":
                self._send_html()
                return
            if path == "/healthz":
                self._send_json({"ok": True})
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            path = urlparse(self.path).path
            if path != "/api/chat":
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json({"error": "Invalid Content-Length"}, HTTPStatus.BAD_REQUEST)
                return

            if length <= 0 or length > 64_000:
                self._send_json({"error": "Request body must be 1-64000 bytes"}, HTTPStatus.BAD_REQUEST)
                return

            try:
                raw = self.rfile.read(length)
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json({"error": "Request body must be valid JSON"}, HTTPStatus.BAD_REQUEST)
                return

            question = str(data.get("question", "")).strip()
            if not question:
                self._send_json({"error": "Question is required"}, HTTPStatus.BAD_REQUEST)
                return

            try:
                _log.info("web request RECEIVED | question=%r", question[:120])
                payload = result_to_payload(generate_fn(question))
            except Exception as exc:  # noqa: BLE001 - return a browser-readable error
                _log.exception("web request FAILED | %s: %s", type(exc).__name__, exc)
                self._send_json(
                    {"error": f"{type(exc).__name__}: {exc}"},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            self._send_json(payload)

        def log_message(self, fmt: str, *args) -> None:
            _log.info("http | " + fmt, *args)

    return ChatHandler


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), create_handler())
    url = f"http://{host}:{server.server_port}"
    print(f"Text-to-SQL web UI running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web UI.")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Text-to-SQL web chat UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind. Defaults to 127.0.0.1.")
    parser.add_argument("--port", default=8000, type=int, help="Port to bind. Defaults to 8000.")
    args = parser.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
