"use client";

import { useEffect, useState } from "react";
import { listDomains } from "@/lib/api";

// One source of truth for the domain (Category) facet list. Both the rail and the search pane
// consume this; a module-level promise cache guarantees the `/api/search/domains` fetch fires ONCE
// per page load and every consumer shares the same result (and any in-flight request).
let domainsPromise: Promise<string[]> | null = null;

function fetchDomainsOnce(): Promise<string[]> {
  if (!domainsPromise) {
    domainsPromise = listDomains()
      .then((d) => d.domains)
      .catch(() => {
        // Reset so a later mount can retry after a transient failure.
        domainsPromise = null;
        return [];
      });
  }
  return domainsPromise;
}

/** Fetch the domain facet list once and share it across all consumers. */
export function useDomains(): string[] {
  const [domains, setDomains] = useState<string[]>([]);

  useEffect(() => {
    let alive = true;
    fetchDomainsOnce().then((d) => {
      if (alive) setDomains(d);
    });
    return () => {
      alive = false;
    };
  }, []);

  return domains;
}
