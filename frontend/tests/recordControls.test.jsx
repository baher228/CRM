import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../src/hooks", () => ({
  useDocumentTitle: () => {},
  useResource: (path) => {
    if (path === "accounts/1") return { data: { id: 1, name: "North Star", status: "Prospect", version: 2, custom: {}, created_at: "2026-01-01", updated_at: "2026-01-02" }, loading: false, error: null, reload: vi.fn() };
    return { data: [], loading: false, error: null, reload: vi.fn() };
  },
}));

import { RecordWorkspace } from "../src/views/RecordWorkspace";

describe("record management and tabs", () => {
  it("uses an accessible local tab interface and exposes management controls", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/accounts/1"]}>
        <Routes><Route element={<RecordWorkspace resourceKey="accounts" />} path="/accounts/:id" /></Routes>
      </MemoryRouter>,
    );
    expect(html).toContain('role="tablist"');
    expect((html.match(/role="tab"/g) || []).length).toBe(5);
    expect(html).toContain('aria-selected="true"');
    expect(html).toContain('role="tabpanel"');
    expect(html).toContain("Manage");
    expect(html).toContain("Merge duplicate");
    expect(html).toContain("Archive record");
  });
});
