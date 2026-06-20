"""Client for the bge-m3 embedding service (OpenAI-compatible) and Postgres config.

Reads connection settings from the environment / .env. Keeps secrets out of the
source files.
"""

import os
import time

import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


EMBED_URL = os.getenv("EMBED_URL", "http://10.213.191.187:8001/v1/embeddings")
EMBED_TOKEN = os.getenv("EMBED_TOKEN", "")
EMBED_MODEL = os.getenv("EMBED_MODEL", "/data/bge-m3")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))


def pg_config(readonly=False):
    """Return kwargs for psycopg2.connect() from the environment.

    With ``readonly=True`` the connection uses the dedicated least-privilege role
    (``PG_RO_USER``/``PG_RO_PASSWORD``); see ``docs/setup-readonly-role.md``. The agent
    path must use this so it can never write or reach ``era_tickets``. We deliberately
    raise rather than silently fall back to the superuser when the RO env is missing.
    Build/ingest scripts keep using the default (superuser) ``pg_config()``.
    """
    cfg = {
        "host": os.getenv("PG_HOST", "localhost"),
        "port": int(os.getenv("PG_PORT", "5433")),
        "dbname": os.getenv("PG_DBNAME", "postgres"),
    }
    if readonly:
        ro_user = os.getenv("PG_RO_USER")
        if not ro_user:
            raise RuntimeError(
                "pg_config(readonly=True) requires PG_RO_USER (and PG_RO_PASSWORD). "
                "Create the read-only role first — see docs/setup-readonly-role.md. "
                "Refusing to fall back to the superuser for agent access."
            )
        cfg["user"] = ro_user
        cfg["password"] = os.getenv("PG_RO_PASSWORD", "")
    else:
        cfg["user"] = os.getenv("PG_USER", "postgres")
        cfg["password"] = os.getenv("PG_PASSWORD", "postgres")
    return cfg


PG_TABLE = os.getenv("PG_TABLE", "era_tickets")


def embed(texts, batch_size=64, max_retries=4, timeout=60):
    """Embed a list of strings, returning a list of 1024-dim float vectors.

    Batches requests to the service and retries transient failures with backoff.
    Order is preserved (the service returns an 'index' per item).
    """
    if isinstance(texts, str):
        texts = [texts]
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {EMBED_TOKEN}",
    }
    out = []
    for start in range(0, len(texts), batch_size):
        chunk = [t if (t and t.strip()) else " " for t in texts[start:start + batch_size]]
        payload = {"model": EMBED_MODEL, "input": chunk}
        last_err = None
        for attempt in range(max_retries):
            try:
                resp = requests.post(EMBED_URL, headers=headers, json=payload,
                                     timeout=timeout)
                resp.raise_for_status()
                data = resp.json()["data"]
                data.sort(key=lambda d: d["index"])
                out.extend(d["embedding"] for d in data)
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if attempt == max_retries - 1:
                    raise RuntimeError(
                        f"embed failed for batch at {start}: {exc}") from exc
                time.sleep(2 ** attempt)
    return out


def embed_one(text):
    """Embed a single string -> one vector."""
    return embed([text])[0]
