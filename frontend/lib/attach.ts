// Pure, testable client-side attach logic for the agent context.
//
// The backend caps `attached_tables` at 10 and de-dupes (see `text2sql/api.py` request model
// `max_length=10`). This mirrors that cap client-side so the UI never lets a user build an
// over-cap attachment set, and reports which of the three outcomes occurred so the caller can
// surface the right feedback (drop-success animation, already-attached notice, cap-reached
// notice). Used by BOTH attach paths — the "Kirim ke agent" button and drag-and-drop — so they
// agree on cap/dedupe behaviour.

/** Client-side cap on attached tables, mirroring the backend `max_length=10`. */
export const MAX_ATTACHED = 10;

/**
 * The `dataTransfer` type used to carry a table name from a dragged result card to the agent
 * drop target. A custom MIME keeps our payload distinct from arbitrary text drops. Shared by the
 * drag source (TableCard) and the drop target (AgentPane) so they agree on the key.
 */
export const ATTACH_DND_TYPE = "application/x-sage-table";

export type AttachResult = "added" | "duplicate" | "cap";

/**
 * Attempt to add `table` to `current`, enforcing dedupe AND the cap of MAX_ATTACHED.
 *
 * - "added":     table appended (order preserved), `next` is a new array.
 * - "duplicate": table already present — `next` is `current` unchanged (referentially equal).
 * - "cap":       already at MAX_ATTACHED distinct tables — `next` is `current` unchanged.
 *
 * Dedupe is checked before the cap so re-attaching an already-present table at cap still reads as
 * a harmless "duplicate", not a "cap" rejection.
 */
export function addAttached(
  current: string[],
  table: string,
): { next: string[]; result: AttachResult } {
  if (current.includes(table)) {
    return { next: current, result: "duplicate" };
  }
  if (current.length >= MAX_ATTACHED) {
    return { next: current, result: "cap" };
  }
  return { next: [...current, table], result: "added" };
}

/**
 * Centralized attach-button label for a given outcome (or `null` before any attempt). The cap copy
 * derives its number from `MAX_ATTACHED` so it can never drift from the actual cap. Indonesian copy.
 */
export function attachResultLabel(result: AttachResult | null): string {
  switch (result) {
    case "added":
      return "Ditambahkan ke agent ✓";
    case "duplicate":
      return "Sudah terlampir ✓";
    case "cap":
      return `Maksimal ${MAX_ATTACHED} tabel`;
    default:
      return "Kirim ke agent ↗";
  }
}
