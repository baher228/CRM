import { describe, expect, it } from "vitest";

import { buildCreatePayload, canTransition, requiresConfirmation } from "../src/utils/business";

describe("quick-create forms", () => {
  it("converts entered pounds to integer minor units", () => {
    expect(buildCreatePayload({ type: "opportunities", name: "  Renewal ", account_id: "42", value: "1250.55", next_action: "Call buyer", due_at: "" })).toEqual({
      title: "Renewal",
      account_id: 42,
      value_minor: 125055,
      next_action: "Call buyer",
    });
  });

  it("keeps optional contact fields absent instead of empty", () => {
    expect(buildCreatePayload({ type: "contacts", name: "Ava Cole", company: "", email: "", value: "", due_at: "" })).toEqual({
      display_name: "Ava Cole",
      email: undefined,
      account_id: undefined,
    });
  });
});

describe("commercial action safety", () => {
  it("allows only explicit forward state transitions", () => {
    expect(canTransition("invoice", "Draft", "Sent")).toBe(true);
    expect(canTransition("invoice", "Paid", "Draft")).toBe(false);
    expect(canTransition("contract", "Sent", "Active")).toBe(false);
  });

  it("requires confirmation for external and financial side effects", () => {
    expect(requiresConfirmation("email.send")).toBe(true);
    expect(requiresConfirmation("payment.refund")).toBe(true);
    expect(requiresConfirmation("account.update")).toBe(false);
  });
});
