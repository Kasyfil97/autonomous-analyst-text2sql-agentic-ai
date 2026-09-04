// Compile the app's Tailwind v4 globals.css into a standalone stylesheet the
// design-sync bundle can ship. Lives in frontend/ so @tailwindcss/postcss and
// its oxide scanner resolve, and so content auto-detection sees the component
// sources. Output → frontend/.ds-styles.css (dot-prefixed → Next ignores).
import postcss from "postcss";
import tailwind from "@tailwindcss/postcss";
import { readFileSync, writeFileSync, existsSync } from "node:fs";

const input = readFileSync("app/globals.css", "utf8");
const result = await postcss([tailwind()]).process(input, {
  from: "app/globals.css",
  to: ".ds-styles.css",
});
// Append the self-hosted Instrument Sans + JetBrains Mono @font-face rules + the
// --font-instrument-sans / --font-jetbrains-mono var definitions (next/font sets
// these at runtime in the app; the standalone bundle must supply them itself).
const fonts = existsSync(".ds-fonts.css") ? "\n" + readFileSync(".ds-fonts.css", "utf8") : "";
const css = result.css + fonts;
writeFileSync(".ds-styles.css", css);
console.error(`wrote .ds-styles.css (${css.length} bytes${fonts ? ", incl. fonts" : ""})`);
