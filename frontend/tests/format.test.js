import { describe, expect, it } from "vitest";

import { formatMoney, initials, recordName, statusTone, titleCase } from "../src/utils/format";

describe("workspace formatters", () => {
  it("formats integer minor units as GBP", () => {
    expect(formatMoney(123456)).toBe("£1,234.56");
    expect(formatMoney(0)).toBe("£0");
  });

  it("creates stable human labels", () => {
    expect(initials("North Star Studio")).toBe("NS");
    expect(titleCase("part-paid")).toBe("Part Paid");
    expect(recordName({ subject: "Renewal review" })).toBe("Renewal review");
  });

  it("maps business states to semantic tones", () => {
    expect(statusTone("Paid")).toBe("positive");
    expect(statusTone("Overdue")).toBe("danger");
    expect(statusTone("Draft")).toBe("warning");
    expect(statusTone("Discovery")).toBe("info");
  });
});
