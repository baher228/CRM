import React, { useMemo, useState } from "react";
import { Clock3, FileText, Link2, Plus, RefreshCw, ShieldOff, Trash2, Unlink } from "lucide-react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { useResource } from "../hooks";
import { formatDate, recordName, titleCase } from "../utils/format";
import { AppDialog, Badge, EmptyState, LoadingState, PageControls, UnavailableState } from "./common";
import { WorkflowDialog } from "./WorkflowDialogs";

function linkedRecordName(type, id, records) {
  const item = records[type]?.find((record) => Number(record.id) === Number(id));
  return item ? recordName(item, `${titleCase(type)} #${id}`) : `${titleCase(type)} #${id}`;
}

export function ThreadLinksControl({ thread, onChanged }) {
  const accountsState = useResource("accounts", { query: { limit: 100 } });
  const contactsState = useResource("contacts", { query: { limit: 100 } });
  const [selection, setSelection] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const records = useMemo(() => ({
    account: accountsState.data,
    contact: contactsState.data,
  }), [accountsState.data, contactsState.data]);

  async function refreshThread() {
    const detail = await api.get(`email/threads/${thread.id}`);
    onChanged(detail);
  }

  async function linkThread(event) {
    event.preventDefault();
    const [entityType, rawId] = selection.split(":");
    if (!entityType || !rawId) return;
    setBusy("link");
    setError("");
    try {
      await api.post(`email/threads/${thread.id}/links`, { entity_type: entityType, entity_id: Number(rawId) });
      setSelection("");
      await refreshThread();
    } catch (caught) {
      setError(caught.message);
    } finally {
      setBusy("");
    }
  }

  async function unlinkThread(link) {
    if (!window.confirm(`Remove the ${linkedRecordName(link.entity_type, link.entity_id, records)} link from this conversation?`)) return;
    setBusy(`${link.entity_type}:${link.entity_id}`);
    setError("");
    try {
      await api.remove(`email/threads/${thread.id}/links/${encodeURIComponent(link.entity_type)}/${link.entity_id}`);
      await refreshThread();
    } catch (caught) {
      setError(caught.message);
    } finally {
      setBusy("");
    }
  }

  const existing = new Set((thread.links || []).map((link) => `${link.entity_type}:${link.entity_id}`));
  return (
    <section aria-label="Conversation links" className="thread-links">
      <header><div><strong><Link2 aria-hidden="true" size={14} /> CRM context</strong><span>Link this thread to the people or organisations it affects.</span></div></header>
      <div className="thread-link-list">
        {(thread.links || []).map((link) => <span className="thread-link" key={`${link.entity_type}:${link.entity_id}`}><Badge tone="info">{linkedRecordName(link.entity_type, link.entity_id, records)}</Badge><button aria-label={`Remove ${linkedRecordName(link.entity_type, link.entity_id, records)} link`} disabled={Boolean(busy)} onClick={() => unlinkThread(link)} type="button"><Unlink aria-hidden="true" size={12} /></button></span>)}
        {!thread.links?.length ? <span className="section-empty compact">No CRM records linked.</span> : null}
      </div>
      <form className="thread-link-form" onSubmit={linkThread}>
        <label><span className="sr-only">CRM record to link</span><select disabled={Boolean(busy)} onChange={(event) => setSelection(event.target.value)} value={selection}><option value="">Choose CRM record</option><optgroup label="Accounts">{accountsState.data.filter((item) => !existing.has(`account:${item.id}`)).map((item) => <option key={`account:${item.id}`} value={`account:${item.id}`}>{recordName(item)}</option>)}</optgroup><optgroup label="Contacts">{contactsState.data.filter((item) => !existing.has(`contact:${item.id}`)).map((item) => <option key={`contact:${item.id}`} value={`contact:${item.id}`}>{recordName(item)}</option>)}</optgroup></select></label>
        <button className="button button-quiet" disabled={!selection || Boolean(busy)} type="submit"><Plus aria-hidden="true" size={13} /> Link</button>
      </form>
      {error ? <p className="field-error-text" role="alert">{error}</p> : null}
    </section>
  );
}

const EMPTY_TEMPLATE = { name: "", category: "", subject: "", body_text: "", active: true };

function templateForm(template) {
  return template ? {
    name: template.name || "",
    category: template.category || "",
    subject: template.subject || "",
    body_text: template.body_text || "",
    active: template.active !== false,
  } : EMPTY_TEMPLATE;
}

function EmailTemplateDialog({ open, template, onClose, onSaved }) {
  const [form, setForm] = useState(() => templateForm(template));
  React.useEffect(() => {
    if (open) setForm(templateForm(template));
  }, [open, template?.id, template?.version]);

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  return (
    <WorkflowDialog description={"Reusable copy supports flat double-brace tokens such as {{name}} and {{account_name}}."} onClose={onClose} onSubmit={async () => {
      const payload = { ...form, name: form.name.trim(), category: form.category.trim() };
      const saved = template
        ? await api.patch(`email/templates/${template.id}`, { version: template.version, ...payload })
        : await api.post("email/templates", payload);
      await onSaved(saved);
      onClose();
    }} open={open} submitLabel={template ? "Save template" : "Create template"} title={template ? "Edit email template" : "New email template"}>
      <label><span>Name</span><input onChange={(event) => update("name", event.target.value)} required value={form.name} /></label>
      <label><span>Category</span><input onChange={(event) => update("category", event.target.value)} value={form.category} /></label>
      <label className="field-wide"><span>Subject</span><input onChange={(event) => update("subject", event.target.value)} value={form.subject} /></label>
      <label className="field-wide"><span>Message</span><textarea onChange={(event) => update("body_text", event.target.value)} required rows="8" value={form.body_text} /></label>
      <label className="checkbox-field field-wide"><input checked={form.active} onChange={(event) => update("active", event.target.checked)} type="checkbox" /><span>Available to composers</span></label>
    </WorkflowDialog>
  );
}

export function EmailTemplatesPanel() {
  const templatesState = useResource("email/templates", { pageSize: 25 });
  const [dialog, setDialog] = useState({ open: false, template: null });
  const [preview, setPreview] = useState(null);
  const [feedback, setFeedback] = useState("");

  async function archive(template) {
    if (!window.confirm(`Archive ${template.name}? Existing messages remain unchanged.`)) return;
    try {
      await api.post(`email/templates/${template.id}/archive`, { version: template.version }, { headers: { "X-CRM-Confirmed": "true" } });
      setFeedback("Email template archived.");
      await templatesState.reload();
    } catch (caught) {
      setFeedback(caught.message);
    }
  }

  async function showPreview(template) {
    try {
      const rendered = await api.post(`email/templates/${template.id}/preview`, { values: { name: "Alex", account_name: "North Star Ltd", title: "CRM follow-up" } });
      setPreview({ ...rendered, name: template.name });
    } catch (caught) {
      setFeedback(caught.message);
    }
  }

  return (
    <section aria-label="Email templates" className="operations-panel">
      <div className="operations-toolbar"><div><strong>Email templates</strong><span>Approved reusable copy for replies, scheduled messages, and sequences.</span></div><button className="button button-primary" onClick={() => setDialog({ open: true, template: null })} type="button"><Plus aria-hidden="true" size={15} /> New template</button></div>
      {feedback ? <p aria-live="polite" className="action-feedback">{feedback}</p> : null}
      {templatesState.loading ? <LoadingState label="Loading email templates" /> : null}
      {templatesState.error ? <UnavailableState error={templatesState.error} onRetry={templatesState.reload} /> : null}
      {!templatesState.loading && !templatesState.error && !templatesState.data.length ? <EmptyState icon={FileText} title="No email templates yet" message="Create reusable copy for frequent messages and sequence steps." /> : null}
      <div className="control-list">
        {templatesState.data.map((template) => <article className="control-row" key={template.id}><div><span><strong>{template.name}</strong><Badge tone={template.active ? "positive" : "neutral"}>{template.active ? "Active" : "Inactive"}</Badge></span><p>{template.subject || "No subject"} Â· {template.category || "Uncategorised"}</p></div><div><button className="button button-quiet" onClick={() => showPreview(template)} type="button">Preview</button><button className="button button-quiet" onClick={() => setDialog({ open: true, template })} type="button">Edit</button><button className="button button-danger" onClick={() => archive(template)} type="button"><Trash2 aria-hidden="true" size={13} /> Archive</button></div></article>)}
      </div>
      <PageControls hasNext={templatesState.hasNext} hasPrevious={templatesState.hasPrevious} label="Email templates" nextPage={templatesState.nextPage} page={templatesState.page} previousPage={templatesState.previousPage} />
      <EmailTemplateDialog onClose={() => setDialog({ open: false, template: null })} onSaved={async () => { setFeedback("Email template saved."); await templatesState.reload(); }} open={dialog.open} template={dialog.template} />
      <AppDialog description="Example merge values are used only for this preview." onClose={() => setPreview(null)} open={Boolean(preview)} title={preview?.name || "Template preview"}><article className="template-preview"><strong>{preview?.subject || "(No subject)"}</strong><p>{preview?.body_text}</p></article></AppDialog>
    </section>
  );
}

export function ScheduledSendsPanel() {
  const [state, setState] = useState("");
  const sendsState = useResource("email/scheduled-sends", { query: { state: state || undefined, limit: 25 }, pageSize: 25 });
  return (
    <section aria-label="Scheduled email sends" className="operations-panel">
      <div className="operations-toolbar"><div><strong>Delivery queue</strong><span>Queued, paused, sent, failed, and cancelled Gmail work.</span></div><div className="operations-actions"><label className="select-field"><span className="sr-only">Filter send state</span><select onChange={(event) => setState(event.target.value)} value={state}><option value="">All states</option>{["Queued", "Paused", "Sent", "Failed", "Cancelled"].map((item) => <option key={item}>{item}</option>)}</select></label><button aria-label="Refresh delivery queue" className="icon-button" onClick={sendsState.reload} type="button"><RefreshCw aria-hidden="true" size={15} /></button></div></div>
      {sendsState.loading ? <LoadingState label="Loading scheduled sends" /> : null}
      {sendsState.error ? <UnavailableState error={sendsState.error} onRetry={sendsState.reload} /> : null}
      {!sendsState.loading && !sendsState.error && !sendsState.data.length ? <EmptyState icon={Clock3} title="Delivery queue is clear" message="Scheduled and sequence messages will appear here." /> : null}
      <div className="control-list">
        {sendsState.data.map((send) => <article className="control-row" key={send.id}><div><span><strong>{send.subject || "(No subject)"}</strong><Badge>{titleCase(send.state)}</Badge></span><p>{send.to_email} Â· {formatDate(send.scheduled_for, { withTime: true })}</p>{send.last_error ? <small>{send.last_error}</small> : null}</div><span className="mono-note">#{send.id}</span></article>)}
      </div>
      <PageControls hasNext={sendsState.hasNext} hasPrevious={sendsState.hasPrevious} label="Scheduled sends" nextPage={sendsState.nextPage} page={sendsState.page} previousPage={sendsState.previousPage} />
      <p className="operations-note">Failed or unknown provider outcomes are recovered deliberately in <Link to="/settings">Settings</Link>.</p>
    </section>
  );
}

export function SuppressionsPanel() {
  const suppressionsState = useResource("email/suppressions", { pageSize: 25 });
  const [form, setForm] = useState({ email: "", reason: "" });
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");

  async function add(event) {
    event.preventDefault();
    setBusy(true);
    setFeedback("");
    try {
      await api.post("email/suppressions", { email: form.email, reason: form.reason, source: "manual" });
      setForm({ email: "", reason: "" });
      setFeedback("Recipient suppressed and active sequence enrollments stopped.");
      await suppressionsState.reload();
    } catch (caught) {
      setFeedback(caught.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(item) {
    if (!window.confirm(`Remove the suppression for ${item.email}? This does not resume cancelled sequence enrollments.`)) return;
    setBusy(true);
    setFeedback("");
    try {
      await api.remove(`email/suppressions/${encodeURIComponent(item.email)}`);
      setFeedback("Suppression removed.");
      await suppressionsState.reload();
    } catch (caught) {
      setFeedback(caught.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Email suppressions" className="operations-panel">
      <div className="operations-toolbar"><div><strong>Suppression list</strong><span>Opt-outs stop queued sequence work and block future sends.</span></div></div>
      <form className="suppression-form" onSubmit={add}><label><span>Email address</span><input disabled={busy} onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))} required type="email" value={form.email} /></label><label><span>Reason</span><input disabled={busy} onChange={(event) => setForm((current) => ({ ...current, reason: event.target.value }))} placeholder="Requested opt-out" value={form.reason} /></label><button className="button button-primary" disabled={busy} type="submit"><ShieldOff aria-hidden="true" size={14} /> Suppress</button></form>
      {feedback ? <p aria-live="polite" className="action-feedback">{feedback}</p> : null}
      {suppressionsState.loading ? <LoadingState label="Loading suppressions" /> : null}
      {suppressionsState.error ? <UnavailableState error={suppressionsState.error} onRetry={suppressionsState.reload} /> : null}
      {!suppressionsState.loading && !suppressionsState.error && !suppressionsState.data.length ? <EmptyState icon={ShieldOff} title="No suppressed recipients" message="Manual opt-outs and bounce-driven suppressions will be recorded here." /> : null}
      <div className="control-list">
        {suppressionsState.data.map((item) => <article className="control-row" key={item.email}><div><span><strong>{item.email}</strong><Badge tone="warning">{titleCase(item.source || "manual")}</Badge></span><p>{item.reason || "No reason recorded"} Â· {formatDate(item.updated_at || item.created_at, { withTime: true })}</p></div><button className="button button-quiet" disabled={busy} onClick={() => remove(item)} type="button">Remove</button></article>)}
      </div>
      <PageControls hasNext={suppressionsState.hasNext} hasPrevious={suppressionsState.hasPrevious} label="Suppressions" nextPage={suppressionsState.nextPage} page={suppressionsState.page} previousPage={suppressionsState.previousPage} />
    </section>
  );
}
