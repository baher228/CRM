import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../src/hooks", () => ({
  useDocumentTitle: () => {},
  useResource: () => ({
    data: {
      briefing: "7 active signals across replies, deadlines, deals, delivery, cash and renewals.",
      counts: {
        needs_action: 7,
        overdue_work: 1,
        risky_deals: 1,
        unread_replies: 1,
        tender_deadlines: 1,
        overdue_invoices: 1,
      },
      outstanding_minor: 100_000,
      priorities: [
        { type: "email_thread", id: 10, title: "Re: recovery plan", reason: "Reply from buyer@example.com", route: "/inbox?thread=10", last_message_at: "2026-07-10T09:00:00Z", priority: "high" },
        { type: "task", id: 11, title: "Send recovery plan", reason: "Overdue since 2026-07-09", route: "/tasks/11", due_at: "2026-07-09", priority: "high" },
        { type: "tender", id: 12, title: "Library services framework", reason: "Deadline 2026-07-13 for City Library", route: "/tenders/12", deadline: "2026-07-13" },
      ],
      upcoming_meetings: [
        { type: "meeting", id: 13, title: "Commercial review", reason: "Video call", route: "/calendar?event=13", starts_at: "2026-07-11T09:00:00Z" },
      ],
      risk_signals: [
        { type: "opportunity", id: 14, title: "Renewal rescue", reason: "Expected close was 2026-07-09", route: "/opportunities/14" },
        { type: "project", id: 15, title: "Onboarding rollout", reason: "Blocked - waiting for data access", route: "/projects/15" },
        { type: "invoice", id: 16, title: "INV-2026-001", reason: "Overdue since 2026-07-09 - GBP 1,000.00 outstanding", route: "/invoices/16" },
        { type: "renewal", id: 17, title: "Signal Works", reason: "Renews in 60 days", route: "/client-success/1" },
      ],
    },
    loading: false,
    error: null,
    reload: () => {},
  }),
}));

import { TodayView } from "../src/views/TodayView";

function renderToday() {
  return renderToStaticMarkup(<MemoryRouter><TodayView /></MemoryRouter>);
}

describe("Today operating center", () => {
  it("renders the category counts and actionable operating queue", () => {
    const html = renderToday();
    expect(html).toContain("7 active signals across replies");
    expect(html).toContain("Unread replies");
    expect(html).toContain("1 due item · 1 deal risk");
    expect(html).toContain("Re: recovery plan");
    expect(html).toContain("Send recovery plan");
    expect(html).toContain("Library services framework");
    expect(html).toContain('href="/inbox?thread=10"');
    expect(html).toContain('href="/tasks/11"');
    expect(html).toContain('href="/tenders/12"');
  });

  it("keeps meetings, delivery, cash and renewals visible as direct links", () => {
    const html = renderToday();
    expect(html).toContain("Next seven days");
    expect(html).toContain("Commercial review");
    expect(html).toContain("Expected close was 2026-07-09");
    expect(html).toContain("Blocked - waiting for data access");
    expect(html).toContain("GBP 1,000.00 outstanding");
    expect(html).toContain("Renews in 60 days");
    expect(html).toContain('href="/calendar?event=13"');
    expect(html).toContain('href="/projects/15"');
    expect(html).toContain('href="/invoices/16"');
    expect(html).toContain('href="/client-success/1"');
    expect(html).not.toContain("<table");
  });
});
