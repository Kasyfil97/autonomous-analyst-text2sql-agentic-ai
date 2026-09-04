// Download the latin + latin-ext Instrument Sans + JetBrains Mono woff2 files
// referenced by the Google Fonts CSS and emit a self-contained @font-face
// stylesheet pointing at the local copies under ./.ds-fonts/. Latin-only app →
// other subsets dropped.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { execFileSync } from "node:child_process";

const css = readFileSync(".ds-fonts-src.css", "utf8");
mkdirSync(".ds-fonts", { recursive: true });

// Split into per-@font-face blocks, each preceded by a /* subset */ comment.
const blocks = css.split(/(?=\/\* )/).filter((b) => b.includes("@font-face"));
const keep = [];
let n = 0;
for (const b of blocks) {
  const subset = /\/\*\s*([\w-]+)\s*\*\//.exec(b)?.[1] ?? "";
  if (subset !== "latin" && subset !== "latin-ext") continue;
  const fam = /font-family:\s*'([^']+)'/.exec(b)[1];
  const weight = /font-weight:\s*(\d+)/.exec(b)[1];
  const url = /url\((https:[^)]+\.woff2)\)/.exec(b)[1];
  const slug = fam.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const file = `${slug}-${weight}-${subset}.woff2`;
  execFileSync("curl", ["-s", "-m", "30", "-o", `.ds-fonts/${file}`, url]);
  keep.push(
    `@font-face {\n  font-family: '${fam}';\n  font-style: normal;\n  font-weight: ${weight};\n  font-display: swap;\n  src: url(./.ds-fonts/${file}) format('woff2');\n}`,
  );
  n++;
}

const out =
  keep.join("\n") +
  `\n:root {\n  --font-instrument-sans: 'Instrument Sans', ui-sans-serif, system-ui, sans-serif;\n  --font-jetbrains-mono: 'JetBrains Mono', ui-monospace, "JetBrains Mono", Consolas, monospace;\n}\n`;
writeFileSync(".ds-fonts.css", out);
console.error(`downloaded ${n} woff2, wrote .ds-fonts.css`);
