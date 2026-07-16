// Typed client for the Sage FastAPI backend. All calls are client-side (SPA), matching the
// locked-down-demo posture: an optional shared bearer token from NEXT_PUBLIC_API_TOKEN.

// NEXT_PUBLIC_API_TOKEN / NEXT_PUBLIC_API_BASE names are intentionally kept as-is. They must
// stay in lockstep with the backend: NEXT_PUBLIC_API_TOKEN has to match the server's
// SAGE_API_TOKEN (else auth silently opens or rejects), and the base must target the Sage API.
const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
const TOKEN = process.env.NEXT_PUBLIC_API_TOKEN;

export type PiiStatus = "present" | "unclassified";

export interface SearchCard {
  id: string;
  table_name: string;
  physical_name: string;
  headline: string;
  description: string;
  domain_tags: string[];
  n_columns: number | null;
  pii: PiiStatus;
}

export interface SearchResponse {
  query: string;
  domain: string | null;
  results: SearchCard[];
  filter_caused_empty: boolean;
  closest_related?: SearchCard[];
}

export interface ColumnInfo {
  field_name: string;
  business_title: string;
  description: string;
  data_type: string;
  pii: boolean;
}

export interface TableDetail {
  card: SearchCard;
  columns: ColumnInfo[];
}

export interface GroundingEntry {
  name: string;
  in_kb: boolean;
  retrieved: boolean;
}

export type GroundingStrength = "precedent_strong" | "schema_only" | null;

export interface AgentResponse {
  kind: string;
  sql: string | null;
  explanation: string | null;
  assumptions: string[];
  tables_used: string[];
  columns_used: string[];
  precedent_ids: string[];
  dialect: string | null;
  declined: boolean;
  missing: string | null;
  warnings: string[];
  grounding: GroundingEntry[];
  grounding_strength: GroundingStrength;
  era_top_cosine: number | null;
  schema_top_cosine: number | null;
}

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function headers(): HeadersInit {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (TOKEN) h["Authorization"] = `Bearer ${TOKEN}`;
  return h;
}

async function handle<T>(resp: Response): Promise<T> {
  if (resp.ok) return (await resp.json()) as T;
  let code = `http_${resp.status}`;
  let message = resp.statusText;
  try {
    const body = await resp.json();
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
    }
  } catch {
    /* non-JSON error body */
  }
  throw new ApiError(resp.status, code, message);
}

export async function searchTables(
  q: string,
  domain?: string | null,
  limit = 12,
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  if (domain) params.set("domain", domain);
  return handle(await fetch(`${BASE}/api/search?${params}`, { headers: headers() }));
}

export async function tableColumns(table: string): Promise<{ table: string; columns: ColumnInfo[] }> {
  const params = new URLSearchParams({ table });
  return handle(await fetch(`${BASE}/api/search/columns?${params}`, { headers: headers() }));
}

export async function tableDetail(id: string): Promise<TableDetail> {
  const params = new URLSearchParams({ id });
  return handle(await fetch(`${BASE}/api/search/table?${params}`, { headers: headers() }));
}

export async function listDomains(): Promise<{ domains: string[] }> {
  return handle(await fetch(`${BASE}/api/search/domains`, { headers: headers() }));
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export async function agentChat(
  question: string,
  attached_tables: string[] = [],
  history: ChatMessage[] = [],
): Promise<AgentResponse> {
  return handle(
    await fetch(`${BASE}/api/agent/chat`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ question, attached_tables, history }),
    }),
  );
}
