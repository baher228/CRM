import { describe, expect, it } from "vitest";

import { csvHeaders, suggestedImportMapping } from "../src/components/SystemControlDialogs";

describe("system data controls", () => {
  it("reads quoted CSV headers without splitting embedded commas", () => {
    expect(csvHeaders('\ufeff"Company, legal",domain,status\r\nAcme,acme.test,Prospect')).toEqual([
      "Company, legal",
      "domain",
      "status",
    ]);
  });

  it("suggests only valid fields and applies import aliases", () => {
    expect(suggestedImportMapping("contacts", ["Company Name", "Email", "Mystery"])).toEqual({
      "Company Name": "account_name",
      Email: "email",
    });
    expect(suggestedImportMapping("accounts", ["Name of Account", "VAT Number"])).toEqual({
      "Name of Account": "name",
      "VAT Number": "vat_number",
    });
  });
});
