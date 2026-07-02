"""Web UI tests: serialization + request handling without live model calls."""
from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from text2sql.agent import Text2SQLResult
from text2sql.search_agent import SearchResult
from text2sql.web import create_handler, result_to_payload


def test_result_to_payload_sql():
    payload = result_to_payload(
        Text2SQLResult(
            sql="-- UNVERIFIED\nSELECT 1",
            explanation="does x",
            tables_used=["table_a"],
            precedent_ids=["ERA25-1"],
            dialect="SparkSQL",
        )
    )

    assert payload["kind"] == "sql"
    assert payload["sql"].endswith("SELECT 1")
    assert payload["tables_used"] == ["table_a"]
    assert payload["precedent_ids"] == ["ERA25-1"]
    assert payload["dialect"] == "SparkSQL"


def test_result_to_payload_search():
    payload = result_to_payload(
        SearchResult(
            answer="found it",
            found=True,
            sources={"tables": ["table_a"], "era_precedents": ["ERA25-1"]},
            warnings=["review source"],
        )
    )

    assert payload["kind"] == "search"
    assert payload["answer"] == "found it"
    assert payload["sources"]["tables"] == ["table_a"]
    assert payload["warnings"] == ["review source"]


def test_chat_endpoint_returns_generated_payload():
    calls = []

    def fake_generate(question):
        calls.append(question)
        return Text2SQLResult(sql="SELECT 1", dialect="SparkSQL")

    server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(fake_generate))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port)
        conn.request(
            "POST",
            "/api/chat",
            body=json.dumps({"question": "make sql"}),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert response.status == 200
    assert calls == ["make sql"]
    assert payload["kind"] == "sql"
    assert payload["sql"] == "SELECT 1"


def test_chat_endpoint_rejects_blank_question():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        create_handler(lambda _q: Text2SQLResult(sql="SELECT 1")),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", server.server_port)
        conn.request(
            "POST",
            "/api/chat",
            body=json.dumps({"question": "   "}),
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert response.status == 400
    assert payload["error"] == "Question is required"
