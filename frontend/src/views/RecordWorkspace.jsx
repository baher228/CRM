import React, { useId, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  CalendarClock,
  CheckCircle2,
  Clock3,
  CreditCard,
  ExternalLink,
  FileCheck2,
  Mail,
  Plus,
  ReceiptText,
  RotateCcw,
  Send,
  Settings2,
  UserRound,
} from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { Avatar, Badge, EmptyState, LoadingState, RecordCard, SignalLine, UnavailableState, displayField } from "../components/common";
import { RecordManagementDialog } from "../components/DataControls";
import { CalendarEventDialog, ClientSuccessDialog, CreditNoteDialog, EmailComposer, WorkflowDialog } from "../components/WorkflowDialogs";
import { SequenceEnrollmentsPanel, SequenceStepControl, SequenceWorkflowActions } from "../components/SequenceAutomationWorkflows";
import { DocumentSyncControl, DocumentVersionsPanel } from "../components/SystemControlDialogs";
import { useDocumentTitle, useResource } from "../hooks";
import { poundsToMinor } from "../utils/business";
import { formatDate, recordName, titleCase } from "../utils/format";
import { resourceConfigs } from "../workspace";

const tabs = ["overview", "relationships", "delivery", "commercial", "activity"];
const entityTypes = {
  accounts: "account",
  contacts: "contact",
  leads: "lead",
  tenders: "tender",
  opportunities: "opportunity",
  projects: "project",
  proposals: "proposal",
  contracts: "contract",
  invoices: "invoice",
  "credit-notes": "credit_note",
  payments: "payment",
  "client-success": "client_success",
  sequences: "sequence",
  tasks: "task",
};

function relatedRows(record, keys) {
  return keys.flatMap((key) => Array.isArray(record?.[key]) ? record[key] : []);
}

function RelatedSection({ title, rows, configKey }) {
  const config = resourceConfigs[configKey];
  if (!config) return null;
  return (
    <section className="record-section">
      <header><h2>{title}</h2><span>{rows.length}</span></header>
      {rows.length ? <div className="related-grid">{rows.slice(0, 6).map((row) => <RecordCard config={config} dense key={row.id} record={row} />)}</div> : <p className="section-empty">No linked {title.toLowerCase()}.</p>}
    </section>
  );
}

function localNow() {
  const date = new Date();
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function recordStatus(resourceKey, record) {
  if (resourceKey === "tenders") return record.triage_status || record.status;
  return record.status || record.lifecycle_status || record.computed_health || record.health || record.health_status;
}

export function RecordWorkspace({ resourceKey }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const config = resourceConfigs[resourceKey];
  const { data: record, loading, error, reload } = useResource(config.endpoint + "/" + id, { list: false });
  const { data: stages } = useResource("pipeline/stages");
  const [tab, setTab] = useState("overview");
  const [dialog, setDialog] = useState("");
  const [form, setForm] = useState({});
  const [emailOpen, setEmailOpen] = useState(false);
  const [calendarOpen, setCalendarOpen] = useState(false);
  const [creditOpen, setCreditOpen] = useState(false);
  const [clientSuccessOpen, setClientSuccessOpen] = useState(false);
  const [managementOpen, setManagementOpen] = useState(false);
  const [sequenceEnrollmentVersion, setSequenceEnrollmentVersion] = useState(0);
  const [actionBusy, setActionBusy] = useState("");
  const [feedback, setFeedback] = useState(null);
  const tabId = useId().replace(/:/g, "");
  const tabRefs = useRef([]);
  const name = recordName(record || {}, "Loading " + config.singular);
  useDocumentTitle(record ? name : config.title);

  const related = useMemo(() => ({
    contacts: relatedRows(record, ["contacts", "people"]),
    opportunities: relatedRows(record, ["opportunities", "deals"]),
    tenders: relatedRows(record, ["tenders"]),
    projects: relatedRows(record, ["projects"]),
    milestones: relatedRows(record, ["milestones"]),
    timeEntries: relatedRows(record, ["time_entries", "timeEntries"]),
    expenses: relatedRows(record, ["expenses"]),
    proposals: relatedRows(record, ["proposals"]),
    contracts: relatedRows(record, ["contracts"]),
    invoices: relatedRows(record, ["invoices"]),
    payments: relatedRows(record, ["payments"]),
    activity: relatedRows(record, ["activities", "activity", "timeline"]),
  }), [record]);

  if (loading) return <LoadingState label={"Loading " + config.singular} />;
  if (error) return <UnavailableState error={error} onRetry={reload} />;
  if (!record) return <EmptyState title="Record not found" message="It may have been archived or merged into another record." />;

  const status = recordStatus(resourceKey, record);
  const entityType = entityTypes[resourceKey] || resourceKey.replace(/-/g, "_").replace(/s$/, "");
  const workflowContext = { ...record, name, entity_type: entityType };

  function openDialog(kind) {
    const defaults = {
      qualify: {
        account_name: record.account_name || record.company || record.buyer_name || "",
        opportunity_title: record.title || name,
        value: ((record.estimated_value_minor || record.value_minor || 0) / 100).toFixed(2),
        next_action: record.next_action || "Arrange qualification call",
      },
      transition: { stage_id: String(record.stage_id || ""), loss_reason: "" },
      rejectProposal: { reason: "" },
      rejectTender: { reason: "" },
      snoozeTender: { reason: "", snoozed_until: new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10) },
      sign: { signed_at: localNow() },
      time: { entry_date: new Date().toISOString().slice(0, 10), minutes: "60", description: "", billable: true, hourly_rate: "" },
      expense: { expense_date: new Date().toISOString().slice(0, 10), vendor: "", description: "", net: "", tax_percent: "0", billable: false },
      projectStatus: { status: record.status || "Planned" },
      milestone: { title: "", due_on: record.due_on || "", amount: "", status: "Planned" },
      refund: {
        amount: ((Number(record.amount_pence || 0) - Number(record.refunded_pence || 0)) / 100).toFixed(2),
        reason: "",
        invoice_id: record.allocations?.[0]?.invoice_id || "",
      },
      task: { title: "Follow up: " + name, due_at: "", priority: "Medium", description: "" },
      note: { subject: "Note", body: "" },
    };
    setForm(defaults[kind] || {});
    setDialog(kind);
    setFeedback(null);
  }

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function perform(path, body, success, confirmMessage, navigateResult) {
    if (confirmMessage && !window.confirm(confirmMessage)) return;
    setActionBusy(path);
    setFeedback(null);
    try {
      const needsConfirmationHeader = Boolean(confirmMessage) || /\/(issue|void|payment-link)$/.test(path);
      const result = await api.post(path, body || {}, needsConfirmationHeader ? { headers: { "X-CRM-Confirmed": "true" } } : undefined);
      setFeedback({ tone: "positive", message: success });
      if (navigateResult) navigate(navigateResult(result));
      else await reload();
      return result;
    } catch (caught) {
      setFeedback({ tone: "error", message: caught.message });
      return null;
    } finally {
      setActionBusy("");
    }
  }

  async function submitDialog() {
    let result;
    if (dialog === "qualify") {
      const accountId = record.account_id || record.buyer_account_id;
      result = await api.post(config.endpoint + "/" + id + "/qualify", {
        account_id: accountId || undefined,
        account_name: form.account_name,
        opportunity_title: form.opportunity_title,
        value_minor: poundsToMinor(form.value),
        next_action: form.next_action,
      });
    } else if (dialog === "transition") {
      result = await api.post("opportunities/" + id + "/transition", {
        version: record.version,
        stage_id: Number(form.stage_id),
        loss_reason: form.loss_reason || "",
      });
    } else if (dialog === "rejectProposal") {
      result = await api.post("proposals/" + id + "/reject", { reason: form.reason });
    } else if (dialog === "rejectTender") {
      result = await api.post("tenders/" + id + "/reject", { version: record.version, reason: form.reason });
    } else if (dialog === "snoozeTender") {
      result = await api.post("tenders/" + id + "/snooze", { version: record.version, reason: form.reason, snoozed_until: form.snoozed_until });
    } else if (dialog === "sign") {
      result = await api.post(
        "contracts/" + id + "/sign",
        { signed_at: form.signed_at || undefined },
        { headers: { "X-CRM-Confirmed": "true" } },
      );
    } else if (dialog === "time") {
      result = await api.post("time-entries", {
        project_id: record.id,
        entry_date: form.entry_date,
        minutes: Number(form.minutes),
        description: form.description,
        billable: Boolean(form.billable),
        hourly_rate_pence: poundsToMinor(form.hourly_rate),
      });
    } else if (dialog === "expense") {
      result = await api.post("expenses", {
        project_id: record.id,
        account_id: record.account_id || undefined,
        expense_date: form.expense_date,
        vendor: form.vendor,
        description: form.description,
        net_pence: poundsToMinor(form.net),
        tax_rate_bps: Math.round(Number(form.tax_percent || 0) * 100),
        billable: Boolean(form.billable),
      });
    } else if (dialog === "projectStatus") {
      result = await api.patch("projects/" + id, { version: record.version, status: form.status });
    } else if (dialog === "milestone") {
      result = await api.post("projects/" + id + "/milestones", {
        title: form.title,
        due_on: form.due_on || undefined,
        amount_pence: poundsToMinor(form.amount),
        status: form.status,
      });
    } else if (dialog === "refund") {
      if (!window.confirm("Confirm this refund. This posts an immutable reversing journal entry.")) return;
      result = await api.post("payments/" + id + "/refund", {
        amount_pence: poundsToMinor(form.amount),
        invoice_id: form.invoice_id ? Number(form.invoice_id) : undefined,
        reason: form.reason,
      }, { headers: { "X-CRM-Confirmed": "true" } });
    } else if (dialog === "task") {
      result = await api.post("tasks", {
        entity_type: entityType,
        entity_id: record.id,
        title: form.title,
        description: form.description,
        due_at: form.due_at || undefined,
        priority: form.priority,
      });
    } else if (dialog === "note") {
      result = await api.post("activities", {
        entity_type: entityType,
        entity_id: record.id,
        kind: "note",
        subject: form.subject,
        body: form.body,
      });
    }
    if (result) {
      const completed = dialog;
      setDialog("");
      setFeedback({ tone: "positive", message: completed === "qualify" ? "Qualified and linked to a deal." : "Action completed." });
      if (completed === "qualify") navigate("/opportunities/" + result.id);
      else await reload();
    }
  }

  const isBusy = Boolean(actionBusy);
  const outstanding = Math.max(Number(record.total_pence || 0) - Number(record.paid_pence || 0) - Number(record.credited_pence || 0), 0);
  const selectedStage = stages.find((stage) => String(stage.id) === String(form.stage_id));

  function handleTabKeyDown(event, index) {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault();
    setTab(tabs[next]);
    tabRefs.current[next]?.focus();
  }

  return (
    <div className="record-workspace">
      <Link className="back-link" to={config.path}><ArrowLeft aria-hidden="true" size={15} /> Back to {config.title}</Link>
      <header className="record-hero">
        <div className="record-identity">
          <Avatar name={name} size="lg" />
          <div><span className="eyebrow">{titleCase(config.singular)} · {record.number || "#" + record.id}</span><h1>{name}</h1><p>{record.subtitle || record.description || record.account_name || record.email || config.description}</p></div>
        </div>
        <div className="record-actions">
          <SignalLine label={"Updated " + formatDate(record.updated_at || record.created_at, { withTime: true })} />
          {status ? <Badge>{titleCase(status)}</Badge> : null}
          <div className="record-workflow-actions">
            {resourceKey === "leads" && status !== "Qualified" ? <button className="button button-primary" disabled={isBusy} onClick={() => openDialog("qualify")} type="button"><FileCheck2 aria-hidden="true" size={15} /> Qualify lead</button> : null}
            {resourceKey === "tenders" && ["New", "Reviewing"].includes(status) ? <><button className="button button-primary" disabled={isBusy} onClick={() => openDialog("qualify")} type="button"><FileCheck2 aria-hidden="true" size={15} /> Qualify tender</button><button className="button button-quiet" disabled={isBusy} onClick={() => openDialog("snoozeTender")} type="button">Snooze</button><button className="button button-quiet" disabled={isBusy} onClick={() => openDialog("rejectTender")} type="button">Reject</button></> : null}
            {resourceKey === "tenders" && ["Rejected", "Snoozed"].includes(status) ? <button className="button button-primary" disabled={isBusy} onClick={() => perform("tenders/" + id + "/reopen", { version: record.version, reason: "Reopened by operator" }, "Tender returned to review.")} type="button">Reopen tender</button> : null}
            {resourceKey === "opportunities" && !["Won", "Lost"].includes(record.status) ? <button className="button button-primary" disabled={isBusy} onClick={() => openDialog("transition")} type="button">Move stage</button> : null}
            {resourceKey === "proposals" && status === "Draft" ? <button className="button button-primary" disabled={isBusy} onClick={() => perform("proposals/" + id + "/send", {}, "Proposal marked as sent.", "Confirm the proposal is ready to send?")} type="button"><Send aria-hidden="true" size={15} /> Send proposal</button> : null}
            {resourceKey === "proposals" && status === "Sent" ? <><button className="button button-primary" disabled={isBusy} onClick={() => perform("proposals/" + id + "/accept", {}, "Proposal accepted and draft contract created.", "Confirm proposal acceptance?", (item) => "/contracts/" + item.contract.id)} type="button">Accept</button><button className="button button-quiet" disabled={isBusy} onClick={() => openDialog("rejectProposal")} type="button">Reject</button></> : null}
            {resourceKey === "contracts" && status === "Draft" ? <button className="button button-primary" disabled={isBusy} onClick={() => perform("contracts/" + id + "/send", {}, "Contract marked as sent.", "Confirm the contract is ready to send?")} type="button"><Send aria-hidden="true" size={15} /> Send contract</button> : null}
            {resourceKey === "contracts" && status === "Sent" ? <button className="button button-primary" disabled={isBusy} onClick={() => openDialog("sign")} type="button"><FileCheck2 aria-hidden="true" size={15} /> Record signature</button> : null}
            {resourceKey === "contracts" && status === "Signed" ? <button className="button button-primary" disabled={isBusy} onClick={() => perform("contracts/" + id + "/activate", {}, "Contract activated.", "Activate this signed contract?")} type="button">Activate</button> : null}
            {resourceKey === "contracts" && ["Signed", "Active"].includes(status) ? <button className="button button-quiet" disabled={isBusy} onClick={() => perform("contracts/" + id + "/project", {}, "Project created.", "", (item) => "/projects/" + item.id)} type="button"><Plus aria-hidden="true" size={15} /> Create project</button> : null}
            {resourceKey === "projects" ? <><button className="button button-primary" disabled={isBusy} onClick={() => openDialog("time")} type="button"><Clock3 aria-hidden="true" size={15} /> Log time</button><button className="button button-quiet" disabled={isBusy} onClick={() => openDialog("milestone")} type="button"><Plus aria-hidden="true" size={15} /> Add milestone</button><button className="button button-quiet" disabled={isBusy} onClick={() => openDialog("projectStatus")} type="button">Update status</button><button className="button button-quiet" disabled={isBusy} onClick={() => openDialog("expense")} type="button"><ReceiptText aria-hidden="true" size={15} /> Add expense</button></> : null}
            {resourceKey === "invoices" && status === "Draft" ? <button className="button button-primary" disabled={isBusy} onClick={() => perform("invoices/" + id + "/issue", {}, "Invoice issued and ledger posted.", "Issue this invoice? Customer, tax and line snapshots become immutable.")} type="button"><FileCheck2 aria-hidden="true" size={15} /> Issue invoice</button> : null}
            {resourceKey === "invoices" && ["Sent", "Part-paid", "Overdue"].includes(status) && outstanding > 0 ? <button className="button button-primary" disabled={isBusy} onClick={() => perform("invoices/" + id + "/payment-link", { amount_minor: outstanding, currency: String(record.currency || "GBP").toLowerCase(), description: "Payment for " + (record.number || "invoice #" + id) }, "Stripe payment link queued.", "Create a one-use Stripe payment link for the outstanding balance?")} type="button"><CreditCard aria-hidden="true" size={15} /> Payment link</button> : null}
            {resourceKey === "invoices" && ["Sent", "Part-paid", "Overdue"].includes(status) && outstanding > 0 ? <button className="button button-quiet" disabled={isBusy} onClick={() => setCreditOpen(true)} type="button"><ReceiptText aria-hidden="true" size={15} /> Credit invoice</button> : null}
            {resourceKey === "invoices" && ["Sent", "Overdue"].includes(status) && !record.paid_pence && !record.credited_pence ? <button className="button button-quiet" disabled={isBusy} onClick={() => perform("invoices/" + id + "/void", { reason: "Voided by operator" }, "Invoice voided with reversing journal.", "Void this invoice? A reversing journal entry will be posted.")} type="button">Void</button> : null}
            {resourceKey === "credit-notes" && status === "Draft" ? <button className="button button-primary" disabled={isBusy} onClick={() => perform("credit-notes/" + id + "/issue", {}, "Credit note issued and reversing ledger posted.", "Issue this credit note? This action is immutable.", (item) => "/credit-notes/" + (item.credit_note?.id || id))} type="button"><FileCheck2 aria-hidden="true" size={15} /> Issue credit note</button> : null}
            {resourceKey === "payments" && Number(record.amount_pence || 0) > Number(record.refunded_pence || 0) ? <button className="button button-quiet" disabled={isBusy} onClick={() => openDialog("refund")} type="button"><RotateCcw aria-hidden="true" size={15} /> Refund</button> : null}
            {resourceKey === "client-success" ? <button className="button button-primary" disabled={isBusy} onClick={() => setClientSuccessOpen(true)} type="button">Update success plan</button> : null}
            {resourceKey === "sequences" ? <SequenceWorkflowActions onChanged={() => reload()} onEnrollmentChanged={() => setSequenceEnrollmentVersion((version) => version + 1)} sequence={record} /> : null}
            {resourceKey === "files" ? <DocumentSyncControl document={record} onQueued={() => reload()} /> : null}
            <button className="button button-quiet" onClick={() => setManagementOpen(true)} type="button"><Settings2 aria-hidden="true" size={15} /> Manage</button>
          </div>
        </div>
      </header>

      {feedback ? <p aria-live="polite" className={"action-feedback " + feedback.tone}>{feedback.message}</p> : null}

      <nav aria-label="Record workspace" className="record-tabs" role="tablist">
        {tabs.map((item, index) => <button aria-controls={`${tabId}-${item}-panel`} aria-selected={tab === item} id={`${tabId}-${item}-tab`} key={item} onClick={() => setTab(item)} onKeyDown={(event) => handleTabKeyDown(event, index)} ref={(node) => { tabRefs.current[index] = node; }} role="tab" tabIndex={tab === item ? 0 : -1} type="button">{titleCase(item)}</button>)}
      </nav>

      <div aria-labelledby={`${tabId}-${tab}-tab`} id={`${tabId}-${tab}-panel`} role="tabpanel" tabIndex="0">
      {tab === "overview" ? (
        <div className="record-layout">
          <main>
            <section className="record-section record-summary">
              <header><h2>Overview</h2><span>Local source of truth</span></header>
              <dl>{config.fields.map(([label, ...keys]) => <div key={label}><dt>{label}</dt><dd>{displayField(record, keys)}</dd></div>)}</dl>
            </section>
            {resourceKey === "sequences" ? <section className="record-section"><header><h2>Sequence steps</h2><span>{record.steps?.length || 0}</span></header>{record.steps?.length ? <ol className="sequence-steps">{record.steps.map((step) => <li key={step.id}><Badge tone="info">{titleCase(step.step_type)}</Badge><div><strong>{step.subject || step.task_title || (step.delay_minutes ? "Wait " + step.delay_minutes + " minutes" : "Sequence step")}</strong><p>{step.body_text || step.task_description || ""}</p>{record.state === "Draft" ? <SequenceStepControl onChanged={() => reload()} sequence={record} step={step} /> : null}</div></li>)}</ol> : <p className="section-empty">Add an email, delay or manual task before activation.</p>}</section> : null}
            {resourceKey === "sequences" ? <SequenceEnrollmentsPanel refreshToken={sequenceEnrollmentVersion} sequenceId={record.id} /> : null}
            {resourceKey === "files" ? <DocumentVersionsPanel document={record} /> : null}
            <section className="record-section">
              <header><h2>Current focus</h2><button className="text-button" onClick={() => openDialog("task")} type="button"><Plus aria-hidden="true" size={14} /> Add next action</button></header>
              <div className="focus-card"><span><CheckCircle2 aria-hidden="true" size={18} /></span><div><strong>{record.next_action || "Define the next action"}</strong><p>{record.next_action_detail || "A clear owner and due date keeps this record moving."}</p></div><time>{formatDate(record.next_action_at || record.due_at)}</time></div>
            </section>
            <RelatedSection configKey="opportunities" rows={related.opportunities} title="Deals" />
          </main>
          <aside>
            <section className="record-section quick-panel">
              <header><h2>Quick actions</h2></header>
              <button onClick={() => setEmailOpen(true)} type="button"><Mail aria-hidden="true" size={17} /><span><strong>Write email</strong><small>Compose with this record in context</small></span></button>
              <button onClick={() => setCalendarOpen(true)} type="button"><CalendarClock aria-hidden="true" size={17} /><span><strong>Schedule meeting</strong><small>Add context to the calendar</small></span></button>
              <button onClick={() => window.dispatchEvent(new CustomEvent("crm:quick-create", { detail: { type: "contacts" } }))} type="button"><UserRound aria-hidden="true" size={17} /><span><strong>Add relationship</strong><small>Create a contact and choose its account</small></span></button>
            </section>
            <section className="record-section detail-panel"><header><h2>System details</h2></header><dl><div><dt>Created</dt><dd>{formatDate(record.created_at, { withTime: true })}</dd></div><div><dt>Updated</dt><dd>{formatDate(record.updated_at, { withTime: true })}</dd></div><div><dt>Version</dt><dd>{record.version ?? "—"}</dd></div><div><dt>Record ID</dt><dd>{record.id}</dd></div></dl>{record.external_url ? <a href={record.external_url} rel="noreferrer" target="_blank">Open source <ExternalLink aria-hidden="true" size={14} /></a> : null}</section>
          </aside>
        </div>
      ) : null}

      {tab === "relationships" ? <div className="record-tab-grid"><RelatedSection configKey="contacts" rows={related.contacts} title="Contacts" /><RelatedSection configKey="opportunities" rows={related.opportunities} title="Deals" /><RelatedSection configKey="tenders" rows={related.tenders} title="Tenders" /></div> : null}
      {tab === "delivery" ? <div className="record-tab-grid"><RelatedSection configKey="projects" rows={related.projects} title="Projects" /><RelatedSection configKey="milestones" rows={related.milestones} title="Milestones" /><RelatedSection configKey="time-entries" rows={related.timeEntries} title="Time entries" /><RelatedSection configKey="expenses" rows={related.expenses} title="Expenses" /></div> : null}
      {tab === "commercial" ? <div className="record-tab-grid"><RelatedSection configKey="proposals" rows={related.proposals} title="Proposals" /><RelatedSection configKey="contracts" rows={related.contracts} title="Contracts" /><RelatedSection configKey="invoices" rows={related.invoices} title="Invoices" /><RelatedSection configKey="payments" rows={related.payments} title="Payments" /></div> : null}
      {tab === "activity" ? (
        <section className="record-section timeline-section">
          <header><h2>Activity</h2><button className="text-button" onClick={() => openDialog("note")} type="button"><Plus aria-hidden="true" size={14} /> Add note</button></header>
          {related.activity.length ? <ol className="timeline">{related.activity.map((item, index) => <li key={item.id || index}><span className="timeline-node" /><div><strong>{recordName(item, titleCase(item.type || item.kind || "Activity"))}</strong><p>{item.detail || item.description || item.body}</p><time>{formatDate(item.occurred_at || item.created_at, { withTime: true })}</time></div></li>)}</ol> : <EmptyState title="No activity yet" message="Emails, meetings, notes, transitions and financial events will form one timeline." />}
        </section>
      ) : null}
      </div>

      <RecordManagementDialog entityType={entityType} onClose={() => setManagementOpen(false)} onNavigate={(targetId) => navigate(targetId ? `${config.path}/${targetId}` : config.path)} onReload={reload} open={managementOpen} record={record} resourceKey={resourceKey} />
      <EmailComposer context={workflowContext} onClose={() => setEmailOpen(false)} onSent={() => setFeedback({ tone: "positive", message: "Email queued for Gmail delivery." })} open={emailOpen} />
      <CalendarEventDialog context={workflowContext} onClose={() => setCalendarOpen(false)} onCreated={() => { setFeedback({ tone: "positive", message: "Calendar event created locally." }); reload(); }} open={calendarOpen} />
      {resourceKey === "invoices" ? <CreditNoteDialog invoice={record} onClose={() => setCreditOpen(false)} onIssued={(credit) => navigate(`/credit-notes/${credit.id}`)} open={creditOpen} /> : null}
      {resourceKey === "client-success" ? <ClientSuccessDialog onClose={() => setClientSuccessOpen(false)} onSaved={() => { setFeedback({ tone: "positive", message: "Client success plan updated." }); reload(); }} open={clientSuccessOpen} record={record} /> : null}

      <WorkflowDialog
        confirmMessage={dialog === "sign" ? "Confirm that the signed contract evidence has been received?" : ""}
        description={{
          qualify: "Link or create the account, create a deal, retain provenance and set the first next action.",
          transition: "Move the deal deliberately. Won and lost stages update the account lifecycle and audit trail.",
          rejectProposal: "Record why the proposal was not accepted.",
          rejectTender: "Reject this notice with a durable triage reason.",
          snoozeTender: "Hide this notice until the selected review date.",
          sign: "Record the signature date. Uploading the signed copy can follow from Files.",
          time: "Add delivery time against this project.",
          expense: "Add a project cost for profitability and optional rebilling.",
          projectStatus: "Keep the delivery state current so blockers and completion are visible in Today and client health.",
          milestone: "Add a dated delivery and billing checkpoint to this project.",
          refund: "Refund against the payment and post the reversing journal.",
          task: "Create a linked next action.",
          note: "Add a permanent note to the chronological activity stream.",
        }[dialog]}
        onClose={() => setDialog("")}
        onSubmit={submitDialog}
        open={Boolean(dialog)}
        submitLabel={{
          qualify: "Qualify",
          transition: "Move deal",
          rejectProposal: "Reject proposal",
          rejectTender: "Reject tender",
          snoozeTender: "Snooze tender",
          sign: "Record signature",
          time: "Log time",
          expense: "Add expense",
          projectStatus: "Update project",
          milestone: "Add milestone",
          refund: "Refund payment",
          task: "Create task",
          note: "Add note",
        }[dialog] || "Save"}
        title={{
          qualify: "Qualify " + config.singular,
          transition: "Move deal stage",
          rejectProposal: "Reject proposal",
          rejectTender: "Reject tender",
          snoozeTender: "Snooze tender",
          sign: "Record contract signature",
          time: "Log project time",
          expense: "Add project expense",
          projectStatus: "Update project status",
          milestone: "Add project milestone",
          refund: "Refund payment",
          task: "Add next action",
          note: "Add activity note",
        }[dialog] || "Record action"}
      >
        {dialog === "qualify" ? <><label><span>Account name</span><input onChange={(event) => update("account_name", event.target.value)} required={!(record.account_id || record.buyer_account_id)} value={form.account_name || ""} /></label><label className="field-wide"><span>Deal title</span><input onChange={(event) => update("opportunity_title", event.target.value)} required value={form.opportunity_title || ""} /></label><label><span>Estimated value (£)</span><input min="0" onChange={(event) => update("value", event.target.value)} step="0.01" type="number" value={form.value || ""} /></label><label className="field-wide"><span>First next action</span><input onChange={(event) => update("next_action", event.target.value)} required value={form.next_action || ""} /></label></> : null}
        {dialog === "transition" ? <><label className="field-wide"><span>Stage</span><select onChange={(event) => update("stage_id", event.target.value)} required value={form.stage_id || ""}><option value="">Select stage</option>{stages.map((stage) => <option key={stage.id} value={stage.id}>{stage.name}</option>)}</select></label>{selectedStage?.kind === "lost" || selectedStage?.name === "Lost" ? <label className="field-wide"><span>Loss reason</span><textarea onChange={(event) => update("loss_reason", event.target.value)} required rows="4" value={form.loss_reason || ""} /></label> : null}</> : null}
        {["rejectProposal", "rejectTender"].includes(dialog) ? <label className="field-wide"><span>Reason</span><textarea onChange={(event) => update("reason", event.target.value)} required rows="4" value={form.reason || ""} /></label> : null}
        {dialog === "snoozeTender" ? <><label className="field-wide"><span>Return to review on</span><input min={new Date().toISOString().slice(0, 10)} onChange={(event) => update("snoozed_until", event.target.value)} required type="date" value={form.snoozed_until || ""} /></label><label className="field-wide"><span>Reason (optional)</span><textarea onChange={(event) => update("reason", event.target.value)} rows="3" value={form.reason || ""} /></label></> : null}
        {dialog === "sign" ? <label className="field-wide"><span>Signed at</span><input onChange={(event) => update("signed_at", event.target.value)} required type="datetime-local" value={form.signed_at || ""} /></label> : null}
        {dialog === "time" ? <><label><span>Date</span><input onChange={(event) => update("entry_date", event.target.value)} required type="date" value={form.entry_date || ""} /></label><label><span>Minutes</span><input max="1440" min="1" onChange={(event) => update("minutes", event.target.value)} required type="number" value={form.minutes || ""} /></label><label className="field-wide"><span>Description</span><input onChange={(event) => update("description", event.target.value)} value={form.description || ""} /></label><label><span>Hourly rate (£)</span><input min="0" onChange={(event) => update("hourly_rate", event.target.value)} step="0.01" type="number" value={form.hourly_rate || ""} /></label><label className="checkbox-field"><input checked={Boolean(form.billable)} onChange={(event) => update("billable", event.target.checked)} type="checkbox" /><span>Billable time</span></label></> : null}
        {dialog === "expense" ? <><label><span>Date</span><input onChange={(event) => update("expense_date", event.target.value)} required type="date" value={form.expense_date || ""} /></label><label><span>Vendor</span><input onChange={(event) => update("vendor", event.target.value)} value={form.vendor || ""} /></label><label className="field-wide"><span>Description</span><input onChange={(event) => update("description", event.target.value)} required value={form.description || ""} /></label><label><span>Net amount (£)</span><input min="0" onChange={(event) => update("net", event.target.value)} required step="0.01" type="number" value={form.net || ""} /></label><label><span>VAT rate (%)</span><input max="100" min="0" onChange={(event) => update("tax_percent", event.target.value)} step="0.01" type="number" value={form.tax_percent || "0"} /></label><label className="checkbox-field"><input checked={Boolean(form.billable)} onChange={(event) => update("billable", event.target.checked)} type="checkbox" /><span>Rebill to client</span></label></> : null}
        {dialog === "projectStatus" ? <label className="field-wide"><span>Project status</span><select onChange={(event) => update("status", event.target.value)} value={form.status || "Planned"}>{["Planned", "Active", "Blocked", "Complete", "Cancelled"].map((item) => <option key={item}>{item}</option>)}</select></label> : null}
        {dialog === "milestone" ? <><label className="field-wide"><span>Milestone title</span><input onChange={(event) => update("title", event.target.value)} required value={form.title || ""} /></label><label><span>Due date</span><input onChange={(event) => update("due_on", event.target.value)} type="date" value={form.due_on || ""} /></label><label><span>Billing amount (£)</span><input min="0" onChange={(event) => update("amount", event.target.value)} step="0.01" type="number" value={form.amount || ""} /></label><label><span>Status</span><select onChange={(event) => update("status", event.target.value)} value={form.status || "Planned"}><option>Planned</option><option>In progress</option><option>Complete</option><option>Cancelled</option></select></label></> : null}
        {dialog === "refund" ? <><label><span>Amount (£)</span><input min="0.01" onChange={(event) => update("amount", event.target.value)} required step="0.01" type="number" value={form.amount || ""} /></label><label><span>Invoice ID (optional)</span><input min="1" onChange={(event) => update("invoice_id", event.target.value)} type="number" value={form.invoice_id || ""} /></label><label className="field-wide"><span>Reason</span><textarea onChange={(event) => update("reason", event.target.value)} required rows="4" value={form.reason || ""} /></label></> : null}
        {dialog === "task" ? <><label className="field-wide"><span>Task</span><input onChange={(event) => update("title", event.target.value)} required value={form.title || ""} /></label><label><span>Due</span><input onChange={(event) => update("due_at", event.target.value)} type="datetime-local" value={form.due_at || ""} /></label><label><span>Priority</span><select onChange={(event) => update("priority", event.target.value)} value={form.priority || "Medium"}><option>Low</option><option>Medium</option><option>High</option></select></label><label className="field-wide"><span>Detail</span><textarea onChange={(event) => update("description", event.target.value)} rows="3" value={form.description || ""} /></label></> : null}
        {dialog === "note" ? <><label className="field-wide"><span>Subject</span><input onChange={(event) => update("subject", event.target.value)} required value={form.subject || ""} /></label><label className="field-wide"><span>Note</span><textarea onChange={(event) => update("body", event.target.value)} required rows="6" value={form.body || ""} /></label></> : null}
      </WorkflowDialog>
    </div>
  );
}
