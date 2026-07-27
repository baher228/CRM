import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { PageControls } from "../src/components/common";
import { initialInlineValues, serializeInlineValues } from "../src/components/DataControls";

describe("shared resource editing foundation", () => {
  it("initializes and serializes common inline field types", () => {
    const fields = [
      { key: "name", required: true },
      { key: "amount_pence", editor: "money" },
      { key: "probability_bps", editor: "percent" },
      { key: "billable", editor: "checkbox" },
      { key: "due_on", editor: "date" },
    ];
    const values = initialInlineValues({
      name: "Northstar",
      amount_pence: 125_50,
      probability_bps: 7_500,
      billable: 1,
      due_on: "2026-09-01T00:00:00Z",
    }, fields);
    expect(values).toEqual({
      name: "Northstar",
      amount_pence: "125.50",
      probability_bps: "75",
      billable: true,
      due_on: "2026-09-01",
    });
    expect(serializeInlineValues(values, fields)).toEqual({
      name: "Northstar",
      amount_pence: 12_550,
      probability_bps: 7_500,
      billable: true,
      due_on: "2026-09-01",
    });
  });

  it("renders keyboard-native previous and next page controls", () => {
    const html = renderToStaticMarkup(
      <PageControls hasNext hasPrevious label="Accounts" nextPage={vi.fn()} page={3} previousPage={vi.fn()} />,
    );
    expect(html).toContain('aria-label="Accounts pages"');
    expect(html).toContain("Page 3");
    expect(html).toContain("Previous</button>");
    expect(html).toContain(">Next ");
  });
});
