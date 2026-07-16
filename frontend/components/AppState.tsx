"use client";

import {
  createContext,
  useContext,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import type { AgentResponse, SearchResponse } from "@/lib/api";

// State that must outlive route changes lives here. The provider is mounted once in the root
// layout, which stays mounted across client-side navigation — so a search (or an agent draft)
// survives visiting a table-detail page and coming back, and survives switching tabs.

export interface SearchState {
  q: string;
  domain: string | null;
  res: SearchResponse | null;
}

export interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  content?: string; // user message text
  attached?: string[]; // tables attached on a user turn
  response?: AgentResponse; // assistant structured result
  error?: string; // assistant turn that failed to load
}

export interface AgentState {
  attached: string[]; // pending attachments for the next message
  question: string; // composer draft
  turns: ChatTurn[]; // the conversation
  sending: boolean; // a request is in flight
}

interface AppStateValue {
  search: SearchState;
  setSearch: Dispatch<SetStateAction<SearchState>>;
  agent: AgentState;
  setAgent: Dispatch<SetStateAction<AgentState>>;
}

const INITIAL_SEARCH: SearchState = { q: "", domain: null, res: null };
const INITIAL_AGENT: AgentState = {
  attached: [],
  question: "",
  turns: [],
  sending: false,
};

const Ctx = createContext<AppStateValue | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [search, setSearch] = useState<SearchState>(INITIAL_SEARCH);
  const [agent, setAgent] = useState<AgentState>(INITIAL_AGENT);
  return (
    <Ctx.Provider value={{ search, setSearch, agent, setAgent }}>{children}</Ctx.Provider>
  );
}

export function useAppState(): AppStateValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAppState must be used within AppStateProvider");
  return v;
}
