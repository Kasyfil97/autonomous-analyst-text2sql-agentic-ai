import { TableCard } from "frontend";

// TableCard is the search-result unit: a clickable table summary with domain
// tags, a PII/sensitivity badge, column count, and an "ask the agent" action.
// The card is a link to the table detail page; the badge encodes data
// sensitivity (present PII vs. not-yet-classified).

const noop = () => {};

const WITH_PII_CARD = {
  id: "1",
  table_name: "kartu_kredit_transaksi",
  physical_name: "edw.core.fct_cc_transaction_daily",
  headline: "Transaksi Kartu Kredit Harian",
  description:
    "Rincian transaksi kartu kredit per nasabah per hari — nominal, merchant, dan kanal. Sumber untuk analisis belanja dan deteksi anomali.",
  domain_tags: ["Kartu Kredit", "Transaksi", "Ritel"],
  n_columns: 42,
  pii: "present" as const,
};

const UNCLASSIFIED_CARD = {
  id: "2",
  table_name: "nasabah_saldo_bulanan",
  physical_name: "edw.mart.dim_customer_balance_monthly",
  headline: "Saldo Nasabah Bulanan",
  description:
    "Agregat saldo rata-rata dan akhir bulan per nasabah tabungan dan giro.",
  domain_tags: ["Nasabah", "Simpanan"],
  n_columns: 18,
  pii: "unclassified" as const,
};

const MINIMAL_CARD = {
  id: "3",
  table_name: "kode_cabang",
  physical_name: "edw.ref.dim_branch",
  headline: "Referensi Kode Cabang",
  description: "",
  domain_tags: ["Referensi"],
  n_columns: null,
  pii: "unclassified" as const,
};

export const WithPII = () => <TableCard card={WITH_PII_CARD} onAsk={noop} />;

export const Unclassified = () => <TableCard card={UNCLASSIFIED_CARD} onAsk={noop} />;

export const Minimal = () => <TableCard card={MINIMAL_CARD} onAsk={noop} />;
