import { SqlBlock } from "frontend";

// SqlBlock renders a draft SQL query with the standing "unverified — not
// executed" warning, plus Copy / Edit affordances. It is the agent's primary
// output surface. The amber chrome is deliberate: a draft can never be mistaken
// for a validated query.

const MONTHLY_CC = `-- UNVERIFIED_DRAFT — review tables, joins, and filters before running.
SELECT
  DATE_TRUNC('month', t.trx_date) AS bulan,
  COUNT(*)                        AS jumlah_transaksi,
  SUM(t.trx_amount)               AS total_nominal
FROM edw.core.fct_cc_transaction_daily t
WHERE t.trx_date >= DATE '2025-01-01'
GROUP BY 1
ORDER BY 1;`;

const TOP_MERCHANT = `-- UNVERIFIED_DRAFT
SELECT m.merchant_name, SUM(t.trx_amount) AS belanja
FROM edw.core.fct_cc_transaction_daily t
JOIN edw.ref.dim_merchant m ON m.merchant_id = t.merchant_id
GROUP BY m.merchant_name
ORDER BY belanja DESC
LIMIT 10;`;

export const MonthlyCreditCardTxn = () => <SqlBlock sql={MONTHLY_CC} />;

export const TopMerchants = () => <SqlBlock sql={TOP_MERCHANT} />;
