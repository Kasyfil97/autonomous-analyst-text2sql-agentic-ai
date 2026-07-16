import { Sidebar } from "frontend";

// Sidebar is the fixed app-shell navigation: brand lockup, the two primary
// destinations (Search / AI Data Agent), and a standing draft-only safety
// notice pinned to the bottom. It reads the current route to mark the active
// item (shown here on the Search route). Given a real height so the bottom
// notice sits where the app shell places it.

export const Default = () => (
  <div style={{ height: 520, width: 248, display: "flex" }}>
    <Sidebar />
  </div>
);
