# BRISA Prototype — Unit 0: Live-KB Inspection & Demo Dataset

> Produced by `scripts/inspect_kb.py` against the live KB (2026-07-16). This is the
> *Resolve Before Planning* artifact from the plan; it sets the realistic card/facet shape
> for Units 2/6 and the demo script.

## 1. KB metadata reality (measured)

| Signal | Finding | Implication |
|---|---|---|
| `schema_tables` rows | **4,438** (full catalog, not the 200-row sample) | Real scale; demo has depth |
| `schema_tables` columns | `id, table_name, table_description, domain_tags, column_names, columns_dict, n_columns, ai_generated, source_schema, source_type, dense_text, …` | `domain_tags`, `columns_dict`, `column_names` all present (per RETRIEVAL.md) |
| `domain_tags` populated | **48.4%** (2,146/4,438), 23 distinct domains | R8 facet is viable; ~52% have no domain → need an "uncategorized/hidden" bucket |
| Table-level business title | **No such column** | R5 headline falls back to a humanized `table_name` (as planned) |
| `[AI]` boilerplate descriptions | **0%** | The sample's `[AI]` boilerplate fear does **not** apply to the full KB — descriptions are real |
| `tid<N>` table ids | **0%** in both `schema_tables` and `schema_columns` | The `tid<N>` artifact was sample-only; hydration tolerance is defensive, not needed on this KB |
| `schema_columns` rows | **131,419** | — |
| Column `business_title` filled | **90.5%** (118,894/131,419) | Rich column metadata → strong R7 column dictionary |
| PII column-name fragment hits | **2,533** (`account_no`, `address`, `addr_email`, `addr_phone_*`, `card`, …) | R5a badge is highly relevant; heuristic is broad (survey-named columns cause false positives) |
| Denylist reachability | `era_tickets`, `era_tickets_vec`, `era_tickets_descpseudo` → **blocked at DB layer (42P01)** | **R4a is defense-in-depth** — the read-only role already cannot see denylisted tables (resolves the deferred question) |

**Net:** metadata quality is far better than the 200-row sample implied. The two big fears
(`[AI]` boilerplate, `tid<N>` unresolved ids) are non-issues on the full KB.

## 2. Domains (for the R8 facet)

`approval`, `bank indonesia`, `brimo`, `brispot`, `crm`, `fasilitas`, `general ledger`,
`historis`, `kartu`, `kurs/mata uang`, `log/audit`, `merchant`, `nasabah`, `pembayaran`,
`pengajuan/request`, `pinjaman`, `produk`, `program`, `rekening/account`, `saldo`, `swift`,
`transaksi`, `unit kerja/cabang`.

Facet backing: `WHERE %s = ANY(domain_tags)` (GIN-indexed). Untagged tables (~52%) group under
an explicit "Tanpa domain / Uncategorized" bucket rather than disappearing.

## 3. Retrieval sanity check (unlabeled)

`hybrid_search('schema_tables', q, limit=5)` on representative Indonesian queries — top hits are
clearly on-topic across every domain tried:

| Query | Top-5 highlights (top cosine) |
|---|---|
| pinjaman KUR mikro per bulan | `sikp_mth_kur_billing`, `dash_mikro_…_potensi_angsuran_summary_*`, `cbasslik_slik_fasilitas_kredit_2_mikro_sme` (0.55) |
| transaksi kartu kredit nasabah | `…raw_kartu_kredit_details`, `1900_priority_t_detail_transaksi_kartu_kredit`, `antasena_…_trx_kartu_kredit` (0.63) |
| saldo rekening tabungan harian | `ratas_harian_simpanan_daily`, `…fct_tabungan`, `…tabungan_giro_saldo_debet` (0.66) |
| data cabang unit kerja | `dim_branch_jbr`, `par_dly_divisi_unit_kerja_cpa`, `…raw_ukers` (0.67) |
| kurs mata uang harian | `…as4_jhfxht`, `mchanger_mc_t_kurs_dasar`, `…as4_amlfxrat` (0.59) |

## 4. KPI decision gate (plan Unit 0)

- The plan's gate is "target table in search top-5 for ≥80%". A **formally scored** hit rate
  needs labeled `(question → expected table)` ground truth, which does **not** exist in the repo
  (the analyst survey captures pain points, not labeled retrieval targets). Building a labeled
  eval set is the origin doc's R20 (eval harness) — out of this prototype's scope.
- **Decision:** the unlabeled sanity check shows clearly relevant top-5 results across all five
  domains sampled, and metadata quality is high. **No re-scope of Units 2/6 is required.** The
  formal ≥80% KPI is **deferred** to a later analyst-labeled evaluation; the prototype proceeds on
  the strength of the qualitative check. This is recorded here so the KPI is not silently claimed.

## 5. Curated demo question set (bilingual)

For the stakeholder demo and the search→agent flow. Each is answerable against the KB above.

1. Cari tabel transaksi kartu kredit nasabah / *Find credit-card transaction tables*
2. Saldo rekening tabungan harian per nasabah / *Daily savings-account balances per customer*
3. Data angsuran pinjaman KUR mikro / *KUR micro-loan installment data*
4. Tabel kurs mata uang harian / *Daily FX-rate tables*
5. Data cabang dan unit kerja BRI / *BRI branch & work-unit data*
6. Transaksi pembayaran melalui BRImo / *Payment transactions via BRImo*
7. Data merchant dan akuisisi merchant / *Merchant & merchant-acquisition data*
8. Fasilitas kredit dan kolektibilitas (SLIK) / *Credit facilities & collectibility (SLIK)*
9. Data nasabah prioritas / *Priority-customer data*
10. Rekening giro dan saldo debet harian / *Current accounts & daily debit balances*
11. Data general ledger dan jurnal / *General-ledger & journal data*
12. Transaksi kartu debit di ATM / *Debit-card transactions at ATM*

> To later compute a real top-5 KPI, append a tab + expected table id to any line and run
> `python scripts/inspect_kb.py --questions <this-list>`; the script computes the hit rate and the
> ≥80% gate.

## 6. Fallback card/facet shape (for Units 2/6)

- **Headline:** humanized `table_name` (no table-level business title). Humanize by replacing
  `_`/schema prefixes with spaces and title-casing where sensible; keep `schema.table_name` as the
  monospace subtitle.
- **Domain facet:** populated from `domain_tags`; untagged tables under an explicit
  "Tanpa domain" bucket; single-select for the prototype (counts/multi-select are P2).
- **Columns (R7):** source from `schema_columns` (business_title 90.5% filled) or
  `schema_tables.columns_dict`.
- **PII (R5a):** badge columns whose names match `gates.RESTRICTED_FRAGMENTS`; render
  `pii_unclassified` elsewhere. Given the locked-down demo posture accepts schema visibility, the
  prototype **badges** rather than withholds column names (revisit for production).
