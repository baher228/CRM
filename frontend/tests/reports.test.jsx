import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../src/hooks", () => ({
  useDocumentTitle: () => {},
  useResource: (path) => ({
    data: path === "reports" ? {
      finance: {
        currency: "GBP",
        invoiced_pence: 250_000,
        collected_pence: 125_000,
        outstanding_pence: 125_000,
        vat: { net_due_pence: 20_000 },
      },
      projects: [{ margin_pence: 75_000 }],
      renewals: [{ renewal_on: "2026-08-30" }],
    } : [],
    loading: false,
    error: null,
    reload: () => {},
  }),
}));

import { ReportsView } from "../src/views/WorkspaceViews";

describe("reports workspace", () => {
  it("renders live finance, delivery and renewal summaries", () => {
    const html = renderToStaticMarkup(<MemoryRouter><ReportsView /></MemoryRouter>);
    expect(html).toContain("£2,500");
    expect(html).toContain("£1,250");
    expect(html).toContain("£750");
    expect(html).toContain("Next renewal");
    expect(html).toContain('href="/reports?view=pipeline"');
    expect(html).toContain('href="/reports?view=ledger"');
  });
});
