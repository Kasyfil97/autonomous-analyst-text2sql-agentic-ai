// Bundle entry for design-sync. Re-exports the BRISA shared components so the
// converter can build a standalone IIFE (window.Brisa.*). Dot-prefixed → the
// Next.js app build ignores it. Next.js imports inside these components are
// shimmed at bundle time via .ds-tsconfig.json paths.
export { Sidebar } from "./components/Sidebar";
export { SqlBlock } from "./components/SqlBlock";
export { TableCard } from "./components/TableCard";
export { AppStateProvider } from "./components/AppState";
