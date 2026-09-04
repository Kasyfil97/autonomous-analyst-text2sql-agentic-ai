#!/usr/bin/env python3
"""
Build an embedding-ready corpus from ERA22_26_raw_cleaned.xlsx.

For each ERA ticket we distill the noisy request letter into a retrieval-shaped
document so that a short natural-language user need (e.g. "Retail CIF Selindo &
Retail Giro Selindo terkini") matches the right precedent under dense+sparse search.

Per row we produce (all in ONE LLM call):
  - canonical_need       : 1-2 clean sentences stating the DATA NEED (for DENSE)
  - synthetic_questions  : 3 paraphrases of how a user might ask (multi-vector DENSE)
  - keywords             : high-value domain/entity/report tokens (for SPARSE/BM25)
  - key_filters          : the dimensions a query is parameterized on — the knobs an
                           analyst adapts when reusing a precedent (branch, position_date,
                           account_number, segment, report_code, …). Case A/B of the flow.
  - description_clean    : Description stripped of salutations / PII / letter refs
  - payload fields       : issue_key, query_final, langkah, pseudocode, comment,
                           domain_tags, has_solution  (returned, NOT embedded)

The expensive distillation (canonical_need + synthetic_questions + keywords +
key_filters) is done by the Bedrock LLM in a single pass — the prompt sees the request
AND the final query, since key_filters are most visible in the solution. Output is
written incrementally to JSONL (resumable) and exported to CSV for inspection.
Vectorization (dense/sparse) happens later at ingest time.

(This script merges the former standalone `enrich_key_filters.py`, which is retired.)

Usage:
  python -m preprocessing.build_era_embedding_corpus --limit 20      # test run
  python -m preprocessing.build_era_embedding_corpus                 # full run
  python -m preprocessing.build_era_embedding_corpus --csv-only      # rebuild CSV from JSONL
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd

# Make repo root importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bedrock_session import BedrockSession, _strip_reasoning  # noqa: E402

HERE = Path(__file__).resolve().parent
INPUT_XLSX = HERE / "ERA22_26_raw_cleaned.xlsx"
OUT_JSONL = HERE / "era_embedding_corpus.jsonl"
OUT_CSV = HERE / "era_embedding_corpus.csv"

# Rows whose Query_Final is one of these are "no real solution" (analyst put it in
# a Jira comment box instead). We still index them as *needs*, but flag has_solution.
_NO_SOLUTION_MARKERS = ("comment box", "on comment box", "nan", "")

# ---------------------------------------------------------------------------
# Rule-based cleaning of the raw request letter
# ---------------------------------------------------------------------------

_PHONE_RE = re.compile(r"\b(?:\+62|62|0)8[\d\-\s]{7,13}\d\b")
_LETTER_REF_RE = re.compile(r"\bB\.?\s?\d[\w.\-/]+", re.IGNORECASE)  # e.g. B.136.e-ASQ/LOS/09/2025
_PN_RE = re.compile(r"\b(PN|PIC|Requestor)\b.*", re.IGNORECASE)
_GREETING_RE = re.compile(
    r"(dengan\s+hormat|dengan\s+ini|demikian.*?(disampaikan|terima\s+kasih)|"
    r"atas\s+(bantuan|perhatian).*?(terima\s+kasih)?|kami\s+ucapkan\s+terima\s+kasih|"
    r"mohon\s+bantuan(?:nya)?|bersama\s+ini)",
    re.IGNORECASE | re.DOTALL,
)


def clean_description(text: str) -> str:
    """Strip salutations, closings, PIC names, phone numbers, and letter refs."""
    if not text:
        return ""
    t = text
    t = _PHONE_RE.sub("", t)
    t = _LETTER_REF_RE.sub("", t)
    t = _GREETING_RE.sub(" ", t)
    # Drop obvious PIC/PN/contact lines.
    lines = []
    for ln in t.splitlines():
        low = ln.lower()
        if any(k in low for k in ("nomor hp", "no. hp", "no hp", "menghubungi sdr",
                                  "menghubungi saudara", "narahubung", "contact person")):
            continue
        lines.append(ln)
    t = "\n".join(lines)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


# ---------------------------------------------------------------------------
# PII redaction — keep the corpus safe AND better-generalizing. Specific account
# numbers / customer names never appear in a future user query, so masking them
# removes both a leak risk and useless rare tokens. Patterns mirror text2sql/tools.py.
# ---------------------------------------------------------------------------

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_GROUPED_PII = re.compile(r"(?<![\d-])(?:\d[ .-]?){12,}\d(?![\d-])")  # NIK/NPWP/card
_LONG_DIGITS = re.compile(r"(?<!\d)\d{9,}(?!\d)")                      # account numbers
# Entity/person names introduced by an account-holder marker: "an CV SAHABAT SETIA".
_ACCT_HOLDER = re.compile(r"\ba[/.]?n\.?\s+((?:CV|PT|PD|UD|KOPERASI|KSP|KSU)\b[^\n,.]*|[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})",
                          re.IGNORECASE)
# A keyword token is dropped if it is a bare digit run or an obvious entity name.
_KW_ENTITY = re.compile(r"^(CV|PT|PD|UD|KOPERASI|KSP|KSU)[_\s]", re.IGNORECASE)
_KW_DIGITS = re.compile(r"^\d[\d_\-]{6,}$")


def redact_pii(text: str) -> str:
    """Mask emails, account numbers, NIK/NPWP, and account-holder names."""
    if not text:
        return text
    text = _EMAIL.sub("<EMAIL>", text)
    text = _ACCT_HOLDER.sub("an <NASABAH>", text)
    text = _GROUPED_PII.sub("<ID>", text)
    text = _LONG_DIGITS.sub("<NOREK>", text)
    return text


def redact_keywords(keywords: list[str]) -> list[str]:
    """Drop keyword tokens that are specific account numbers or customer names."""
    _placeholders = {"<NASABAH>", "<NOREK>", "<ID>", "<EMAIL>"}
    out = []
    for k in keywords:
        if k in _placeholders or _KW_DIGITS.match(k) or _KW_ENTITY.match(k):
            continue
        # Mask any embedded long digit run but keep the (usually report-code) token.
        k2 = _LONG_DIGITS.sub("<NOREK>", k)
        if k2 and k2 != "<NOREK>":
            out.append(k2)
    return out


def parse_domain(raw) -> list[str]:
    """Parse the Data_Domain cell, stored as a python-list string like "['GIRO','CIF']"."""
    if raw is None:
        return []
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return []
    try:
        val = ast.literal_eval(s)
        if isinstance(val, (list, tuple)):
            return [str(x).strip().upper() for x in val if str(x).strip()]
    except (ValueError, SyntaxError):
        pass
    return [tok.strip().upper() for tok in re.split(r"[,\|]", s) if tok.strip()]


def has_real_solution(query_final: str) -> bool:
    q = (query_final or "").strip().lower()
    return q not in _NO_SOLUTION_MARKERS and len(q) > 8


# ---------------------------------------------------------------------------
# LLM distillation
# ---------------------------------------------------------------------------

_SYSTEM = (
    "Anda asisten data engineering di bank. Tugas Anda menyaring tiket permintaan "
    "data (ERA) menjadi representasi ringkas untuk semantic search. "
    "Fokus HANYA pada KEBUTUHAN DATA-nya: entitas, domain, metrik, cakupan, periode. "
    "Abaikan salam, nama/PN/nomor HP PIC, nomor surat, dan basa-basi. "
    "JANGAN cantumkan data pribadi/spesifik: ganti nomor rekening dengan <NOREK>, "
    "nama nasabah/perusahaan dengan <NASABAH>, NIK/NPWP dengan <ID>. "
    "Untuk key_filters: identifikasi DIMENSI PARAMETER yang berubah bila permintaan "
    "serupa diulang (tanggal posisi, rentang tanggal, kode cabang/uker, nomor rekening/"
    "CIF, segmen, jenis produk, kode report, tipe file) — NAMA dimensinya (snake_case "
    "Inggris), bukan nilainya; paling terlihat di QUERY FINAL. "
    "Jawab HANYA dengan satu objek JSON valid, tanpa teks lain."
)

_USER_TMPL = """Dari tiket berikut, hasilkan JSON dengan kunci:
- "canonical_need": 1-2 kalimat Bahasa Indonesia yang menyatakan kebutuhan data secara bersih dan lugas (seperti kalimat kebutuhan, bukan surat).
- "synthetic_questions": array 3 string, parafrase singkat bagaimana pengguna mungkin menanyakan data ini (bahasa sehari-hari, tanpa basa-basi).
- "keywords": array token penting HURUF BESAR untuk pencarian kata-kunci (domain, entitas, nama produk/report/kode tabel bila ada). 5-12 token.
- "key_filters": array 0-8 nama dimensi parameter snake_case Inggris (mis. "position_date","branch_code","account_number","start_date","end_date","segment","product_type","report_code","file_type"). Kosongkan bila permintaan dokumen/manual tanpa parameter jelas.

DOMAIN (tag yang sudah ada): {domain}

DESCRIPTION (permintaan asli, mungkin berisik):
{description}

LANGKAH PENGERJAAN (interpretasi analis, bila ada):
{langkah}

QUERY FINAL (solusi analis, sumber terbaik untuk key_filters, bila ada):
{solution}

Balas hanya JSON."""


def _extract_json(text: str) -> dict | None:
    """Pull the first balanced JSON object out of the model's text."""
    if not text:
        return None
    text = _strip_reasoning(text)
    # Strip code fences if present.
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _rule_based_fallback(desc_clean: str, domain: list[str]) -> dict:
    """Cheap distillation if the LLM fails, so the pipeline never drops a row."""
    need = desc_clean.split("\n\n")[0][:300] if desc_clean else " ".join(domain)
    return {
        "canonical_need": redact_pii(need) or "Permintaan data (tidak terdistilasi).",
        "synthetic_questions": [],
        "keywords": domain,
        "key_filters": [],
    }


def _dedup_lower(items) -> list[str]:
    """Lowercased, order-preserving de-dup (key_filters keep their natural order)."""
    out, seen = [], set()
    for it in items or []:
        t = str(it).strip().lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def distill(session: BedrockSession, description: str, langkah: str,
            domain: list[str], solution: str = "", retries: int = 3) -> tuple[dict, str]:
    """Return (distilled_dict, status). status in {'llm','fallback'}.

    One LLM pass yields canonical_need + synthetic_questions + keywords + key_filters.
    ``solution`` (Query_Final) is fed in because key_filters are most visible there.
    """
    user = _USER_TMPL.format(
        domain=", ".join(domain) or "(tidak ada)",
        description=(description or "")[:4000],
        langkah=(langkah or "")[:1500],
        solution=(solution or "")[:2000],
    )
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]
    for attempt in range(retries):
        try:
            msg = session.invoke(messages, max_tokens=850, temperature=0.0)
            parsed = _extract_json(msg.get("content", ""))
            if parsed and parsed.get("canonical_need"):
                # Normalize shapes + belt-and-suspenders PII redaction over LLM output.
                parsed["canonical_need"] = redact_pii(str(parsed["canonical_need"]).strip())
                parsed["synthetic_questions"] = [
                    redact_pii(str(q).strip())
                    for q in (parsed.get("synthetic_questions") or []) if str(q).strip()
                ]
                kws = [str(k).strip().upper() for k in (parsed.get("keywords") or []) if str(k).strip()]
                # Redact/drop specific tokens, then union in the existing domain tags.
                parsed["keywords"] = sorted(set(redact_keywords(kws)) | set(domain))
                parsed["key_filters"] = _dedup_lower(parsed.get("key_filters"))
                return parsed, "llm"
        except Exception as exc:  # noqa: BLE001
            print(f"    ! invoke attempt {attempt + 1} failed: {type(exc).__name__}: {exc}")
            time.sleep(1.5 * (attempt + 1))
    return _rule_based_fallback(clean_description(description), domain), "fallback"


# ---------------------------------------------------------------------------
# Corpus build
# ---------------------------------------------------------------------------

def load_done_keys() -> set[str]:
    if not OUT_JSONL.exists():
        return set()
    done = set()
    with OUT_JSONL.open(encoding="utf-8") as f:
        for line in f:
            try:
                done.add(json.loads(line)["issue_key"])
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def export_csv() -> None:
    rows = []
    with OUT_JSONL.open(encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not rows:
        print("No rows in JSONL to export.")
        return
    df = pd.DataFrame(rows)
    for col in ("synthetic_questions", "keywords", "key_filters", "domain_tags"):
        if col in df.columns:
            df[col] = df[col].apply(lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v)
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"CSV exported: {OUT_CSV}  ({len(df)} rows)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="process only first N pending rows")
    ap.add_argument("--csv-only", action="store_true", help="rebuild CSV from existing JSONL and exit")
    args = ap.parse_args()

    if args.csv_only:
        export_csv()
        return

    df = pd.read_excel(INPUT_XLSX).fillna("")
    print(f"Loaded {len(df)} rows from {INPUT_XLSX.name}")

    done = load_done_keys()
    if done:
        print(f"Resuming — {len(done)} rows already done, skipping them.")

    session = BedrockSession()

    pending = df[~df["issue_key"].astype(str).isin(done)]
    if args.limit:
        pending = pending.head(args.limit)
    total = len(pending)
    print(f"Processing {total} rows...\n")

    n_llm = n_fb = 0
    with OUT_JSONL.open("a", encoding="utf-8") as out:
        for i, (_, r) in enumerate(pending.iterrows(), 1):
            issue_key = str(r["issue_key"])
            domain = parse_domain(r["Data_Domain"])
            desc_clean = redact_pii(clean_description(str(r["Description"])))
            distilled, status = distill(session, str(r["Description"]),
                                        str(r["Langkah_Pengerjaan"]), domain,
                                        solution=str(r["Query_Final"]))
            n_llm += status == "llm"
            n_fb += status == "fallback"

            record = {
                "issue_key": issue_key,
                "canonical_need": distilled["canonical_need"],
                "synthetic_questions": distilled["synthetic_questions"],
                "keywords": distilled["keywords"],
                "key_filters": distilled["key_filters"],
                "domain_tags": domain,
                "description_clean": desc_clean,
                "has_solution": has_real_solution(str(r["Query_Final"])),
                # payload (not embedded)
                "query_final": str(r["Query_Final"]),
                "langkah_pengerjaan": str(r["Langkah_Pengerjaan"]),
                "pseudocode": str(r["Pseudocode"]),
                "comment": str(r["Comment"]),
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            print(f"[{i}/{total}] {issue_key}  ({status})  {distilled['canonical_need'][:70]!r}")

    print(f"\nDone. LLM={n_llm}  fallback={n_fb}")
    export_csv()


if __name__ == "__main__":
    main()
