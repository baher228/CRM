import { expect, test } from "@playwright/test";

const unique = () => `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;

const isoDate = (daysFromNow) => {
  const value = new Date();
  value.setUTCDate(value.getUTCDate() + daysFromNow);
  return value.toISOString().slice(0, 10);
};

async function json(response) {
  const body = await response.text();
  expect(response.ok(), body).toBeTruthy();
  return body ? JSON.parse(body) : null;
}

test("quick create, deep links, search and browser history", async ({ page }) => {
  const suffix = unique();
  const accountName = `North Star ${suffix}`;
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Today’s command view" })).toBeVisible();

  await page.getByRole("button", { name: /New Ctrl N|New/ }).first().click();
  await expect(page.getByRole("dialog", { name: "Quick create" })).toBeVisible();
  await page.getByLabel("Account name").fill(accountName);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/accounts\/\d+$/);
  await expect(page.getByRole("heading", { name: accountName })).toBeVisible();

  await page.keyboard.press(process.platform === "darwin" ? "Meta+K" : "Control+K");
  const search = page.getByRole("dialog", { name: "Command workspace" });
  await expect(search).toBeVisible();
  await search.getByRole("searchbox").fill(accountName);
  await expect(search.getByText(accountName)).toBeVisible();
  await search.getByText(accountName).click();
  await expect(page.getByRole("heading", { name: accountName })).toBeVisible();

  await page.getByRole("link", { name: /Back to Accounts/ }).click();
  await expect(page).toHaveURL(/\/accounts$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/accounts\/\d+$/);
});

test("responsive shell is keyboard reachable and never overflows", async ({ page }, testInfo) => {
  await page.goto("/pipeline");
  await expect(page.getByRole("heading", { name: "Pipeline", exact: true })).toBeVisible();
  const dimensions = await page.evaluate(() => ({
    width: innerWidth,
    body: document.body.scrollWidth,
    document: document.documentElement.scrollWidth,
  }));
  expect(dimensions.body).toBeLessThanOrEqual(dimensions.width);
  expect(dimensions.document).toBeLessThanOrEqual(dimensions.width);

  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
  if (testInfo.project.name === "mobile") {
    await expect(page.getByRole("navigation", { name: "Mobile navigation" })).toBeVisible();
    await expect(page.getByRole("button", { name: "More" })).toBeVisible();
  } else {
    await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
  }

  await page.emulateMedia({ reducedMotion: "reduce" });
  const motion = await page.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior);
  expect(["auto", "instant"]).toContain(motion);
});

test("primary workflows do not horizontally overflow at acceptance widths", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "One browser project exercises the complete viewport matrix.");
  const routes = ["/", "/inbox", "/inbox?view=suppressions", "/pipeline", "/projects", "/billing", "/files?view=templates", "/reports?view=ledger"];
  for (const width of [1440, 1024, 768, 375]) {
    await page.setViewportSize({ width, height: width === 375 ? 812 : 900 });
    for (const route of routes) {
      await page.goto(route);
      await expect(page.locator("main#main-content")).toBeVisible();
      const dimensions = await page.evaluate(() => ({
        viewport: document.documentElement.clientWidth,
        body: document.body.scrollWidth,
        document: document.documentElement.scrollWidth,
      }));
      expect(dimensions.body, `${route} body at ${width}px`).toBeLessThanOrEqual(dimensions.viewport);
      expect(dimensions.document, `${route} document at ${width}px`).toBeLessThanOrEqual(dimensions.viewport);
    }
  }
});

test("disconnected integrations keep local work available", async ({ page }, testInfo) => {
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await expect(page.getByText(/Values are never displayed/).first()).toBeVisible();
  if (testInfo.project.name === "mobile") {
    await page.getByRole("button", { name: "More" }).click();
  }
  await page.getByRole("link", { name: "Accounts" }).click();
  await expect(page.getByRole("heading", { name: "Accounts" })).toBeVisible();
});

test("inline edits recover conflicts and cursor pages preserve query state", async ({ page, request }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "The version conflict is exercised once; mobile uses the responsive acceptance matrix.");
  const suffix = unique();
  const prefix = `Paged Account ${suffix}`;
  const accounts = [];
  for (let index = 0; index < 27; index += 1) {
    accounts.push(await json(await request.post("/api/v1/accounts", {
      data: { name: `${prefix} ${String(index).padStart(2, "0")}` },
    })));
  }

  await page.goto("/accounts");
  const search = page.getByRole("searchbox", { name: "Search Accounts" });
  await search.fill(prefix);
  const firstName = `${prefix} 00`;
  await expect(page.getByRole("link", { name: firstName, exact: true })).toBeVisible();
  await page.getByRole("button", { name: `Edit ${firstName}` }).click();
  const editor = page.locator(".inline-record-editor");
  await expect(editor).toBeVisible();

  const serverName = `${prefix} 00 server`;
  await json(await request.patch(`/api/v1/accounts/${accounts[0].id}`, {
    data: { version: accounts[0].version, name: serverName },
  }));
  await editor.getByLabel("Account name").fill(`${prefix} 00 local`);
  await editor.getByRole("button", { name: "Save changes" }).click();
  await expect(editor.getByText("The server is now at version 2.")).toBeVisible();
  await editor.getByRole("button", { name: "Reload latest" }).click();
  await expect(editor.getByLabel("Account name")).toHaveValue(serverName);
  const finalName = `${prefix} 00 final`;
  await editor.getByLabel("Account name").fill(finalName);
  await editor.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("link", { name: finalName, exact: true })).toBeVisible();

  await page.getByLabel(`Select ${finalName}`).check();
  await expect(page.getByText("1 selected")).toBeVisible();
  const pagination = page.getByRole("navigation", { name: "Accounts pages" });
  await pagination.getByRole("button", { name: /Next/ }).click();
  await expect(pagination).toContainText("Page 2");
  await expect(search).toHaveValue(prefix);
  await expect(page.getByText("Select records for bulk actions")).toBeVisible();
  await pagination.getByRole("button", { name: /Previous/ }).click();
  await expect(page.getByRole("link", { name: finalName, exact: true })).toBeVisible();
});

test("calendar events create, edit and archive with keyboard-native controls", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "The calendar lifecycle is exercised once; mobile is covered by the overflow gate.");
  const suffix = unique();
  const title = `Planning session ${suffix}`;
  const updatedTitle = `${title} updated`;
  page.on("dialog", (dialog) => dialog.accept());

  await page.goto("/calendar");
  await page.getByRole("button", { name: "Add event" }).click();
  const create = page.getByRole("dialog", { name: "Add calendar event" });
  await create.getByLabel("Title").fill(title);
  await create.getByLabel("Location").fill("Video call");
  await create.getByRole("button", { name: "Add event", exact: true }).click();
  await expect(page.getByRole("button", { name: `Edit ${title}` })).toBeVisible();

  await page.getByRole("button", { name: `Edit ${title}` }).click();
  const edit = page.getByRole("dialog", { name: "Edit calendar event" });
  await edit.getByLabel("Title").fill(updatedTitle);
  await edit.getByRole("button", { name: "Save event" }).click();
  await expect(page.getByRole("button", { name: `Edit ${updatedTitle}` })).toBeVisible();

  await page.getByRole("button", { name: `Edit ${updatedTitle}` }).click();
  await page.getByRole("dialog", { name: "Edit calendar event" }).getByRole("button", { name: "Archive" }).click();
  await expect(page.getByText("Calendar event archived.")).toBeVisible();
  await expect(page.getByRole("button", { name: `Edit ${updatedTitle}` })).toHaveCount(0);
});

test("communications templates, suppressions, document templates and report drill-downs are operational", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "Management workspaces are exercised once; mobile is covered by the overflow gate.");
  const suffix = unique();
  const emailTemplate = `Follow-up ${suffix}`;
  const documentTemplate = `Proposal source ${suffix}`;
  const suppressedEmail = `suppressed-${suffix}@example.com`;
  page.on("dialog", (dialog) => dialog.accept());

  await page.goto("/inbox?view=templates");
  await page.getByRole("button", { name: "New template" }).click();
  const emailDialog = page.getByRole("dialog", { name: "New email template" });
  await emailDialog.getByLabel("Name").fill(emailTemplate);
  await emailDialog.getByLabel("Subject").fill("Hello {{name}}");
  await emailDialog.getByLabel("Message").fill("A useful note for {{account_name}}.");
  await emailDialog.getByRole("button", { name: "Create template" }).click();
  await expect(page.getByText(emailTemplate, { exact: true })).toBeVisible();
  await page.locator(".control-row").filter({ hasText: emailTemplate }).getByRole("button", { name: "Preview" }).click();
  await expect(page.getByRole("dialog", { name: emailTemplate })).toContainText("Hello Alex");
  await page.getByRole("dialog", { name: emailTemplate }).getByRole("button", { name: /Close/ }).click();

  await page.getByRole("button", { name: "Suppressions" }).click();
  await page.getByLabel("Email address").fill(suppressedEmail);
  await page.getByLabel("Reason").fill("Acceptance opt-out");
  await page.getByRole("button", { name: "Suppress", exact: true }).click();
  await expect(page.getByText(suppressedEmail, { exact: true })).toBeVisible();

  await page.goto("/files?view=templates");
  await page.getByRole("button", { name: "New template" }).click();
  const documentDialog = page.getByRole("dialog", { name: "New document template" });
  await documentDialog.getByLabel("Name").fill(documentTemplate);
  await documentDialog.getByLabel("Category").fill("Proposal");
  await documentDialog.getByLabel("Google Drive file ID").fill(`drive-${suffix}`);
  await documentDialog.getByRole("button", { name: "Create template" }).click();
  await expect(page.getByText(documentTemplate, { exact: true })).toBeVisible();
  await page.locator(".control-row").filter({ hasText: documentTemplate }).getByRole("button", { name: "Archive" }).click();
  await expect(page.getByText("Document template archived.")).toBeVisible();

  await page.goto("/reports");
  await page.getByRole("button", { name: "Finance & VAT" }).click();
  await expect(page.getByRole("heading", { name: "VAT position" })).toBeVisible();
  await page.getByRole("button", { name: "Ledger" }).click();
  await expect(page.getByRole("searchbox", { name: "Search ledger" })).toBeVisible();
  await page.getByRole("button", { name: "Pipeline" }).click();
  await expect(page.getByText(/Stage confidence|No open pipeline/)).toBeVisible();
});

test("sequence roster supports enroll, pause, resume and cancel", async ({ page, request }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "The sequence lifecycle is exercised once; mobile is covered by the overflow gate.");
  const suffix = unique();
  const account = await json(await request.post("/api/v1/accounts", { data: { name: `Sequence Account ${suffix}` } }));
  const contact = await json(await request.post("/api/v1/contacts", {
    data: { account_id: account.id, display_name: `Sequence Contact ${suffix}`, email: `sequence-${suffix}@example.com` },
  }));
  const template = await json(await request.post("/api/v1/email/templates", {
    data: { name: `Sequence template ${suffix}`, subject: "Hello {{first_name}}", body_text: "A short note." },
  }));
  const sequence = await json(await request.post("/api/v1/sequences", {
    data: { name: `Sequence ${suffix}`, steps: [{ step_type: "email", template_id: template.id }] },
  }));
  const active = await json(await request.post(`/api/v1/sequences/${sequence.id}/activate`, {
    headers: { "X-CRM-Confirmed": "true" },
    data: { version: sequence.version },
  }));
  expect(active.state).toBe("Active");
  page.on("dialog", (dialog) => dialog.accept());

  await page.goto(`/sequences/${sequence.id}`);
  await page.getByRole("button", { name: "Enroll", exact: true }).click();
  const enroll = page.getByRole("dialog", { name: "Enroll in sequence" });
  await enroll.getByLabel("CRM contact").selectOption(String(contact.id));
  await enroll.getByRole("button", { name: "Enroll recipient" }).click();
  const roster = page.locator(".enrollment-row").filter({ hasText: contact.email });
  await expect(roster).toContainText("Active");

  await roster.getByRole("button", { name: "Pause" }).click();
  await page.getByRole("dialog", { name: `Pause: ${contact.email}` }).getByRole("button", { name: "Pause", exact: true }).click();
  await expect(roster).toContainText("Paused");
  await roster.getByRole("button", { name: "Resume" }).click();
  await page.getByRole("dialog", { name: `Resume: ${contact.email}` }).getByRole("button", { name: "Resume", exact: true }).click();
  await expect(roster).toContainText("Active");
  await roster.getByRole("button", { name: "Cancel" }).click();
  const cancel = page.getByRole("dialog", { name: `Cancel: ${contact.email}` });
  await cancel.getByLabel(/Reason/).fill("Acceptance complete");
  await cancel.getByRole("button", { name: "Cancel", exact: true }).last().click();
  await expect(roster).toContainText("Cancelled");
});

test("saved views, tags, custom fields, archive restore and duplicate merge are operator-safe", async ({ page, request }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "The data controls are exercised once; mobile is covered by the overflow gate.");
  const suffix = unique();
  const targetName = `Control Target ${suffix}`;
  const sourceName = `Control Duplicate ${suffix}`;
  const archiveName = `Control Archive ${suffix}`;
  const fieldName = `Buying tier ${suffix}`;
  const tagName = `Priority ${suffix}`;
  const createAccount = async (name, domain) => json(await request.post("/api/v1/accounts", { data: { name, domain } }));
  const target = await createAccount(targetName, `target-${suffix}.example`);
  const source = await createAccount(sourceName, `source-${suffix}.example`);
  const archivedCandidate = await createAccount(archiveName, `archive-${suffix}.example`);
  await json(await request.post("/api/v1/tags", { data: { name: tagName, color: "cyan" } }));

  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/settings");
  await page.getByRole("button", { name: "New field" }).click();
  const fieldDialog = page.getByRole("dialog", { name: "New custom field" });
  await fieldDialog.getByLabel("Record type").selectOption("account");
  await fieldDialog.getByLabel("Field type").selectOption("select");
  await fieldDialog.getByLabel("Field name").fill(fieldName);
  await fieldDialog.getByLabel(/Options/).fill("Strategic, Standard");
  await fieldDialog.getByRole("button", { name: "Create field" }).click();
  await expect(page.getByText(fieldName)).toBeVisible();

  await page.goto("/accounts");
  await page.getByRole("searchbox", { name: "Search Accounts" }).fill(archiveName);
  await page.locator(".column-menu summary").click();
  await page.getByRole("checkbox", { name: "Domain", exact: true }).uncheck();
  await page.getByRole("button", { name: "Save view" }).click();
  const viewDialog = page.getByRole("dialog", { name: "Save account view" });
  await viewDialog.getByLabel("View name").fill(`Archive candidates ${suffix}`);
  await viewDialog.getByRole("button", { name: "Save view" }).click();
  await expect(page).toHaveURL(/saved_view=\d+/);
  await page.reload();
  await expect(page.getByRole("searchbox", { name: "Search Accounts" })).toHaveValue(archiveName);

  await page.getByLabel(`Select ${archiveName}`).check();
  await page.getByRole("button", { name: "Archive selected" }).click();
  await expect(page.getByText("1 record archived.")).toBeVisible();
  await page.getByText("Include archived").click();
  await expect(page.getByRole("button", { name: "Restore" })).toBeVisible();
  await page.getByRole("button", { name: "Restore" }).click();
  await expect(page.getByText(`${archiveName} restored.`)).toBeVisible();

  await page.goto(`/accounts/${source.id}`);
  await page.getByRole("button", { name: "Manage" }).click();
  const manage = page.getByRole("dialog", { name: `Manage ${sourceName}` });
  await manage.getByLabel(tagName).check();
  await expect(manage.getByText("Tags updated.")).toBeVisible();
  await manage.getByLabel(fieldName).selectOption("Strategic");
  await manage.getByRole("button", { name: "Save custom fields" }).click();
  await expect(manage.getByText("Custom fields updated.")).toBeVisible();
  await manage.getByLabel("Surviving account").selectOption(String(target.id));
  await manage.getByRole("button", { name: "Merge" }).click();
  await expect(page).toHaveURL(new RegExp(`/accounts/${target.id}$`));
  await expect(page.getByRole("heading", { name: targetName })).toBeVisible();
  const sourceAfter = await json(await request.get(`/api/v1/accounts/${source.id}`));
  expect(sourceAfter.archived_at).toBeTruthy();
  expect(archivedCandidate.id).toBeTruthy();
});

test("operator can preview and commit imports, inspect recovery, and switch pipeline modes", async ({ page }, testInfo) => {
  const suffix = `${testInfo.project.name}-${Date.now()}`;
  const accountName = `Imported operator ${suffix}`;
  page.on("dialog", (dialog) => dialog.accept());

  await page.goto("/settings");
  await page.getByRole("button", { name: "Import CSV" }).click();
  const importDialog = page.getByRole("dialog", { name: "Import CRM records" });
  await importDialog.getByLabel("Or paste CSV").fill(`name,domain,status\n${accountName},import-${suffix}.example,Prospect`);
  await expect(importDialog.getByLabel("Map name")).toHaveValue("name");
  await importDialog.getByRole("button", { name: "Preview import" }).click();
  await expect(importDialog.getByText(/1 rows checked · 1 ready/)).toBeVisible();
  await importDialog.getByRole("button", { name: "Commit import" }).click();
  await expect(page.getByText("1 records imported; 0 duplicates skipped.")).toBeVisible();

  await page.getByRole("button", { name: "Open job recovery" }).click();
  await expect(page.getByRole("dialog", { name: "Durable job recovery" })).toBeVisible();
  await page.getByRole("button", { name: "Done" }).click();
  await page.getByRole("button", { name: "Restore backup" }).click();
  await expect(page.getByRole("dialog", { name: "Restore CRM workspace" })).toContainText("Type RESTORE");
  await page.getByRole("button", { name: "Cancel" }).click();

  await page.goto("/accounts");
  await page.getByRole("searchbox", { name: "Search Accounts" }).fill(accountName);
  await expect(page.getByRole("link", { name: accountName, exact: true })).toBeVisible();

  await page.goto("/pipeline");
  for (const mode of ["Ranked queue", "Table", "Forecast", "Board"]) {
    await page.getByRole("button", { name: mode, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`view=${mode === "Ranked queue" ? "queue" : mode.toLowerCase()}`));
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  }
});

test("operator can run the complete tender-to-renewal lifecycle", async ({ page, request }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "The lifecycle is exercised once; mobile has a dedicated responsive gate.");

  const suffix = unique();
  const tenderTitle = `Digital service tender ${suffix}`;
  const accountName = `Lifecycle Works ${suffix}`;
  const dealTitle = `Transformation programme ${suffix}`;
  const commercialTitle = `Delivery proposal ${suffix}`;
  const invoiceLine = `Implementation phase ${suffix}`;

  const tender = await json(await request.post("/api/v1/tenders", {
    data: {
      title: tenderTitle,
      buyer_name: accountName,
      portal_name: "Deterministic test portal",
      estimated_value_minor: 1_200_000,
      source_urls: [`https://example.test/notices/${suffix}`],
    },
  }));

  page.on("dialog", (dialog) => dialog.accept());
  await page.goto(`/tenders/${tender.id}`);
  await expect(page.getByRole("heading", { name: tenderTitle })).toBeVisible();
  await page.getByRole("button", { name: "Qualify tender" }).click();
  const qualify = page.getByRole("dialog", { name: "Qualify tender" });
  await qualify.getByLabel("Account name").fill(accountName);
  await qualify.getByLabel("Deal title").fill(dealTitle);
  await qualify.getByLabel(/Estimated value/).fill("12000");
  await qualify.getByRole("button", { name: "Qualify", exact: true }).click();
  await expect(page).toHaveURL(/\/opportunities\/\d+$/);
  await expect(page.getByRole("heading", { name: dealTitle })).toBeVisible();
  const opportunityId = Number(page.url().match(/(\d+)$/)[1]);

  await page.goto("/proposals");
  await page.getByRole("button", { name: "New proposal" }).click();
  const proposal = page.getByRole("dialog", { name: "New proposal" });
  await proposal.getByLabel("Account").selectOption({ label: accountName });
  await proposal.getByLabel(/Deal/).selectOption({ label: dealTitle });
  await proposal.getByLabel("Title").fill(commercialTitle);
  await proposal.getByLabel("Line description").fill(invoiceLine);
  await proposal.getByLabel(/Amount/).fill("12000");
  await proposal.getByRole("button", { name: "Create proposal" }).click();
  await expect(page).toHaveURL(/\/proposals\/\d+$/);
  await page.getByRole("button", { name: "Send proposal" }).click();
  await expect(page.getByRole("button", { name: "Accept" })).toBeVisible();
  await page.getByRole("button", { name: "Accept" }).click();

  await expect(page).toHaveURL(/\/contracts\/\d+$/);
  const contractId = Number(page.url().match(/(\d+)$/)[1]);
  await page.getByRole("button", { name: "Send contract" }).click();
  await expect(page.getByRole("button", { name: "Record signature" })).toBeVisible();
  await page.getByRole("button", { name: "Record signature" }).click();
  await page.getByRole("dialog", { name: "Record contract signature" }).getByRole("button", { name: "Record signature", exact: true }).click();
  await expect(page.getByRole("button", { name: "Activate" })).toBeVisible();
  await page.getByRole("button", { name: "Activate" }).click();
  await expect(page.getByRole("button", { name: "Create project" })).toBeVisible();

  await page.goto(`/opportunities/${opportunityId}`);
  await page.getByRole("button", { name: "Move stage" }).click();
  const transition = page.getByRole("dialog", { name: "Move deal stage" });
  await transition.getByRole("combobox").selectOption({ label: "Won" });
  await transition.getByRole("button", { name: "Move deal", exact: true }).click();
  await expect(page.getByRole("button", { name: "Move stage" })).toHaveCount(0);

  await page.goto(`/contracts/${contractId}`);
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page).toHaveURL(/\/projects\/\d+$/);
  const projectId = Number(page.url().match(/(\d+)$/)[1]);

  await page.getByRole("button", { name: "Log time" }).click();
  const time = page.getByRole("dialog", { name: "Log project time" });
  await time.getByLabel("Minutes").fill("90");
  await time.getByLabel("Description").fill("Discovery and delivery planning");
  await time.getByLabel(/Hourly rate/).fill("150");
  await time.getByRole("button", { name: "Log time", exact: true }).click();
  await expect(page.getByText("Action completed.")).toBeVisible();

  await page.getByRole("button", { name: "Add expense" }).click();
  const expense = page.getByRole("dialog", { name: "Add project expense" });
  await expense.getByLabel("Vendor").fill("Lifecycle Supplies");
  await expense.getByLabel("Description").fill("Research materials");
  await expense.getByLabel(/Net amount/).fill("125");
  await expense.getByRole("button", { name: "Add expense", exact: true }).click();
  await expect(page.getByText("Action completed.")).toBeVisible();

  await page.goto("/billing");
  await page.getByRole("button", { name: "New invoice" }).click();
  const invoice = page.getByRole("dialog", { name: "New invoice" });
  await invoice.getByLabel("Account").selectOption({ label: accountName });
  await invoice.getByLabel(/Project/).selectOption({ label: commercialTitle });
  await invoice.getByLabel("Line description").fill(invoiceLine);
  await invoice.getByLabel(/Amount/).fill("12000");
  await invoice.getByRole("button", { name: "Create draft invoice" }).click();
  await expect(page).toHaveURL(/\/invoices\/\d+$/);
  const invoiceId = Number(page.url().match(/(\d+)$/)[1]);
  await page.getByRole("button", { name: "Issue invoice" }).click();
  await expect(page.getByRole("button", { name: "Payment link" })).toBeVisible();
  await json(await request.post("/api/v1/integrations/credentials/stripe", {
    headers: { "Idempotency-Key": `stripe-credential-${suffix}` },
    data: { secret: "sk_test_playwright" },
  }));
  await page.getByRole("button", { name: "Payment link" }).click();
  await expect(page.getByText("Stripe payment link queued.")).toBeVisible();
  await page.goto("/settings");
  await page.getByRole("button", { name: "Reconcile now" }).nth(1).click();
  await expect(page.getByText(/Stripe reconciliation job .* queued/)).toBeVisible();

  async function recordPayment(amount, reference) {
    await page.goto("/billing?view=payments");
    await page.getByRole("button", { name: "New payment" }).click();
    const payment = page.getByRole("dialog", { name: "Record payment" });
    await payment.getByLabel(/Invoice allocation/).selectOption(String(invoiceId));
    await payment.getByLabel(/Amount/).fill(amount);
    await payment.getByLabel("Reference").fill(reference);
    await payment.getByRole("button", { name: "Record payment", exact: true }).click();
    await expect(page).toHaveURL(/\/payments\/\d+$/);
  }

  await recordPayment("4000", `partial-${suffix}`);
  await page.goto(`/invoices/${invoiceId}`);
  await expect(page.getByText("Part-paid", { exact: true })).toBeVisible();
  await recordPayment("8000", `settlement-${suffix}`);
  await page.goto(`/invoices/${invoiceId}`);
  await expect(page.locator(".badge").filter({ hasText: /^Paid$/ })).toBeVisible();

  const opportunity = await json(await request.get(`/api/v1/opportunities/${opportunityId}`));
  await json(await request.put(`/api/v1/client-success/${opportunity.account_id}`, {
    data: {
      onboarding_status: "Complete",
      open_risks: 0,
      next_review_on: isoDate(14),
      renewal_on: isoDate(30),
      notes: "Renewal acceptance lifecycle",
    },
  }));
  const renewals = await json(await request.post("/api/v1/client-success/renewals/process", {
    headers: { "Idempotency-Key": `renewal-e2e-${suffix}` },
    data: { days: 90 },
  }));
  expect(renewals.created_count).toBeGreaterThanOrEqual(1);
  const renewal = renewals.items.find((item) => item.account_id === opportunity.account_id);
  expect(renewal.type).toBe("Renewal");

  await page.goto("/client-success");
  await expect(page.getByRole("link", { name: accountName, exact: true })).toBeVisible();
  await page.goto(`/opportunities/${renewal.id}`);
  await expect(page.getByRole("heading", { name: `${accountName} renewal` })).toBeVisible();

  const project = await json(await request.get(`/api/v1/projects/${projectId}`));
  expect(project.opportunity_id).toBe(opportunityId);
});

test("operator can manage catalog, manual delivery, client success and invoice credits", async ({ page, request }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "The operations workflow is exercised once; responsive routes have a dedicated gate.");
  const suffix = unique();
  const accountName = `Operations Client ${suffix}`;
  const catalogName = `Advisory day ${suffix}`;
  const proposalTitle = `Operations proposal ${suffix}`;
  const projectName = `Manual delivery ${suffix}`;
  const account = await json(await request.post("/api/v1/accounts", { data: { name: accountName, domain: `ops-${suffix}.example` } }));
  page.on("dialog", (dialog) => dialog.accept());

  await page.goto("/proposals?view=catalog");
  await page.getByRole("button", { name: "New catalog item" }).click();
  const catalogDialog = page.getByRole("dialog", { name: "New catalog item" });
  await catalogDialog.getByLabel("Name").fill(catalogName);
  await catalogDialog.getByLabel("Description").fill("A reusable consulting service");
  await catalogDialog.getByLabel("Unit", { exact: true }).fill("day");
  await catalogDialog.getByLabel(/Unit price/).fill("250");
  await expect(catalogDialog.getByLabel(/VAT rate/)).toBeDisabled();
  await catalogDialog.getByRole("button", { name: "Create item" }).click();
  const catalogRow = page.locator(".catalog-row").filter({ hasText: catalogName });
  await expect(catalogRow).toBeVisible();
  await catalogRow.getByRole("button", { name: "Edit" }).click();
  const editCatalog = page.getByRole("dialog", { name: "Edit catalog item" });
  await editCatalog.getByLabel(/Unit price/).fill("275");
  await editCatalog.getByRole("button", { name: "Save item" }).click();
  await expect(catalogRow.getByText("£275 / day")).toBeVisible();

  await page.getByRole("button", { name: "Proposals", exact: true }).click();
  await page.getByRole("button", { name: "New proposal" }).click();
  const proposal = page.getByRole("dialog", { name: "New proposal" });
  await proposal.getByLabel("Account").selectOption(String(account.id));
  await proposal.getByLabel("Title").fill(proposalTitle);
  const catalogSelect = proposal.getByLabel("Catalog item for line 1");
  const catalogValue = await catalogSelect.locator("option", { hasText: catalogName }).getAttribute("value");
  await catalogSelect.selectOption(catalogValue);
  await expect(proposal.getByLabel("Unit price / Amount (£) for line 1")).toHaveValue("275.00");
  await proposal.getByRole("button", { name: "Add line" }).click();
  await proposal.getByLabel("Line description for line 2").fill("Follow-up workshop");
  await proposal.getByLabel("Quantity for line 2").fill("2");
  await proposal.getByLabel("Unit price / Amount (£) for line 2").fill("75");
  await proposal.getByLabel("Discount for line 2").fill("10");
  await proposal.getByRole("button", { name: "Create proposal" }).click();
  await expect(page).toHaveURL(/\/proposals\/\d+$/);
  const proposalId = Number(page.url().match(/(\d+)$/)[1]);
  const savedProposal = await json(await request.get(`/api/v1/proposals/${proposalId}`));
  expect(savedProposal.lines).toHaveLength(2);
  expect(savedProposal.lines[0].catalog_item_id).toBe(Number(catalogValue));
  expect(savedProposal.total_pence).toBe(41_000);

  await page.goto("/projects");
  await page.getByRole("button", { name: "New project" }).click();
  const projectDialog = page.getByRole("dialog", { name: "New project" });
  await projectDialog.getByLabel("Project name").fill(projectName);
  await projectDialog.getByLabel(/Account/).selectOption(String(account.id));
  await projectDialog.getByLabel("Budget (£)").fill("5000");
  await projectDialog.getByRole("button", { name: "Create project" }).click();
  await expect(page).toHaveURL(/\/projects\/\d+$/);
  const projectId = Number(page.url().match(/(\d+)$/)[1]);
  await page.getByRole("button", { name: "Add milestone" }).click();
  const milestone = page.getByRole("dialog", { name: "Add project milestone" });
  await milestone.getByLabel("Milestone title").fill("Discovery complete");
  await milestone.getByLabel("Billing amount (£)").fill("1000");
  await milestone.getByRole("button", { name: "Add milestone", exact: true }).click();
  await page.getByRole("button", { name: "Update status" }).click();
  const projectStatus = page.getByRole("dialog", { name: "Update project status" });
  await projectStatus.getByRole("combobox").selectOption("Blocked");
  await projectStatus.getByRole("button", { name: "Update project", exact: true }).click();
  await expect(page.locator(".record-hero .badge")).toHaveText("Blocked");
  const savedProject = await json(await request.get(`/api/v1/projects/${projectId}`));
  expect(savedProject.milestones).toHaveLength(1);

  await page.goto("/client-success");
  await page.getByRole("button", { name: "New success plan" }).click();
  const success = page.getByRole("dialog", { name: "New client success plan" });
  await success.getByLabel("Account").selectOption(String(account.id));
  await success.getByLabel("Onboarding").selectOption("In progress");
  await success.getByLabel("Manual health").selectOption("Watch");
  await success.getByLabel("Open risks").fill("2");
  await success.getByLabel("Next review").fill(isoDate(14));
  await success.getByLabel("Renewal date").fill(isoDate(90));
  await success.getByRole("button", { name: "Create success plan" }).click();
  await expect(page).toHaveURL(new RegExp(`/client-success/${account.id}$`));
  await page.getByRole("button", { name: "Update success plan" }).click();
  const updateSuccess = page.getByRole("dialog", { name: `Update ${accountName}` });
  await updateSuccess.getByLabel("Onboarding").selectOption("Complete");
  await updateSuccess.getByLabel("Manual health").selectOption("Healthy");
  await updateSuccess.getByLabel("Open risks").fill("0");
  await updateSuccess.getByRole("button", { name: "Update success plan" }).click();
  await expect(page.locator(".record-hero .badge")).toHaveText("Healthy");

  const invoice = await json(await request.post("/api/v1/invoices", { data: {
    account_id: account.id,
    currency: "GBP",
    due_on: isoDate(14),
    customer_name: accountName,
    lines: [{ description: "Credit test", quantity: "1", unit_price_pence: 10_000, tax_rate_bps: 0, discount_bps: 0 }],
  } }));
  await json(await request.post(`/api/v1/invoices/${invoice.id}/issue`, { headers: { "Idempotency-Key": `issue-${suffix}`, "X-CRM-Confirmed": "true" }, data: {} }));
  await page.goto(`/invoices/${invoice.id}`);
  await page.getByRole("button", { name: "Credit invoice" }).click();
  const credit = page.getByRole("dialog", { name: "Credit invoice" });
  await credit.getByLabel("Credit type").selectOption("partial");
  await credit.getByLabel("Credit total (£)").fill("25");
  await credit.getByLabel("Reason").fill("Scope reduced after issue");
  await credit.getByRole("button", { name: "Create & issue credit note" }).click();
  await expect(page).toHaveURL(/\/credit-notes\/\d+$/);
  await expect(page.locator(".record-hero .badge")).toHaveText("Issued");
  const creditedInvoice = await json(await request.get(`/api/v1/invoices/${invoice.id}`));
  expect(creditedInvoice.credited_pence).toBe(2_500);
  expect(creditedInvoice.outstanding_pence).toBe(7_500);

  await page.goto("/proposals?view=catalog");
  const finalCatalogRow = page.locator(".catalog-row").filter({ hasText: catalogName });
  await finalCatalogRow.getByRole("button", { name: `Archive ${catalogName}` }).click();
  await expect(finalCatalogRow).toHaveCount(0);
});
