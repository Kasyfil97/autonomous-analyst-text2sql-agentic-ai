import { SearchPane } from "@/components/SearchPane";
import { AgentPane } from "@/components/AgentPane";

// The unified Sage workspace. The Sessions + Category rail is rendered by the root layout; these
// two panes fill the remaining grid columns (results + agent). `layout.tsx` wraps this in a
// `display:contents` node so each pane becomes an independently scrolling grid region.
export default function Home() {
  return (
    <>
      <SearchPane />
      <AgentPane />
    </>
  );
}
