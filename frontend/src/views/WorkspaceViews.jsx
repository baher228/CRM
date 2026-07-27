import React, { useEffect, useMemo, useState } from "react";
import {
  BarChart3,
  CalendarDays,
  Check,
  ChevronRight,
  CircleDollarSign,
  Cloud,
  FileBarChart,
  Inbox,
  Landmark,
  Mail,
  MailOpen,
  ArrowUpRight,
  Plus,
  Radar,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
  Target,
  UsersRound,
  Workflow,
} from "lucide-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { api } from "../api";
import { Badge, EmptyState, LoadingState, Metric, PageControls, RecordCard, SignalLine, UnavailableState } from "../components/common";
import { EmailTemplatesPanel, ScheduledSendsPanel, SuppressionsPanel, ThreadLinksControl } from "../components/CommunicationsControls";
import { CustomFieldManager } from "../components/DataControls";
import { AutomationDialog, AutomationWorkflowActions, SequenceDialog } from "../components/SequenceAutomationWorkflows";
import {
  BackupCreateControl,
  BackupRestoreControl,
  BusinessProfileControl,
  CsvImportControl,
  DocumentCreateControl,
  DocumentTemplateManager,
  IntegrityCheckControl,
  JobRecoveryControl,
  ReportsCsvExportControl,
} from "../components/SystemControlDialogs";
import {
  CalendarEventDialog,
  CatalogWorkspace,
  ClientSuccessDialog,
  CommercialCreateDialog,
  EmailComposer,
  ProjectCreateDialog,
  WorkflowDialog,
} from "../components/WorkflowDialogs";
import { useDocumentTitle, useResource } from "../hooks";
import { compactNumber, formatDate, formatMoney, recordName, titleCase } from "../utils/format";
import { dealStages, resourceConfigs } from "../workspace";
import { PageIntro, ResourceView } from "./ResourceView";

function Segmented({ label, options, value, onChange }) {
  return (
    <div aria-label={label} className="segmented" role="group">
      {options.map((option) => (
        <button aria-pressed={value === option.value} key={option.value} onClick={() => onChange(option.value)} type="button">{option.label}</button>
      ))}
    </div>
  );
}

export function InboxView() {
  const [params, setParams] = useSearchParams();
  const view = ["inbox", "templates", "queue", "suppressions"].includes(params.get("view")) ? params.get("view") : "inbox";
  const { data: threads, loading, error, reload } = useResource("email/threads");
  const [selectedId, setSelectedId] = useState(null);
  const [selectedThread, setSelectedThread] = useState(null);
  const [composerOpen, setComposerOpen] = useState(false);
  const [composerThread, setComposerThread] = useState(null);
  const [feedback, setFeedback] = useState("");
  useDocumentTitle("Inbox");
  useEffect(() => {
    if (!selectedId && threads[0]) setSelectedId(threads[0].id);
  }, [threads, selectedId]);
  const selected = threads.find((thread) => thread.id === selectedId);
  useEffect(() => {
    if (!selectedId) {
      setSelectedThread(null);
      return;
    }
    let active = true;
    setSelectedThread(selected || null);
    api.get("email/threads/" + selectedId).then(async (item) => {
      if (!active) return;
      if (item.unread) {
        try {
          item = await api.post(`email/threads/${selectedId}/read`, {});
        } catch {
          // Keep the thread readable if the local read acknowledgement fails.
        }
        reload();
      }
      if (active) setSelectedThread(item);
    }).catch(() => active && setSelectedThread(selected || null));
    return () => { active = false; };
  }, [selectedId]);

  async function createReplyTask() {
    if (!selected) return;
    const link = selectedThread?.links?.[0];
    try {
      await api.post("tasks", {
        title: "Reply: " + (selected.subject || "Email conversation"),
        entity_type: link?.entity_type || "",
        entity_id: link?.entity_id,
        priority: "Medium",
      });
      setFeedback("Follow-up task created.");
    } catch (caught) {
      setFeedback(caught.message);
    }
  }

  return (
    <div>
      <PageIntro
        actions={view === "inbox" ? <button className="button button-primary" onClick={() => { setComposerThread(null); setComposerOpen(true); }} type="button"><Send aria-hidden="true" size={16} /> Compose</button> : null}
        description="Gmail conversations matched to the people and work they affect."
        eyebrow="Command"
        signal={error ? "Google disconnected" : `${threads.filter((item) => item.unread ?? (!item.read_at && !item.is_read)).length} unread`}
        title="Inbox"
      />
      <Segmented label="Inbox view" onChange={(next) => setParams({ view: next })} options={[{ value: "inbox", label: "Conversations" }, { value: "templates", label: "Templates" }, { value: "queue", label: "Delivery queue" }, { value: "suppressions", label: "Suppressions" }]} value={view} />
      {view === "inbox" && loading ? <LoadingState label="Syncing linked conversations" /> : null}
      {view === "inbox" && !loading && error ? <UnavailableState error={error} onRetry={reload} /> : null}
      {view === "inbox" && !loading && !error && !threads.length ? (
        <EmptyState icon={Inbox} title="Inbox is ready to connect" message="Connect Google Workspace in Settings. Local CRM work remains available while Gmail is disconnected." action={<Link className="button button-primary" to="/settings">Open Settings</Link>} />
      ) : null}
      {view === "inbox" && !loading && !error && threads.length ? (
        <section aria-label="Email workspace" className="inbox-workspace">
          <div className="thread-list">
            <div className="thread-list-heading"><strong>Conversations</strong><button aria-label="Refresh inbox" className="icon-button" onClick={reload} type="button"><RefreshCw aria-hidden="true" size={16} /></button></div>
            {threads.map((thread) => {
              const active = selectedId === thread.id;
              return (
                <button aria-pressed={active} className={`thread-row ${active ? "active" : ""}`} key={thread.id} onClick={() => setSelectedId(thread.id)} type="button">
                  <span className="thread-icon">{thread.unread === false || thread.read_at || thread.is_read ? <MailOpen aria-hidden="true" size={16} /> : <Mail aria-hidden="true" size={16} />}</span>
                  <span><strong>{thread.sender_name || thread.from_name || thread.from_email || "Unknown sender"}</strong><b>{thread.subject || "No subject"}</b><small>{thread.snippet || thread.preview || "Open this thread to read the conversation."}</small></span>
                  <time>{formatDate(thread.last_message_at || thread.received_at, { withTime: true })}</time>
                </button>
              );
            })}
          </div>
          <article className="message-pane">
            <header>
              <div><span className="eyebrow">Linked conversation</span><h2>{selectedThread?.subject || selected?.subject || "Conversation"}</h2><p>{selectedThread?.sender_name || selected?.sender_name || selectedThread?.from_email || selected?.from_email}</p></div>
              {selected?.account_name ? <Badge tone="info">{selected.account_name}</Badge> : null}
            </header>
            <div className="message-body">
              <p>{selectedThread?.body_text || selectedThread?.messages?.at(-1)?.body_text || selected?.snippet || selected?.preview || "Message content will appear after the Gmail thread is synchronized."}</p>
            </div>
            {selectedThread ? <ThreadLinksControl onChanged={setSelectedThread} thread={selectedThread} /> : null}
            <footer><button className="button button-primary" onClick={() => { setComposerThread(selectedThread || selected); setComposerOpen(true); }} type="button">Reply</button><button className="button button-quiet" onClick={createReplyTask} type="button"><Check aria-hidden="true" size={15} /> Create task</button></footer>
          </article>
        </section>
      ) : null}
      {view === "templates" ? <EmailTemplatesPanel /> : null}
      {view === "queue" ? <ScheduledSendsPanel /> : null}
      {view === "suppressions" ? <SuppressionsPanel /> : null}
      {feedback ? <p aria-live="polite" className="action-feedback">{feedback}</p> : null}
      <EmailComposer onClose={() => setComposerOpen(false)} onSent={() => setFeedback("Email queued for delivery.")} open={composerOpen} thread={composerThread} />
    </div>
  );
}

export function CalendarView() {
  const { data: events, loading, error, mutate, reload } = useResource("calendar/events");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [feedback, setFeedback] = useState("");
  useDocumentTitle("Calendar");
  const grouped = useMemo(() => events.reduce((groups, event) => {
    const day = formatDate(event.starts_at || event.start_at || event.date);
    groups[day] ||= [];
    groups[day].push(event);
    return groups;
  }, {}), [events]);

  return (
    <div>
      <PageIntro actions={<button className="button button-primary" onClick={() => { setSelectedEvent(null); setDialogOpen(true); }} type="button"><Plus aria-hidden="true" size={16} /> Add event</button>} description="Meetings, preparation and follow-up in Europe/London." eyebrow="Command" signal={error ? "Calendar disconnected" : `${events.length} linked events`} title="Calendar" />
      {feedback ? <p aria-live="polite" className="action-feedback positive">{feedback}</p> : null}
      {loading ? <LoadingState label="Loading the calendar" /> : null}
      {!loading && error ? <UnavailableState error={error} onRetry={reload} /> : null}
      {!loading && !error && !events.length ? <EmptyState icon={CalendarDays} title="No linked events" message="Connect Google Calendar in Settings or add a local event." /> : null}
      {!loading && !error && events.length ? (
        <div className="agenda-board">
          {Object.entries(grouped).map(([day, dayEvents]) => (
            <section className="agenda-day" key={day}>
              <header><span>{day}</span><small>{dayEvents.length} {dayEvents.length === 1 ? "event" : "events"}</small></header>
              <div>{dayEvents.map((event) => (
                <button aria-label={`Edit ${recordName(event, "calendar event")}`} className="calendar-event" key={event.id} onClick={() => { setSelectedEvent(event); setDialogOpen(true); }} type="button">
                  <time>{formatDate(event.starts_at || event.start_at, { withTime: true })}</time>
                  <span className="event-rail" aria-hidden="true" />
                  <div><strong>{recordName(event, "Untitled event")}</strong><p>{event.account_name || event.location || "Local calendar"}</p>{event.preparation_brief ? <small>{event.preparation_brief}</small> : null}</div>
                  <ChevronRight aria-hidden="true" size={16} />
                </button>
              ))}</div>
            </section>
          ))}
        </div>
      ) : null}
      <CalendarEventDialog
        event={selectedEvent}
        onArchived={async (archived) => { mutate((current) => current.filter((item) => item.id !== archived.id)); setSelectedEvent(null); setFeedback("Calendar event archived."); await reload(); }}
        onClose={() => setDialogOpen(false)}
        onSaved={async (saved) => { mutate((current) => current.some((item) => item.id === saved.id) ? current.map((item) => item.id === saved.id ? saved : item) : [...current, saved]); setSelectedEvent(saved); setFeedback(selectedEvent ? "Calendar event updated." : "Calendar event created."); await reload(); }}
        open={dialogOpen}
      />
    </div>
  );
}

const emptyDiscovery = {
  niche: "",
  region: "",
  portals: "",
  limit: "10",
  deadline_window: "",
  minimum_value: "",
  open_notices_only: true,
};

export function TenderRadarView() {
  const tendersState = useResource("tenders");
  const runsState = useResource("discovery/runs");
  const [runOpen, setRunOpen] = useState(false);
  const [form, setForm] = useState(emptyDiscovery);
  const [feedback, setFeedback] = useState(null);
  const [tenderVersion, setTenderVersion] = useState(0);
  const active = runsState.data.some((run) => ["queued", "running"].includes(run.state));
  useDocumentTitle("Tender Radar");

  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(runsState.reload, 1200);
    return () => window.clearInterval(timer);
  }, [active, runsState.reload]);

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function startRun() {
    const run = await api.post("discovery/runs", {
      niche: form.niche,
      region: form.region || undefined,
      portals: form.portals.split(",").map((item) => item.trim()).filter(Boolean),
      limit: Number(form.limit),
      deadline_window: form.deadline_window,
      minimum_value: form.minimum_value,
      open_notices_only: Boolean(form.open_notices_only),
    });
    setRunOpen(false);
    setForm(emptyDiscovery);
    setFeedback({ tone: "positive", message: "Discovery run " + run.id.slice(0, 8) + " queued." });
    runsState.reload();
  }

  async function importRun(run) {
    if (!window.confirm("Import the usable results from this persisted discovery run into Tender Radar?")) return;
    try {
      const report = await api.post("discovery/runs/" + run.id + "/import", {});
      setFeedback({ tone: "positive", message: report.imported + " tender(s) imported; " + report.already_imported + " already present." });
      runsState.reload();
      tendersState.reload();
      setTenderVersion((version) => version + 1);
    } catch (error) {
      setFeedback({ tone: "error", message: error.message });
    }
  }

  async function cancelRun(run) {
    try {
      await api.post("discovery/runs/" + run.id + "/cancel", {});
      setFeedback({ tone: "positive", message: "Discovery run cancelled. Completed results remain importable." });
      runsState.reload();
    } catch (error) {
      setFeedback({ tone: "error", message: error.message });
    }
  }

  return (
    <div>
      <PageIntro
        actions={<button className="button button-primary" onClick={() => { setForm(emptyDiscovery); setRunOpen(true); }} type="button"><Radar aria-hidden="true" size={16} /> Run discovery</button>}
        description="Persisted evidence-backed searches that can be reviewed before anything enters the CRM."
        eyebrow="Revenue"
        signal={active ? "Discovery live" : tendersState.data.length + " tender records"}
        title="Tender Radar"
      />
      {feedback ? <p aria-live="polite" className={"action-feedback " + feedback.tone}>{feedback.message}</p> : null}
      <section className="discovery-runs">
        <header><div><h2>Discovery runs</h2><p>Runs survive restarts. Results are imported only when you approve them.</p></div><button aria-label="Refresh discovery runs" className="icon-button" onClick={runsState.reload} type="button"><RefreshCw aria-hidden="true" size={16} /></button></header>
        {runsState.loading ? <LoadingState label="Loading discovery runs" /> : null}
        {runsState.error ? <UnavailableState compact error={runsState.error} onRetry={runsState.reload} /> : null}
        {!runsState.loading && !runsState.error && !runsState.data.length ? <p className="section-empty">No discovery runs yet. Start one when you have a market or service focus to search.</p> : null}
        {runsState.data.map((run) => (
          <article className="discovery-run" key={run.id}>
            <div>
              <span><strong>{run.recipe?.niche || "Discovery"}</strong><Badge>{titleCase(run.state)}</Badge></span>
              <p>{run.message || run.phase}{run.recipe?.region ? " · " + run.recipe.region : ""}</p>
              {run.error ? <small className="run-error">{run.error}</small> : null}
            </div>
            <div className="run-progress"><progress aria-label={"Discovery progress for " + (run.recipe?.niche || run.id)} max="100" value={Number(run.progress || 0)} /><span>{Number(run.progress || 0)}%</span></div>
            <div className="run-actions">
              {["queued", "running"].includes(run.state) ? <button className="button button-quiet" onClick={() => cancelRun(run)} type="button">Cancel</button> : null}
              {["completed", "cancelled"].includes(run.state) && run.results?.length ? <button className="button button-primary" onClick={() => importRun(run)} type="button">Import {run.results.length} result{run.results.length === 1 ? "" : "s"}</button> : null}
            </div>
          </article>
        ))}
      </section>
      <ResourceView embedded key={tenderVersion} resourceKey="tenders" />
      <WorkflowDialog description="Discovery runs in preview mode first. Selectable portals are optional; the search focus is required by the current discovery adapter." onClose={() => setRunOpen(false)} onSubmit={startRun} open={runOpen} submitLabel="Start discovery" title="Run tender discovery">
        <label className="field-wide"><span>Search focus</span><input onChange={(event) => update("niche", event.target.value)} placeholder="Digital transformation consultancy" required value={form.niche} /></label>
        <label><span>Region (optional)</span><input onChange={(event) => update("region", event.target.value)} placeholder="United Kingdom" value={form.region} /></label>
        <label><span>Maximum results</span><input max="100" min="1" onChange={(event) => update("limit", event.target.value)} required type="number" value={form.limit} /></label>
        <label className="field-wide"><span>Portal names or domains (optional, comma separated)</span><input onChange={(event) => update("portals", event.target.value)} placeholder="Contracts Finder, find-tender.service.gov.uk" value={form.portals} /></label>
        <label><span>Deadline window (optional)</span><input onChange={(event) => update("deadline_window", event.target.value)} placeholder="Next 90 days" value={form.deadline_window} /></label>
        <label><span>Minimum value (optional)</span><input onChange={(event) => update("minimum_value", event.target.value)} placeholder="£25,000" value={form.minimum_value} /></label>
        <label className="checkbox-field"><input checked={form.open_notices_only} onChange={(event) => update("open_notices_only", event.target.checked)} type="checkbox" /><span>Open notices only</span></label>
      </WorkflowDialog>
    </div>
  );
}

function dealStage(deal) {
  const value = String(deal.stage_name || deal.stage || deal.status || "Discovery");
  return dealStages.find((stage) => stage.toLowerCase() === value.toLowerCase()) || "Discovery";
}

export function PipelineView() {
  const [params, setParams] = useSearchParams();
  const requestedView = params.get("view") === "deals" ? "board" : params.get("view");
  const view = ["board", "queue", "table", "leads", "forecast"].includes(requestedView) ? requestedView : "board";
  const dealsState = useResource("opportunities", { enabled: view !== "leads", query: { limit: 100 }, pageSize: 100 });
  useDocumentTitle("Pipeline");
  const deals = dealsState.data;
  const openDeals = deals.filter((deal) => !["Won", "Lost"].includes(String(deal.status || dealStage(deal))));
  const dealValue = (deal) => Number(deal.value_pence ?? deal.value_minor ?? deal.amount_pence ?? deal.amount_minor ?? 0);
  const dealProbability = (deal) => deal.probability_bps !== undefined && deal.probability_bps !== null
    ? Number(deal.probability_bps) / 10_000
    : Number(deal.probability || 0) / 100;
  const totalMinor = openDeals.reduce((sum, deal) => sum + dealValue(deal), 0);
  const weightedMinor = openDeals.reduce((sum, deal) => sum + Number(deal.weighted_value_pence ?? deal.weighted_value_minor ?? dealValue(deal) * dealProbability(deal)), 0);
  const today = new Date().toISOString().slice(0, 10);
  const rankedDeals = openDeals.map((deal) => {
    const reasons = [];
    if (!deal.next_action) reasons.push("No next action");
    if (deal.expected_close_date && deal.expected_close_date < today) reasons.push("Close date overdue");
    const daysSinceUpdate = Math.max(0, (Date.now() - new Date(deal.updated_at || Date.now()).getTime()) / 86_400_000);
    if (daysSinceUpdate >= 14) reasons.push(`${Math.floor(daysSinceUpdate)} days quiet`);
    const rank = (reasons.length * 100_000_000) + dealValue(deal) * Math.max(dealProbability(deal), 0.1);
    return { deal, rank, reasons };
  }).sort((left, right) => right.rank - left.rank || dealValue(right.deal) - dealValue(left.deal));

  return (
    <div>
      <PageIntro
        actions={<button className="button button-primary" onClick={() => window.dispatchEvent(new CustomEvent("crm:quick-create", { detail: { type: view === "leads" ? "leads" : "opportunities" } }))} type="button"><Plus aria-hidden="true" size={16} /> New {view === "leads" ? "lead" : "deal"}</button>}
        description="From first signal to signed work, with one explicit next action."
        eyebrow="Revenue"
        signal={`${openDeals.length} open deals`}
        title="Pipeline"
      />
      <Segmented label="Pipeline view" onChange={(next) => setParams({ view: next })} options={[{ value: "board", label: "Board" }, { value: "queue", label: "Ranked queue" }, { value: "table", label: "Table" }, { value: "leads", label: "Leads" }, { value: "forecast", label: "Forecast" }]} value={view} />
      {view === "leads" ? <ResourceView embedded resourceKey="leads" /> : null}
      {view === "board" ? (
        <>
          {dealsState.loading ? <LoadingState label="Loading deal stages" /> : null}
          {!dealsState.loading && dealsState.error ? <UnavailableState error={dealsState.error} onRetry={dealsState.reload} /> : null}
          {!dealsState.loading && !dealsState.error && !deals.length ? <EmptyState icon={Target} title="Pipeline is ready" message="Qualify a lead or create the first deal to begin forecasting." /> : null}
          {!dealsState.loading && !dealsState.error && deals.length ? (
            <div className="pipeline-board">
              {dealStages.map((stage) => {
                const stageDeals = deals.filter((deal) => dealStage(deal) === stage);
                if (!stageDeals.length && ["Lost"].includes(stage)) return null;
                return (
                  <section className="pipeline-column" key={stage}>
                    <header><span className={`stage-dot stage-${stage.toLowerCase()}`} /><strong>{stage}</strong><small>{stageDeals.length}</small></header>
                    <div>{stageDeals.map((deal) => <RecordCard config={resourceConfigs.opportunities} dense key={deal.id} record={deal} />)}</div>
                  </section>
                );
              })}
            </div>
          ) : null}
        </>
      ) : null}
      {view === "queue" ? (
        <section aria-label="Deals ranked by attention needed" className="pipeline-queue">
          {dealsState.loading ? <LoadingState label="Ranking open deals" /> : null}
          {dealsState.error ? <UnavailableState error={dealsState.error} onRetry={dealsState.reload} /> : null}
          {!dealsState.loading && !dealsState.error && !rankedDeals.length ? <EmptyState icon={Target} title="Queue is clear" message="Open deals will be ranked here by risk, inactivity and value." /> : null}
          {rankedDeals.map(({ deal, reasons }, index) => <article className="pipeline-queue-row" key={deal.id}><span className="queue-rank">{String(index + 1).padStart(2, "0")}</span><div><Link className="record-title" to={`/opportunities/${deal.id}`}>{recordName(deal, "Untitled deal")}</Link><p>{deal.account_name || "No account"} · {reasons.join(" · ") || "On track"}</p></div><Badge>{dealStage(deal)}</Badge><strong>{formatMoney(dealValue(deal))}</strong><small>{Math.round(dealProbability(deal) * 100)}%</small><Link aria-label={`Open ${recordName(deal, "deal")}`} className="button button-quiet" to={`/opportunities/${deal.id}`}>Open</Link></article>)}
        </section>
      ) : null}
      {view === "table" ? (
        <section aria-label="Deal table" className="pipeline-table" role="table">
          <div className="pipeline-table-head" role="row"><span role="columnheader">Deal</span><span role="columnheader">Stage</span><span role="columnheader">Value</span><span role="columnheader">Probability</span><span role="columnheader">Expected close</span><span role="columnheader">Next action</span></div>
          {dealsState.loading ? <LoadingState label="Loading deal table" /> : null}
          {dealsState.error ? <UnavailableState error={dealsState.error} onRetry={dealsState.reload} /> : null}
          {openDeals.map((deal) => <div className="pipeline-table-row" key={deal.id} role="row"><span role="cell"><Link className="record-title" to={`/opportunities/${deal.id}`}>{recordName(deal, "Untitled deal")}</Link><small>{deal.account_name}</small></span><span role="cell"><Badge>{dealStage(deal)}</Badge></span><strong role="cell">{formatMoney(dealValue(deal))}</strong><span role="cell">{Math.round(dealProbability(deal) * 100)}%</span><span role="cell">{formatDate(deal.expected_close_date || deal.expected_close_at)}</span><span role="cell">{deal.next_action || "Needs next action"}</span></div>)}
          {!dealsState.loading && !dealsState.error && !openDeals.length ? <EmptyState icon={Target} title="No open deals" message="Qualified opportunities will appear here." /> : null}
        </section>
      ) : null}
      {view === "forecast" ? (
        <section className="forecast-view">
          {dealsState.error ? <UnavailableState error={dealsState.error} onRetry={dealsState.reload} /> : null}
          <div className="metric-strip"><Metric label="Open value" value={formatMoney(totalMinor)} note={`${openDeals.length} opportunities`} /><Metric label="Weighted" value={formatMoney(weightedMinor)} note="Probability adjusted" tone="cash" /><Metric label="Closing this month" value={compactNumber(openDeals.filter((deal) => String(deal.expected_close_date || deal.expected_close_at || "").slice(0, 7) === new Date().toISOString().slice(0, 7)).length)} note="Current close dates" /><Metric label="Needs next action" value={compactNumber(openDeals.filter((deal) => !deal.next_action).length)} note="Incomplete records" tone="urgent" /></div>
          <div className="panel forecast-panel"><header className="panel-heading"><div><span className="section-kicker">Forecast</span><h2>Stage confidence</h2></div><BarChart3 aria-hidden="true" size={18} /></header>{dealStages.slice(0, 7).map((stage, index) => { const stageDeals = openDeals.filter((deal) => dealStage(deal) === stage); const count = stageDeals.length; const width = openDeals.length ? count / openDeals.length * 100 : 0; const stageWeighted = stageDeals.reduce((sum, deal) => sum + dealValue(deal) * dealProbability(deal), 0); return <div className="forecast-row" key={stage}><span>{stage}</span><div aria-label={`${stage}: ${count} deals, ${formatMoney(stageWeighted)} weighted`} aria-valuemax="100" aria-valuemin="0" aria-valuenow={Math.round(width)} role="progressbar"><i style={{ width: `${width}%` }} /></div><strong>{count}</strong><small>{Math.round(stageDeals.reduce((sum, deal) => sum + dealProbability(deal), 0) / Math.max(count, 1) * 100)}%</small></div>; })}</div>
        </section>
      ) : null}
      {view !== "leads" ? <PageControls hasNext={dealsState.hasNext} hasPrevious={dealsState.hasPrevious} label="Pipeline records" nextPage={dealsState.nextPage} page={dealsState.page} previousPage={dealsState.previousPage} /> : null}
    </div>
  );
}

export function CommercialView() {
  const [params, setParams] = useSearchParams();
  const view = ["proposals", "contracts", "catalog"].includes(params.get("view")) ? params.get("view") : "proposals";
  const [createOpen, setCreateOpen] = useState(false);
  const navigate = useNavigate();
  useDocumentTitle("Proposals & contracts");
  return (
    <div>
      <PageIntro actions={view !== "catalog" ? <button className="button button-primary" onClick={() => setCreateOpen(true)} type="button"><Plus aria-hidden="true" size={16} /> New {view === "contracts" ? "contract" : "proposal"}</button> : null} description="Versioned scope and terms from first offer to signed copy." eyebrow="Commercial" signal="Controlled documents" title="Proposals & contracts" />
      <Segmented label="Commercial document type" onChange={(next) => setParams({ view: next })} options={[{ value: "proposals", label: "Proposals" }, { value: "contracts", label: "Contracts" }, { value: "catalog", label: "Catalog" }]} value={view} />
      {view === "catalog" ? <CatalogWorkspace /> : <ResourceView embedded resourceKey={view} />}
      {view !== "catalog" ? <CommercialCreateDialog onClose={() => setCreateOpen(false)} onCreated={(record) => navigate("/" + view + "/" + record.id)} open={createOpen} type={view} /> : null}
    </div>
  );
}

export function BillingView() {
  const [params, setParams] = useSearchParams();
  const view = ["invoices", "payments", "credit-notes"].includes(params.get("view")) ? params.get("view") : "invoices";
  const [createOpen, setCreateOpen] = useState(false);
  const navigate = useNavigate();
  useDocumentTitle("Billing");
  return (
    <div>
      <PageIntro actions={view !== "credit-notes" ? <button className="button button-primary" onClick={() => setCreateOpen(true)} type="button"><Plus aria-hidden="true" size={16} /> New {view === "payments" ? "payment" : "invoice"}</button> : null} description="Issue locally, collect through Stripe, reconcile without losing the audit trail." eyebrow="Commercial" signal="GBP · Local ledger" title="Billing" />
      <Segmented label="Billing view" onChange={(next) => setParams({ view: next })} options={[{ value: "invoices", label: "Invoices" }, { value: "payments", label: "Payments" }, { value: "credit-notes", label: "Credit notes" }]} value={view} />
      <ResourceView embedded resourceKey={view} />
      {view !== "credit-notes" ? <CommercialCreateDialog onClose={() => setCreateOpen(false)} onCreated={(record) => navigate("/" + view + "/" + record.id)} open={createOpen} type={view} /> : null}
    </div>
  );
}

export function ProjectsView() {
  const [createOpen, setCreateOpen] = useState(false);
  const [listVersion, setListVersion] = useState(0);
  const navigate = useNavigate();
  return (
    <>
      <ResourceView actions={<button className="button button-primary" onClick={() => setCreateOpen(true)} type="button"><Plus aria-hidden="true" size={16} /> New project</button>} key={listVersion} resourceKey="projects" />
      <ProjectCreateDialog onClose={() => setCreateOpen(false)} onCreated={(project) => { setListVersion((version) => version + 1); navigate(`/projects/${project.id}`); }} open={createOpen} />
    </>
  );
}

export function ClientSuccessView() {
  const [createOpen, setCreateOpen] = useState(false);
  const [listVersion, setListVersion] = useState(0);
  const navigate = useNavigate();
  return (
    <>
      <ResourceView actions={<button className="button button-primary" onClick={() => setCreateOpen(true)} type="button"><Plus aria-hidden="true" size={16} /> New success plan</button>} key={listVersion} resourceKey="client-success" />
      <ClientSuccessDialog onClose={() => setCreateOpen(false)} onSaved={(record) => { setListVersion((version) => version + 1); navigate(`/client-success/${record.account_id}`); }} open={createOpen} />
    </>
  );
}

export function SequencesView() {
  const [createOpen, setCreateOpen] = useState(false);
  const [listVersion, setListVersion] = useState(0);
  const navigate = useNavigate();
  return (
    <>
      <ResourceView
        actions={<button className="button button-primary" onClick={() => setCreateOpen(true)} type="button"><Plus aria-hidden="true" size={16} /> New sequence</button>}
        key={listVersion}
        resourceKey="sequences"
      />
      <SequenceDialog onClose={() => setCreateOpen(false)} onSaved={(sequence) => { setListVersion((version) => version + 1); navigate("/sequences/" + sequence.id); }} open={createOpen} />
    </>
  );
}

export function FilesView() {
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "templates" ? "templates" : "files";
  const [listVersion, setListVersion] = useState(0);
  const navigate = useNavigate();
  useDocumentTitle("Files");
  return <div>
    <PageIntro actions={view === "files" ? <DocumentCreateControl onCreated={(file) => { setListVersion((version) => version + 1); navigate("/files/" + file.id); }} /> : null} description="Drive-backed working files, reusable templates, and checksummed issued versions." eyebrow="System" signal={view === "files" ? "Local metadata Â· Drive sync" : "Reusable sources"} title="Files" />
    <Segmented label="Files view" onChange={(next) => setParams({ view: next })} options={[{ value: "files", label: "Files" }, { value: "templates", label: "Templates" }]} value={view} />
    {view === "files" ? <ResourceView embedded key={listVersion} resourceKey="files" /> : <DocumentTemplateManager />}
  </div>;
}

const reportCards = [
  ["pipeline", "Pipeline & forecast", "Stage confidence, weighted value and close dates.", Target],
  ["finance", "Revenue & collections", "Issued, collected, outstanding and receivables age.", CircleDollarSign],
  ["finance", "VAT summary", "Output, input and net tax position from posted records.", Landmark],
  ["delivery", "Delivery profitability", "Revenue, expenses, time and margin by project.", FileBarChart],
  ["renewals", "Client health & renewals", "Risk signals and upcoming commercial renewals.", ShieldCheck],
  ["ledger", "Ledger & reconciliation", "Balanced journals with source links and account postings.", RefreshCw],
];

function ReportState({ state, label, emptyTitle, emptyMessage, children }) {
  if (state.loading) return <LoadingState label={label} />;
  if (state.error) return <UnavailableState error={state.error} onRetry={state.reload} />;
  if (!state.data?.length) return <EmptyState icon={FileBarChart} title={emptyTitle} message={emptyMessage} />;
  return children;
}

function ReportsOverview() {
  const { data, loading, error, reload } = useResource("reports", { list: false });
  const finance = data?.finance || {};
  const projects = Array.isArray(data?.projects) ? data.projects : [];
  const renewals = Array.isArray(data?.renewals) ? data.renewals : [];
  const ready = !loading && !error && Boolean(data?.finance);
  const margin = projects.reduce((total, project) => total + Number(project.margin_pence || 0), 0);
  return (
    <>
      {loading ? <LoadingState label="Building management reports" /> : null}
      {error ? <UnavailableState compact error={error} onRetry={reload} /> : null}
      {ready ? <section aria-label="Report summary" className="metric-strip">
        <Metric label="Invoiced" value={formatMoney(finance.invoiced_pence, finance.currency)} note="Issued invoices" />
        <Metric label="Collected" value={formatMoney(finance.collected_pence, finance.currency)} note="Recorded payments" tone="cash" />
        <Metric label="Outstanding" value={formatMoney(finance.outstanding_pence, finance.currency)} note="Open receivables" tone="urgent" />
        <Metric label="Delivery margin" value={formatMoney(margin)} note={`${projects.length} active project${projects.length === 1 ? "" : "s"}`} />
      </section> : null}
      <div className="report-grid">
        {reportCards.map(([view, title, description, Icon], index) => {
          const facts = [
            `${formatMoney(finance.invoiced_pence, finance.currency)} issued`,
            `${formatMoney(finance.outstanding_pence, finance.currency)} currently outstanding`,
            `${formatMoney(finance.vat?.net_due_pence, finance.currency)} net VAT position`,
            `${formatMoney(margin)} delivery margin`,
            renewals[0]?.renewal_on ? `Next renewal ${formatDate(renewals[0].renewal_on)}` : "No renewal dates currently due",
            "Trace local financial source records",
          ];
          return <article className="report-card" key={title}><span><Icon aria-hidden="true" size={20} /></span><div><h2>{title}</h2><p>{description}</p><strong className="report-card-value">{ready ? facts[index] : "Report data will appear here after refresh."}</strong><Link className="report-card-link" to={`/reports?view=${view}`}>Drill into report <ArrowUpRight aria-hidden="true" size={13} /></Link></div></article>;
        })}
      </div>
    </>
  );
}

function FinanceReport() {
  const state = useResource("reports/finance", { list: false });
  const finance = state.data || {};
  const aging = finance.receivables_aging_pence || {};
  return <div className="report-detail-stack">
    {state.loading ? <LoadingState label="Building finance report" /> : null}
    {state.error ? <UnavailableState error={state.error} onRetry={state.reload} /> : null}
    {!state.loading && !state.error ? <>
      <section aria-label="Finance metrics" className="metric-strip">
        <Metric label="Invoiced" value={formatMoney(finance.invoiced_pence, finance.currency)} note="Non-draft invoices" />
        <Metric label="Collected" value={formatMoney(finance.collected_pence, finance.currency)} note="Confirmed local payments" tone="cash" />
        <Metric label="Credited" value={formatMoney(finance.credited_pence, finance.currency)} note="Issued credit notes" />
        <Metric label="Outstanding" value={formatMoney(finance.outstanding_pence, finance.currency)} note="Open receivables" tone="urgent" />
      </section>
      <div className="report-detail-grid">
        <section className="report-detail-panel"><header><span className="section-kicker">Receivables</span><h2>Aging</h2></header><dl>{[["Current", "current"], ["1–30 days", "1_30"], ["31–60 days", "31_60"], ["61–90 days", "61_90"], ["90+ days", "90_plus"]].map(([label, key]) => <div key={key}><dt>{label}</dt><dd>{formatMoney(aging[key], finance.currency)}</dd></div>)}</dl><Link className="report-card-link" to="/billing">Open billing <ArrowUpRight aria-hidden="true" size={13} /></Link></section>
        <section className="report-detail-panel"><header><span className="section-kicker">Tax</span><h2>VAT position</h2></header><dl><div><dt>Output VAT</dt><dd>{formatMoney(finance.vat?.output_pence, finance.currency)}</dd></div><div><dt>Input VAT</dt><dd>{formatMoney(finance.vat?.input_pence, finance.currency)}</dd></div><div><dt>Net due</dt><dd>{formatMoney(finance.vat?.net_due_pence, finance.currency)}</dd></div></dl><Link className="report-card-link" to="/settings">Review VAT settings <ArrowUpRight aria-hidden="true" size={13} /></Link></section>
      </div>
    </> : null}
  </div>;
}

function DeliveryReport() {
  const state = useResource("reports/projects");
  return <ReportState state={state} label="Building delivery report" emptyTitle="No project delivery data" emptyMessage="Project revenue, expenses and time appear after work is linked to a project.">
    <section className="report-table" role="table" aria-label="Project delivery profitability">
      <div className="report-table-head" role="row"><span role="columnheader">Project</span><span role="columnheader">Status</span><span role="columnheader">Revenue</span><span role="columnheader">Expenses</span><span role="columnheader">Margin</span><span role="columnheader">Time</span></div>
      {state.data.map((project) => <div className="report-table-row" role="row" key={project.id}><span role="cell"><Link to={`/projects/${project.id}`}>{project.name}</Link></span><span role="cell"><Badge>{project.status}</Badge></span><span role="cell">{formatMoney(project.revenue_pence, project.currency)}</span><span role="cell">{formatMoney(project.expense_pence, project.currency)}</span><strong role="cell">{formatMoney(project.margin_pence, project.currency)}</strong><span role="cell">{Math.round(Number(project.time_minutes || 0) / 60 * 10) / 10}h</span></div>)}
    </section>
  </ReportState>;
}

function RenewalsReport() {
  const state = useResource("reports/renewals");
  return <ReportState state={state} label="Building renewals report" emptyTitle="No renewals due" emptyMessage="No client renewal falls inside the next 90 days.">
    <section className="report-list" aria-label="Upcoming client renewals">
      {state.data.map((renewal) => <article className="control-row" key={renewal.id}><div><span><Link className="record-title" to={`/client-success/${renewal.account_id}`}>{renewal.account_name || `Account ${renewal.account_id}`}</Link><Badge>{renewal.computed_health || renewal.manual_health || "Unrated"}</Badge></span><p>Renewal {formatDate(renewal.renewal_on)} · {renewal.health_reasons?.join(" · ") || "No current risk signal"}</p></div><Link className="button button-quiet" to={`/client-success/${renewal.account_id}`}>Review</Link></article>)}
    </section>
  </ReportState>;
}

function PipelineReport() {
  const state = useResource("opportunities", { query: { limit: 50 }, pageSize: 50 });
  const open = state.data.filter((deal) => !["Won", "Lost"].includes(String(deal.status || dealStage(deal))));
  return <div className="report-detail-stack">
    {state.loading ? <LoadingState label="Building pipeline report" /> : null}
    {state.error ? <UnavailableState error={state.error} onRetry={state.reload} /> : null}
    {!state.loading && !state.error && !open.length ? <EmptyState icon={Target} title="No open pipeline" message="Open opportunities will be grouped here by stage." /> : null}
    {!state.loading && !state.error && open.length ? <section className="report-detail-panel"><header><span className="section-kicker">Pipeline</span><h2>Stage confidence · page {state.page}</h2></header><div className="report-stage-list">{dealStages.slice(0, 7).map((stage) => { const matches = open.filter((deal) => dealStage(deal) === stage); const value = matches.reduce((sum, deal) => sum + Number(deal.value_pence ?? deal.value_minor ?? 0), 0); return <div key={stage}><span>{stage}</span><strong>{matches.length}</strong><small>{formatMoney(value)}</small></div>; })}</div><Link className="report-card-link" to="/pipeline">Open pipeline workspace <ArrowUpRight aria-hidden="true" size={13} /></Link></section> : null}
    <PageControls hasNext={state.hasNext} hasPrevious={state.hasPrevious} label="Pipeline report" nextPage={state.nextPage} page={state.page} previousPage={state.previousPage} />
  </div>;
}

function LedgerReport() {
  const [query, setQuery] = useState("");
  const [sourceType, setSourceType] = useState("");
  const state = useResource("ledger", { query: { q: query, source_type: sourceType, limit: 25 }, pageSize: 25 });
  const sourceRoutes = { invoice: "invoices", payment: "payments", credit_note: "credit-notes", expense: "expenses" };
  return <div className="report-detail-stack">
    <div className="list-toolbar">
      <label className="search-field"><span className="sr-only">Search ledger</span><input onChange={(event) => setQuery(event.target.value)} placeholder="Search descriptions or source IDs" type="search" value={query} /></label>
      <label className="select-field"><span className="sr-only">Filter ledger source</span><select onChange={(event) => setSourceType(event.target.value)} value={sourceType}><option value="">All source types</option>{["invoice", "payment", "credit_note", "expense", "refund"].map((source) => <option key={source} value={source}>{titleCase(source)}</option>)}</select></label>
      <span className="result-count">Page {state.page}</span>
    </div>
    {state.loading ? <LoadingState label="Loading ledger journals" /> : null}
    {state.error ? <UnavailableState error={state.error} onRetry={state.reload} /> : null}
    {!state.loading && !state.error && !state.data.length ? <EmptyState icon={Landmark} title="No matching journals" message="Posted invoices, payments, credits and expenses create balanced journals here." /> : null}
    <section className="ledger-list" aria-label="Ledger journals">
      {state.data.map((journal) => {
        const route = sourceRoutes[journal.linked_type] || Object.entries(sourceRoutes).find(([key]) => String(journal.source_type).startsWith(key))?.[1];
        const linkedId = journal.linked_id || journal.source_id;
        return <article className="ledger-journal" key={journal.id}><header><div><strong>{journal.description}</strong><span>{titleCase(journal.source_type)} #{journal.source_id}</span></div><div className="ledger-badges">{journal.payment_status ? <Badge>{journal.payment_status}</Badge> : null}<Badge tone={journal.debit_pence === journal.credit_pence ? "positive" : "danger"}>{journal.debit_pence === journal.credit_pence ? "Balanced" : "Review"}</Badge></div></header><div>{journal.lines?.map((line) => <div key={line.id}><span><code>{line.account_code}</code> {line.description || "Posting"}</span><span>{formatMoney(line.debit_pence)}</span><span>{formatMoney(line.credit_pence)}</span></div>)}</div><footer><time>{formatDate(journal.posted_at, { withTime: true })}</time>{route ? <Link to={`/${route}/${linkedId}`}>Open source <ArrowUpRight aria-hidden="true" size={12} /></Link> : null}</footer></article>;
      })}
    </section>
    <PageControls hasNext={state.hasNext} hasPrevious={state.hasPrevious} label="Ledger" nextPage={state.nextPage} page={state.page} previousPage={state.previousPage} />
  </div>;
}

export function ReportsView() {
  const [params, setParams] = useSearchParams();
  const view = ["overview", "finance", "delivery", "renewals", "pipeline", "ledger"].includes(params.get("view")) ? params.get("view") : "overview";
  useDocumentTitle("Reports");
  return (
    <div>
      <PageIntro actions={<ReportsCsvExportControl />} description="Curated management views built from the local source of truth." eyebrow="Control" signal={view === "overview" ? "Live local reports" : titleCase(view)} title="Reports" />
      <Segmented label="Report view" onChange={(next) => setParams({ view: next })} options={[{ value: "overview", label: "Overview" }, { value: "finance", label: "Finance & VAT" }, { value: "delivery", label: "Delivery" }, { value: "renewals", label: "Renewals" }, { value: "pipeline", label: "Pipeline" }, { value: "ledger", label: "Ledger" }]} value={view} />
      {view === "overview" ? <ReportsOverview /> : null}
      {view === "finance" ? <FinanceReport /> : null}
      {view === "delivery" ? <DeliveryReport /> : null}
      {view === "renewals" ? <RenewalsReport /> : null}
      {view === "pipeline" ? <PipelineReport /> : null}
      {view === "ledger" ? <LedgerReport /> : null}
    </div>
  );
}

function IntegrationCard({
  provider,
  icon: Icon,
  name,
  description,
  configured,
  connected = false,
  detail,
  onChanged,
  secondaryAction,
  secondaryActions = [],
}) {
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState("");
  const [feedback, setFeedback] = useState({ tone: "", message: "" });
  const inputId = `credential-${provider}`;

  async function save(event) {
    event.preventDefault();
    if (!secret.trim()) return;
    setBusy("save");
    setFeedback({ tone: "", message: "" });
    try {
      await api.post(`integrations/credentials/${provider}`, { secret });
      setSecret("");
      setFeedback({ tone: "positive", message: "Saved to Windows Credential Manager." });
      onChanged?.();
    } catch (error) {
      setFeedback({ tone: "error", message: error.message });
    } finally {
      setBusy("");
    }
  }

  async function remove() {
    if (!window.confirm(`Remove the saved ${name} credential?`)) return;
    setBusy("remove");
    setFeedback({ tone: "", message: "" });
    try {
      await api.remove(`integrations/credentials/${provider}`);
      setSecret("");
      setFeedback({ tone: "positive", message: "Credential removed." });
      onChanged?.();
    } catch (error) {
      setFeedback({ tone: "error", message: error.message });
    } finally {
      setBusy("");
    }
  }

  async function runSecondary(action, index) {
    setBusy(`secondary-${index}`);
    setFeedback({ tone: "", message: "" });
    try {
      await action.onClick();
      if (action.successMessage) setFeedback({ tone: "positive", message: action.successMessage });
    } catch (error) {
      setFeedback({ tone: "error", message: error.message });
    } finally {
      setBusy("");
    }
  }

  const badge = connected ? "Connected" : configured ? "Ready" : "Not configured";
  const actions = [...secondaryActions, secondaryAction].filter(Boolean);
  return (
    <article className="integration-card">
      <span className="integration-icon"><Icon aria-hidden="true" size={21} /></span>
      <div><h3>{name}</h3><p>{description}</p>{detail ? <small>{detail}</small> : null}</div>
      <Badge tone={configured || connected ? "positive" : "warning"}>{badge}</Badge>
      <form className="credential-form" onSubmit={save}>
        <label htmlFor={inputId}>{configured ? "Replace saved credential" : "Add credential"}</label>
        <div className="credential-input-row">
          <input
            autoComplete="new-password"
            id={inputId}
            onChange={(event) => setSecret(event.target.value)}
            placeholder={configured ? "Stored securely — enter a replacement" : "Paste credential"}
            type="password"
            value={secret}
          />
          <button className="button button-primary" disabled={Boolean(busy) || !secret.trim()} type="submit">{busy === "save" ? "Saving…" : "Save"}</button>
        </div>
        <div className="credential-actions">
          <span>Values are never displayed, written to SQLite or included in backups.</span>
          <div>
            {actions.map((action, index) => <button className="text-button" disabled={Boolean(busy) || action.disabled} key={action.label} onClick={() => runSecondary(action, index)} type="button">{busy === `secondary-${index}` ? "Working…" : action.label}</button>)}
            {configured ? <button className="text-button credential-remove" disabled={Boolean(busy)} onClick={remove} type="button">{busy === "remove" ? "Removing…" : "Remove"}</button> : null}
          </div>
        </div>
        {feedback.message ? <p aria-live="polite" className={`credential-feedback ${feedback.tone}`}>{feedback.message}</p> : null}
      </form>
    </article>
  );
}

export function SettingsView() {
  const businessState = useResource("settings/business", { list: false });
  const credentialState = useResource("integrations/credentials", { list: false });
  const googleState = useResource("integrations/google", { list: false });
  const stripeState = useResource("integrations/stripe", { list: false });
  const [oauthError, setOauthError] = useState("");
  const [systemFeedback, setSystemFeedback] = useState("");
  useDocumentTitle("Settings");
  const settings = businessState.data || {};
  const credentials = Object.fromEntries((credentialState.data?.items || []).map((item) => [item.provider, item]));
  const google = googleState.data || {};
  const stripe = stripeState.data || {};
  const loading = businessState.loading || credentialState.loading || googleState.loading || stripeState.loading;
  const error = businessState.error || credentialState.error || googleState.error || stripeState.error;
  const oauthConfig = credentialState.data?.google_oauth || {};
  const oauthReady = oauthConfig.client_id_configured && (oauthConfig.random_loopback || oauthConfig.redirect_uri_configured);
  const requiredGoogleScopes = ["gmail.modify", "calendar.events", "drive.file"];
  const missingGoogleScopes = google.status === "connected"
    ? requiredGoogleScopes.filter((scope) => !(google.scopes || []).some((granted) => String(granted).endsWith("/" + scope)))
    : [];

  function reload() {
    businessState.reload();
    credentialState.reload();
    googleState.reload();
    stripeState.reload();
  }

  function reloadConnections() {
    credentialState.reload();
    googleState.reload();
    stripeState.reload();
  }

  async function connectGoogle() {
    setOauthError("");
    try {
      const result = await api.post("integrations/google/oauth/start", {});
      window.location.assign(result.authorization_url);
    } catch (connectError) {
      setOauthError(connectError.message);
    }
  }

  async function reconcileProvider(provider) {
    const queued = await api.post(`integrations/${provider}/reconcile`, {});
    setSystemFeedback(`${titleCase(provider)} reconciliation job ${queued.job_id} queued.`);
    if (provider === "google") googleState.reload();
    if (provider === "stripe") stripeState.reload();
  }

  return (
    <div>
      <PageIntro description="Business defaults, secure connections, backups and local system health." eyebrow="System" signal="Single operator" title="Settings" />
      {loading ? <LoadingState label="Checking local settings" /> : null}
      {error ? <UnavailableState compact error={error} onRetry={reload} /> : null}
      {systemFeedback ? <p aria-live="polite" className="action-feedback positive">{systemFeedback}</p> : null}
      <section className="settings-section">
        <header><span className="section-kicker">Connections</span><h2>Workspace services</h2><p>Disconnected services never block local CRM work.</p></header>
        {credentialState.data?.error ? <div className="form-error"><strong>Credential Manager unavailable</strong><span>{credentialState.data.error}</span></div> : null}
        {oauthError ? <div className="form-error"><strong>Google connection could not start</strong><span>{oauthError}</span></div> : null}
        <div className="integration-grid">
          <IntegrationCard
            configured={Boolean(credentials.google?.configured)}
            connected={google.status === "connected"}
            description="Gmail, Calendar, Drive and Docs through desktop OAuth."
            detail={google.error || (missingGoogleScopes.length ? `Reconnect to grant: ${missingGoogleScopes.join(", ")}.` : oauthReady ? "Desktop OAuth app is ready with a one-time local callback." : "Add GOOGLE_CLIENT_ID to the local configuration.")}
            icon={Cloud}
            name="Google Workspace"
            onChanged={reloadConnections}
            provider="google"
            secondaryActions={[
              { label: google.status === "connected" ? "Reconnect Workspace" : "Connect Workspace", onClick: connectGoogle, disabled: !credentials.google?.configured || !oauthReady },
              { label: "Reconcile now", onClick: () => reconcileProvider("google"), disabled: google.status !== "connected" },
            ]}
          />
          <IntegrationCard configured={Boolean(credentials.stripe?.configured)} connected={Boolean(stripe.configured)} description="One-use payment links and reconciliation. The CRM remains invoice authority." detail={stripe.error || "Use a restricted test or live secret key for this installation."} icon={CircleDollarSign} name="Stripe" onChanged={reloadConnections} provider="stripe" secondaryActions={[{ label: "Reconcile now", onClick: () => reconcileProvider("stripe"), disabled: !stripe.configured }]} />
          <IntegrationCard configured={Boolean(credentials.tavily?.configured)} description="Tender discovery, source evidence and contact research." detail="Used only when a discovery or enrichment job runs." icon={Target} name="Tavily" onChanged={reloadConnections} provider="tavily" />
          <IntegrationCard configured={Boolean(credentials.gemini?.configured)} description="Optional extraction, scoring, summaries and operator-approved drafts." detail="AI never sends messages or posts financial records." icon={Sparkles} name="Gemini" onChanged={reloadConnections} provider="gemini" />
        </div>
      </section>
      <section className="settings-section settings-grid">
        <article className="settings-card"><span><Landmark aria-hidden="true" size={18} /> Business & VAT</span><h3>{settings.trading_name || settings.legal_name || "Business profile incomplete"}</h3><p>{settings.currency || "GBP"} · {settings.timezone || "Europe/London"} · VAT {settings.vat_registered ? "enabled" : "disabled until configured"}</p>{settings.version ? <BusinessProfileControl onSaved={() => { businessState.reload(); setSystemFeedback("Business and VAT profile saved."); }} profile={settings} /> : <button className="button button-quiet" disabled type="button">Loading profile…</button>}</article>
        <article className="settings-card"><span><ShieldCheck aria-hidden="true" size={18} /> Data protection</span><h3>Local database</h3><p>WAL, foreign keys, strict sessions and durable jobs protect this workspace.</p><IntegrityCheckControl onChecked={(result) => setSystemFeedback("Database integrity check: " + result.database + ".")} /></article>
        <article className="settings-card"><span><RefreshCw aria-hidden="true" size={18} /> Backups</span><h3>Verified local snapshots</h3><p>Create verified snapshots or stage a guarded recovery for the next restart. Secrets remain excluded.</p><div className="settings-card-actions"><BackupCreateControl onQueued={(job) => setSystemFeedback("Backup job " + job.job_id + " queued.")} /><BackupRestoreControl onQueued={(job) => setSystemFeedback("Restore job " + job.job_id + " queued. Restart CRM Workspace after it completes.")} /></div></article>
        <article className="settings-card"><span><FileBarChart aria-hidden="true" size={18} /> Data transfer</span><h3>Preview before import</h3><p>Map CSV columns and inspect validation, duplicates and row errors before a separate confirmed commit.</p><CsvImportControl onImported={(result) => setSystemFeedback(result.created_count + " records imported; " + result.duplicate_count + " duplicates skipped.")} /></article>
        <article className="settings-card"><span><Workflow aria-hidden="true" size={18} /> Job recovery</span><h3>Visible, deliberate retries</h3><p>Inspect failures and reconcile unknown Google or Stripe outcomes before the worker retries anything.</p><JobRecoveryControl /></article>
      </section>
      <CustomFieldManager />
    </div>
  );
}

export function AutomationsView() {
  const rulesState = useResource("automations");
  const executionsState = useResource("automations/executions");
  const [createOpen, setCreateOpen] = useState(false);
  useDocumentTitle("Automations");
  return (
    <div>
      <PageIntro actions={<button className="button button-primary" onClick={() => setCreateOpen(true)} type="button"><Plus aria-hidden="true" size={16} /> New rule</button>} description="Allowlisted trigger-condition-action rules that begin disabled and in dry-run mode." eyebrow="Control" signal={rulesState.data.length + " rules"} title="Automations" />
      {rulesState.loading ? <LoadingState label="Loading automation rules" /> : null}
      {rulesState.error ? <UnavailableState error={rulesState.error} onRetry={rulesState.reload} /> : null}
      {!rulesState.loading && !rulesState.error && !rulesState.data.length ? <EmptyState icon={Workflow} title="No automation rules yet" message="Create a dry-run rule, preview it against sample data, then enable it deliberately." action={<button className="button button-primary" onClick={() => setCreateOpen(true)} type="button">Create first rule</button>} /> : null}
      <div className="automation-list">
        {rulesState.data.map((rule) => (
          <article className="automation-row" key={rule.id}>
            <div><span><strong>{rule.name}</strong><Badge tone={rule.enabled ? "positive" : "warning"}>{rule.enabled ? "Enabled" : "Disabled"}</Badge>{rule.dry_run ? <Badge tone="info">Dry run</Badge> : null}</span><p>{rule.trigger_name} · version {rule.version}</p></div>
            <div className="record-workflow-actions"><AutomationWorkflowActions onChanged={rulesState.reload} rule={rule} /></div>
          </article>
        ))}
      </div>
      <section className="settings-section">
        <header><span className="section-kicker">Execution log</span><h2>Recent rule outcomes</h2><p>Dry-run matches and live actions retain their correlation and failure result locally.</p></header>
        {executionsState.loading ? <LoadingState label="Loading automation history" /> : null}
        {executionsState.error ? <UnavailableState compact error={executionsState.error} onRetry={executionsState.reload} /> : null}
        {!executionsState.loading && !executionsState.error && !executionsState.data.length ? <EmptyState icon={Workflow} title="No executions yet" message="Enabled rules will write their dry-run or live outcomes here when lifecycle events occur." /> : null}
        <div className="automation-list">
          {executionsState.data.slice(0, 20).map((execution) => <article className="automation-row" key={execution.id}><div><span><strong>{execution.trigger_name}</strong><Badge tone={execution.outcome === "succeeded" || execution.outcome === "matched" ? "positive" : execution.outcome === "failed" || execution.outcome === "cycle_blocked" ? "urgent" : "info"}>{titleCase(execution.outcome)}</Badge><Badge tone="info">{titleCase(execution.mode)}</Badge></span><p>{execution.record_key} · {formatDate(execution.created_at)}</p>{execution.error ? <small className="automation-error">{execution.error}</small> : null}</div></article>)}
        </div>
      </section>
      <AutomationDialog onClose={() => setCreateOpen(false)} onSaved={rulesState.reload} open={createOpen} />
    </div>
  );
}
