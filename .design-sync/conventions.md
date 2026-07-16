# BRISA (Sage) — how to build with this design system

BRISA is a calm, institutional data-console design system for an internal BRI
tool: semantic data search plus a draft-SQL AI agent. It is a small, focused kit
— four React components plus a token + type foundation. Indonesian-first UI copy.

## Setup

- Components are plain React (client components). **No provider wrapper is
  required** to render them — import and use directly.
- All styling comes from `styles.css` (imported once at the app root). It defines
  the design tokens (`:root` custom properties), the IBM Plex font faces, and the
  compiled component styles. Without it, components render unstyled.
- `AppStateProvider` is app infrastructure (a React context holding cross-route
  search/agent state), not a visual component — mount it once at the root only if
  you need that shared state; it renders nothing itself.

## Styling idiom — Tailwind v4 utilities + design tokens

Style your own layout with **Tailwind v4 utility classes**. Brand colors are
**CSS custom-property tokens**, referenced from utilities via arbitrary values,
e.g. `bg-[color:var(--color-accent)]`, `text-[color:var(--color-muted)]`,
`border-[color:var(--color-line)]`. Always use the tokens for color — never
hard-code hexes — so the palette stays consistent.

Color tokens (all `var(--…)`):

| Token | Role |
|---|---|
| `--color-bg` | app background (slate) |
| `--color-panel` / `--color-panel-2` | surfaces (white / raised) |
| `--color-ink` | primary text (near-black navy) |
| `--color-muted` | secondary text |
| `--color-line` | borders / hairlines |
| `--color-accent` | teal — the single trust accent (primary actions, active nav) |
| `--color-accent-2` | blue — links / "view detail" |
| `--color-warn` / `--color-warn-bg` / `--color-warn-line` | amber — the *unverified draft* state ONLY |
| `--color-danger` | red — destructive / clear |
| `--color-code` | dark code-block background |

Type tokens: `--font-sans` (IBM Plex Sans) for everything, `--font-mono`
(IBM Plex Mono) for SQL, physical table names, and code. Numeric columns use
tabular figures.

**Semantic rule that defines this system:** amber (`--color-warn*`) is reserved
exclusively for the "unverified / not executed" draft state — never as a generic
highlight. Teal is trust; green is deliberately avoided as a hero color so a
draft can never read as validated.

## Components

- **Sidebar** — fixed app-shell nav: brand lockup + primary destinations +
  a pinned draft-only safety notice. Marks the active route.
- **TableCard** — a search-result unit: table headline, physical name (mono),
  domain tags, a PII / "sensitivity not classified" badge, column count, and an
  "ask the agent" action. `card: SearchCard`, `onAsk(tableName)`.
- **SqlBlock** — draft-SQL output with the standing amber "unverified — not
  executed" chrome and Copy / Edit controls. `sql: string`.

Read the bound `styles.css` and each component's `<Name>.d.ts` / `<Name>.prompt.md`
before composing.

## Idiomatic snippet

```tsx
import { TableCard } from "<pkg>";

<section className="grid gap-3 bg-[color:var(--color-bg)] p-8">
  <p className="text-xs text-[color:var(--color-muted)]">12 tabel · diurutkan relevansi</p>
  {results.map((c) => (
    <TableCard key={c.id} card={c} onAsk={(t) => askAgent(t)} />
  ))}
</section>
```
