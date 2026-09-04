"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

// A two-pane horizontal split with a draggable divider. The left pane gets an explicit pixel width
// (dragged by the user, persisted to localStorage); the right pane flexes to fill the rest. Both
// sides enforce a minimum width so neither can be collapsed to nothing.

const MIN_LEFT = 360;
const MIN_RIGHT = 340;
const HANDLE_W = 6; // px — must match the handle's rendered width
const STORAGE_KEY = "sage.split.left-px";
const KEY_STEP = 24; // px nudge per arrow-key press

export function ResizableSplit({ left, right }: { left: ReactNode; right: ReactNode }) {
  const containerRef = useRef<HTMLDivElement>(null);
  // null → not yet sized (use a 50/50 flex default until the user drags or a stored width loads).
  const [leftWidth, setLeftWidth] = useState<number | null>(null);
  const draggingRef = useRef(false);

  // Clamp a candidate left width to the container's current bounds.
  const clamp = useCallback((w: number) => {
    const total = containerRef.current?.getBoundingClientRect().width ?? 0;
    const max = total - MIN_RIGHT - HANDLE_W;
    return Math.max(MIN_LEFT, Math.min(w, Math.max(MIN_LEFT, max)));
  }, []);

  // Restore a persisted width once mounted (client-only, avoids SSR mismatch).
  useEffect(() => {
    let raw: string | null = null;
    try {
      raw = window.localStorage.getItem(STORAGE_KEY);
    } catch {
      raw = null;
    }
    if (raw) {
      const n = Number(raw);
      if (Number.isFinite(n)) setLeftWidth(clamp(n));
    }
  }, [clamp]);

  const persist = useCallback((w: number) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, String(Math.round(w)));
    } catch {
      /* best effort */
    }
  }, []);

  // Global pointer listeners while dragging so the drag keeps tracking even over the (iframe-free)
  // right pane or off the handle.
  useEffect(() => {
    function onMove(e: PointerEvent) {
      if (!draggingRef.current) return;
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      setLeftWidth(clamp(e.clientX - rect.left));
    }
    function onUp() {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      setLeftWidth((w) => {
        if (w != null) persist(w);
        return w;
      });
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [clamp, persist]);

  // Keep the width valid if the viewport shrinks.
  useEffect(() => {
    function onResize() {
      setLeftWidth((w) => (w == null ? w : clamp(w)));
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [clamp]);

  function startDrag(e: React.PointerEvent) {
    e.preventDefault();
    draggingRef.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    // Seed an explicit width from the current layout so the first drag delta is smooth.
    setLeftWidth((w) => {
      if (w != null) return w;
      const rect = containerRef.current?.getBoundingClientRect();
      return rect ? clamp(rect.width / 2) : w;
    });
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    setLeftWidth((w) => {
      const base = w ?? (containerRef.current?.getBoundingClientRect().width ?? 0) / 2;
      const next = clamp(base + (e.key === "ArrowLeft" ? -KEY_STEP : KEY_STEP));
      persist(next);
      return next;
    });
  }

  const leftStyle =
    leftWidth != null
      ? { width: leftWidth, flex: "0 0 auto" as const }
      : { flex: "1 1 0%" as const, minWidth: MIN_LEFT };

  return (
    <div ref={containerRef} className="flex h-full min-h-0">
      <div className="h-full min-h-0 overflow-hidden" style={leftStyle}>
        {left}
      </div>

      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Ubah lebar panel"
        tabIndex={0}
        onPointerDown={startDrag}
        onKeyDown={onKeyDown}
        className="group relative z-10 flex w-1.5 shrink-0 cursor-col-resize items-center justify-center bg-[color:var(--color-line)] transition-colors hover:bg-[color:var(--color-accent)]/50 focus:outline-none focus-visible:bg-[color:var(--color-accent)]"
      >
        {/* Wider invisible hit area for easier grabbing. */}
        <span aria-hidden className="absolute inset-y-0 -left-1.5 -right-1.5" />
        {/* Grip dots. */}
        <span
          aria-hidden
          className="pointer-events-none flex flex-col gap-1 text-[color:var(--color-panel)] opacity-0 transition-opacity group-hover:opacity-100"
        >
          <span className="h-0.5 w-0.5 rounded-full bg-current" />
          <span className="h-0.5 w-0.5 rounded-full bg-current" />
          <span className="h-0.5 w-0.5 rounded-full bg-current" />
        </span>
      </div>

      <div className="h-full min-h-0 flex-1 overflow-hidden" style={{ minWidth: MIN_RIGHT }}>
        {right}
      </div>
    </div>
  );
}
