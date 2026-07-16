// Bundle entry for design-sync. Re-exports the Sage shared components so the
// converter can build a standalone IIFE (window.Sage.*). Dot-prefixed → the
// Next.js app build ignores it. Next.js imports inside these components are
// shimmed at bundle time via .ds-tsconfig.json paths.
export { SessionsRail } from "./components/SessionsRail";
export { SearchPane } from "./components/SearchPane";
export { AgentPane } from "./components/AgentPane";
export { TableDetailPanel } from "./components/TableDetailPanel";
export { SqlBlock } from "./components/SqlBlock";
export { TableCard } from "./components/TableCard";
export { AppStateProvider } from "./components/AppState";
