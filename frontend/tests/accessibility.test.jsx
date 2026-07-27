import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { AppDialog } from "../src/components/common";
import { QuickCreate } from "../src/components/QuickCreate";
import { WorkflowDialog } from "../src/components/WorkflowDialogs";

describe("accessible dialogs and forms", () => {
  it("associates dialog names and descriptions", () => {
    const html = renderToStaticMarkup(
      <AppDialog description="A useful explanation" onClose={() => {}} open title="Example action">
        <button type="button">Continue</button>
      </AppDialog>,
    );
    const titleId = html.match(/aria-labelledby="([^"]+)"/)?.[1];
    const descriptionId = html.match(/aria-describedby="([^"]+)"/)?.[1];
    expect(titleId).toBeTruthy();
    expect(descriptionId).toBeTruthy();
    expect(html).toContain(`id="${titleId}"`);
    expect(html).toContain(`id="${descriptionId}"`);
    expect(html).toContain('aria-label="Close Example action"');
  });

  it("keeps required quick-create actions keyboard available", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter><QuickCreate initialType="accounts" onClose={() => {}} open /></MemoryRouter>,
    );
    expect(html).toContain('aria-busy="false"');
    expect(html).toContain("data-dialog-initial-focus");
    expect(html).toMatch(/<input[^>]*required=""[^>]*value=""/);
    expect(html).toContain("Create account");
    expect(html).not.toMatch(/<button[^>]*disabled[^>]*>Create account/);
  });

  it("announces workflow progress without replacing the visible action label", () => {
    const html = renderToStaticMarkup(
      <WorkflowDialog onClose={() => {}} onSubmit={() => {}} open submitLabel="Save change" title="Edit record">
        <label><span>Name</span><input required /></label>
      </WorkflowDialog>,
    );
    expect(html).toContain('aria-busy="false"');
    expect(html).toContain('aria-live="polite"');
    expect(html).toContain('role="status"');
    expect(html).toContain("Save change");
  });
});
