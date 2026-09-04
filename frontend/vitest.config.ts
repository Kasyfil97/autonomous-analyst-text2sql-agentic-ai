import { defineConfig } from "vitest/config";

// Vitest config scoped to the pure logic units (sessions/attach). The `@/` alias is resolved
// from tsconfig.json's `paths` via Vite's native `resolve.tsconfigPaths` so tests import modules
// the same way the app does. jsdom provides a `localStorage`/`window` environment for the
// persistence tests.
export default defineConfig({
  resolve: {
    tsconfigPaths: true,
  },
  test: {
    environment: "jsdom",
    include: ["lib/__tests__/**/*.test.ts"],
    globals: false,
  },
});
