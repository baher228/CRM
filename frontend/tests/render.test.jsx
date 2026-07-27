import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { App } from "../src/App";

function renderRoute(path) {
  return renderToStaticMarkup(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>);
}

describe("routed workspace shell", () => {
  it("renders the command view and grouped navigation", () => {
    const html = renderRoute("/");
    expect(html).toContain("Today’s command view");
    expect(html).toContain("Primary navigation");
    expect(html).toContain("Tender Radar");
  });

  it("deep-links directly into record workspaces", () => {
    const html = renderRoute("/accounts/42");
    expect(html).toContain("Loading account");
    expect(html).toContain("class=\"active\"");
  });

  it("renders the pipeline view without a horizontal table", () => {
    const html = renderRoute("/pipeline?view=forecast");
    expect(html).toContain("Pipeline");
    expect(html).toContain("Ranked queue");
    expect(html).not.toContain("<table");
  });

  it("surfaces manual project creation without falling back to account quick-create", () => {
    const html = renderRoute("/projects");
    expect(html).toContain("Projects");
    expect(html).toContain("New project");
  });

  it("renders persisted list controls and honest custom-field management", () => {
    const accounts = renderRoute("/accounts");
    expect(accounts).toContain("Saved Accounts view");
    expect(accounts).toContain("Columns");
    expect(accounts).toContain("Save view");
    expect(accounts).toContain("Include archived");

    const settings = renderRoute("/settings");
    expect(settings).toContain("Custom fields");
    expect(settings).toContain("New field");
    expect(settings).toContain("currently supports values on these two record types");
    expect(settings).toContain("Import CSV");
    expect(settings).toContain("Restore backup");
    expect(settings).toContain("Open job recovery");
  });

  it("renders the persisted Tender Radar control surface", () => {
    const html = renderRoute("/tenders");
    expect(html).toContain("Run discovery");
    expect(html).toContain("Discovery runs");
  });
});
