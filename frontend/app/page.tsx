"use client";

import { useState } from "react";
import { SearchPane } from "@/components/SearchPane";
import { AgentPane } from "@/components/AgentPane";
import { TableDetailView } from "@/components/TableDetailView";
import { ResizableSplit } from "@/components/ResizableSplit";

// The unified Sage workspace. The global top bar is rendered by the root layout; a resizable split
// fills the remaining height. The left pane swaps between the search results and a full-pane table
// detail view (opened by clicking a result card); the AgentPane stays mounted on the right, and the
// divider between them can be dragged to resize either side.
export default function Home() {
  const [openTableId, setOpenTableId] = useState<string | null>(null);

  return (
    <ResizableSplit
      left={
        openTableId ? (
          <TableDetailView id={openTableId} onBack={() => setOpenTableId(null)} />
        ) : (
          <SearchPane onOpenTable={setOpenTableId} />
        )
      }
      right={<AgentPane />}
    />
  );
}
