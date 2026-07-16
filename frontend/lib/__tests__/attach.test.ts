import { describe, expect, it } from "vitest";
import { MAX_ATTACHED, addAttached } from "@/lib/attach";

describe("addAttached", () => {
  it("appends a new table and reports 'added', order preserved", () => {
    const { next, result } = addAttached(["a", "b"], "c");
    expect(result).toBe("added");
    expect(next).toEqual(["a", "b", "c"]);
  });

  it("returns the array unchanged and reports 'duplicate' for an existing table", () => {
    const current = ["a", "b", "c"];
    const { next, result } = addAttached(current, "b");
    expect(result).toBe("duplicate");
    expect(next).toBe(current); // referentially unchanged
    expect(next).toEqual(["a", "b", "c"]);
  });

  it("rejects the 11th distinct table with 'cap', unchanged", () => {
    const full = Array.from({ length: MAX_ATTACHED }, (_, i) => `t${i}`);
    expect(full).toHaveLength(10);
    const { next, result } = addAttached(full, "t_new");
    expect(result).toBe("cap");
    expect(next).toBe(full); // referentially unchanged
    expect(next).toHaveLength(MAX_ATTACHED);
  });

  it("treats a duplicate at cap as 'duplicate', not 'cap'", () => {
    const full = Array.from({ length: MAX_ATTACHED }, (_, i) => `t${i}`);
    const { next, result } = addAttached(full, "t0");
    expect(result).toBe("duplicate");
    expect(next).toBe(full);
  });

  it("preserves insertion order across repeated adds", () => {
    let acc: string[] = [];
    for (const t of ["x", "y", "z"]) {
      acc = addAttached(acc, t).next;
    }
    expect(acc).toEqual(["x", "y", "z"]);
  });
});
