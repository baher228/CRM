import React from "react";
import {
  AlertCircle,
  ArrowUpRight,
  CalendarClock,
  CheckCircle2,
  Clock3,
  FileClock,
  FolderKanban,
  ListTodo,
  Mail,
  PoundSterling,
  Radio,
  ReceiptText,
  RefreshCw,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import { Link } from "react-router-dom";

import { EmptyState, LoadingState, Metric, SignalLine, UnavailableState } from "../components/common";
import { useDocumentTitle, useResource } from "../hooks";
import { compactNumber, formatDate, formatMoney, recordName } from "../utils/format";
import { PageIntro } from "./ResourceView";

function pick(source, ...keys) {
  return keys.map((key) => source?.[key]).find((value) => value !== undefined && value !== null);
}

function firstItems(source, ...keys) {
  for (const key of keys) {
    if (Array.isArray(source?.[key])) return source[key];
  }
  return [];
}

const signalMeta = {
  email_thread: [Mail, "reply"],
  task: [ListTodo, "task"],
  tender: [FileClock, "tender"],
  opportunity: [TrendingUp, "deal"],
  project: [FolderKanban, "project"],
  invoice: [ReceiptText, "invoice"],
  renewal: [RefreshCw, "renewal"],
};

function metaFor(item) {
  return signalMeta[item?.type] || [AlertCircle, "record"];
}

export function TodayView() {
  const { data: dashboard, loading, error, reload } = useResource("dashboard", { list: false });
  useDocumentTitle("Today");
  const counts = dashboard?.counts || {};
  const activity = firstItems(dashboard, "priorities", "action_items", "recent_activity").slice(0, 12);
  const meetings = firstItems(dashboard, "upcoming_meetings", "upcoming_events", "meetings", "calendar").slice(0, 6);
  const risks = firstItems(dashboard, "risk_signals", "deal_risks", "project_blockers", "overdue_invoice_items", "renewals").slice(0, 12);
  const firstName = dashboard?.operator_name?.split(" ")[0];
  const dueWork = counts.overdue_work || 0;
  const riskyDeals = counts.risky_deals || 0;
  const actionNote = `${compactNumber(dueWork)} due ${dueWork === 1 ? "item" : "items"} · ${compactNumber(riskyDeals)} deal ${riskyDeals === 1 ? "risk" : "risks"}`;
  const cashNote = `${compactNumber(counts.overdue_invoices || 0)} overdue`;

  return (
    <div className="today-view">
      <PageIntro
        actions={<button className="button button-primary" onClick={() => window.dispatchEvent(new CustomEvent("crm:quick-create"))} type="button"><Sparkles aria-hidden="true" size={16} /> Capture something</button>}
        description="Replies, deadlines, delivery and cash—ranked into one working day."
        eyebrow={new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", timeZone: "Europe/London" }).format(new Date())}
        signal="Live local signals"
        title={firstName ? `Good morning, ${firstName}` : "Today’s command view"}
      />

      <div className="signal-deck" aria-label="Workspace pulse">
        <div className="signal-deck-copy">
          <SignalLine label="Operator briefing" />
          <strong>{pick(dashboard, "briefing", "summary") || "Your CRM is ready for the next move."}</strong>
          <p>{error ? "Local records remain safe. Reconnect the service to refresh live priorities." : "Work top to bottom: respond, unblock, close, then plan."}</p>
        </div>
        <div aria-hidden="true" className="signal-wave"><i /><i /><i /><i /><i /><i /><i /><i /><i /></div>
      </div>

      {loading ? <LoadingState label="Building today’s view" /> : null}
      {!loading && error ? <UnavailableState compact error={error} onRetry={reload} /> : null}

      <section aria-label="Today at a glance" className="metric-strip">
        <Metric label="Needs action" value={compactNumber(pick(counts, "needs_action") ?? pick(dashboard, "needs_action", "overdue_tasks") ?? 0)} note={actionNote} tone="urgent" />
        <Metric label="Unread replies" value={compactNumber(pick(counts, "unread_replies") ?? 0)} note="Inbound conversations" />
        <Metric label="Tender deadlines" value={compactNumber(pick(counts, "tender_deadlines") ?? pick(dashboard, "tender_deadlines", "tenders_due_soon") ?? 0)} note="Inside seven days" />
        <Metric label="Outstanding" value={pick(dashboard, "outstanding_pence", "outstanding_minor") !== undefined ? formatMoney(pick(dashboard, "outstanding_pence", "outstanding_minor")) : "—"} note={cashNote} tone="cash" />
      </section>

      <div className="today-grid">
        <section className="panel priority-panel">
          <header className="panel-heading">
            <div><span className="section-kicker">01 · Act</span><h2>Priority queue</h2></div>
            <Link to="/tasks">All work <ArrowUpRight aria-hidden="true" size={14} /></Link>
          </header>
          {activity.length ? (
            <ol className="priority-list">
              {activity.map((item, index) => {
                const [Icon, label] = metaFor(item);
                const dueAt = item.due_at || item.deadline || item.due_on || item.renewal_on || item.last_message_at;
                return (
                  <li key={`${item.type || "record"}-${item.id || index}`}>
                    <span className="priority-index">{String(index + 1).padStart(2, "0")}</span>
                    <span className={`priority-mark priority-${item.priority || (index < 2 ? "high" : "normal")}`}><Icon aria-hidden="true" size={16} /></span>
                    <div>
                      <strong>{item.title || recordName(item, "Review this item")}</strong>
                      <span>{item.reason || item.detail || item.description || item.related_name || "Open the linked record for context."}</span>
                      <Link className="record-open" to={item.route || "/"}>Open {label} <ArrowUpRight aria-hidden="true" size={12} /></Link>
                    </div>
                    <time>{dueAt ? formatDate(dueAt, { withTime: item.type === "email_thread" || item.type === "task" }) : label}</time>
                  </li>
                );
              })}
            </ol>
          ) : <EmptyState icon={CheckCircle2} title="Priority queue is clear" message="New replies, overdue work and approaching deadlines will land here." />}
        </section>

        <aside className="today-side">
          <section className="panel agenda-panel">
            <header className="panel-heading"><div><span className="section-kicker">02 · Meet</span><h2>Next seven days</h2></div><Link to="/calendar">Calendar</Link></header>
            {meetings.length ? meetings.map((meeting, index) => (
              <div className="agenda-row" key={`${meeting.type || "meeting"}-${meeting.id || index}`}>
                <time>{formatDate(meeting.starts_at || meeting.start_at || meeting.date, { withTime: true })}</time>
                <div>
                  <strong>{meeting.title || recordName(meeting, "Meeting")}</strong>
                  <span>{meeting.reason || meeting.account_name || meeting.location || "CRM calendar"}</span>
                  <Link className="record-open" to={meeting.route || "/calendar"}>Open meeting <ArrowUpRight aria-hidden="true" size={11} /></Link>
                </div>
                <CalendarClock aria-hidden="true" size={17} />
              </div>
            )) : <p className="panel-empty"><Clock3 aria-hidden="true" size={17} /> No linked meetings in the next seven days.</p>}
          </section>

          <section className="panel risk-panel">
            <header className="panel-heading"><div><span className="section-kicker">03 · Watch</span><h2>Risk signals</h2></div><Radio aria-hidden="true" size={16} /></header>
            {risks.length ? risks.map((risk, index) => {
              const [, label] = metaFor(risk);
              return (
                <div className="risk-row" key={`${risk.type || "risk"}-${risk.id || index}`}>
                  <span className="risk-pip" />
                  <div>
                    <strong>{risk.title || recordName(risk, "Risk needs review")}</strong>
                    <span>{risk.reason || risk.detail || risk.status}</span>
                    <Link className="record-open" to={risk.route || "/"}>Open {label} <ArrowUpRight aria-hidden="true" size={11} /></Link>
                  </div>
                </div>
              );
            }) : (
              <div className="risk-clear"><CheckCircle2 aria-hidden="true" size={18} /><span><strong>No active risk signals</strong><small>Blockers, overdue invoices and renewals will appear here.</small></span></div>
            )}
            <Link className="cash-link" to="/billing"><PoundSterling aria-hidden="true" size={17} /> Review receivables <ArrowUpRight aria-hidden="true" size={14} /></Link>
          </section>
        </aside>
      </div>
    </div>
  );
}
