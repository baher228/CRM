import { describe, expect, it } from "vitest";

import {
  buildAutomationPayload,
  buildSequencePayload,
  parsePreviewRecords,
} from "../src/components/SequenceAutomationWorkflows";

describe("sequence and automation workflow payloads", () => {
  it("normalizes sequence schedule values for the API", () => {
    expect(buildSequencePayload({
      name: "  Renewal follow-up  ",
      description: "  Timely check-in  ",
      timezone: "Europe/London",
      send_window_start: "09:00",
      send_window_end: "17:00",
      daily_cap: "40",
    })).toEqual({
      name: "Renewal follow-up",
      description: "Timely check-in",
      timezone: "Europe/London",
      send_window_start: "09:00",
      send_window_end: "17:00",
      daily_cap: 40,
    });
  });

  it("keeps automation updates versioned and parses preview objects", () => {
    expect(buildAutomationPayload({
      name: "  Chase overdue invoices ",
      trigger_name: "invoice.overdue",
      conditions: '[{"field":"amount_due_minor","operator":"gt","value":0}]',
      actions: '[{"type":"create_task","params":{"title":"Chase invoice"}}]',
      enabled: true,
      dry_run: true,
    }, 3)).toMatchObject({ name: "Chase overdue invoices", version: 3, enabled: true, dry_run: true });
    expect(parsePreviewRecords('{"id":"invoice-1"}')).toEqual([{ id: "invoice-1" }]);
    expect(() => parsePreviewRecords("[]")).toThrow("Provide one record object");
  });
});
