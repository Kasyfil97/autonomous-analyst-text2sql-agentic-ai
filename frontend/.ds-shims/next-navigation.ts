// Standalone shim for next/navigation — static, inert router hooks so BRISA
// components render outside a Next.js app. usePathname resolves to /search so
// the Sidebar shows a representative active-nav state.
export function usePathname(): string {
  return "/search";
}

export function useRouter() {
  const noop = () => {};
  return { push: noop, replace: noop, back: noop, forward: noop, refresh: noop, prefetch: noop };
}

export function useSearchParams(): URLSearchParams {
  return new URLSearchParams();
}

export function useParams(): Record<string, string> {
  return {};
}

export function redirect(): void {}
export function notFound(): void {}
