import { describe, expect, it } from "vitest";

import {
  commercialLinePayload,
  commercialLineTotalMinor,
  creditLinesForGrossMinor,
  vatIsEnabled,
} from "../src/components/WorkflowDialogs";

describe("commercial and delivery controls", () => {
  it("builds quantity, discount and VAT-safe catalog line payloads", () => {
    const line = {
      catalog_item_id: "7",
      description: "Implementation",
      quantity: "2.5",
      unit_price: "100.00",
      discount_percent: "10",
      tax_percent: "20",
    };
    expect(commercialLinePayload(line, true)).toEqual({
      catalog_item_id: 7,
      description: "Implementation",
      quantity: "2.5",
      unit_price_pence: 10_000,
      discount_bps: 1_000,
      tax_rate_bps: 2_000,
    });
    expect(commercialLineTotalMinor(line, true)).toBe(27_000);
    expect(commercialLinePayload(line, false).tax_rate_bps).toBe(0);
    expect(commercialLineTotalMinor(line, false)).toBe(22_500);
  });

  it("enables VAT only for a currently effective, approved profile", () => {
    const ready = {
      vat_registered: true,
      legal_name: "North Star Ltd",
      vat_number: "GB123456789",
      vat_scheme: "standard",
      vat_effective_from: "2025-01-01",
      tax_codes_approved: true,
    };
    expect(vatIsEnabled(ready, "2026-07-10")).toBe(true);
    expect(vatIsEnabled({ ...ready, tax_codes_approved: false }, "2026-07-10")).toBe(false);
    expect(vatIsEnabled({ ...ready, vat_effective_from: "2027-01-01" }, "2026-07-10")).toBe(false);
  });

  it("creates an exact gross credit including rounding adjustment when needed", () => {
    const lines = creditLinesForGrossMinor(10_001, 2_000, "Credit invoice");
    const total = lines.reduce((sum, line) => sum + line.unit_price_pence + Math.round(line.unit_price_pence * line.tax_rate_bps / 10_000), 0);
    expect(total).toBe(10_001);
    expect(lines[0].description).toBe("Credit invoice");
  });
});
