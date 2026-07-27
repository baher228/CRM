import React, { useEffect, useId, useMemo, useState } from "react";
import { Archive, CheckCircle2, Pencil, Plus, Trash2 } from "lucide-react";

import { api } from "../api";
import { useResource } from "../hooks";
import { poundsToMinor } from "../utils/business";
import { formatMoney } from "../utils/format";
import { AppDialog, Badge, EmptyState, LoadingState, UnavailableState } from "./common";

let lineSequence = 0;

function blankCommercialLine() {
  lineSequence += 1;
  return {
    key: `line-${lineSequence}`,
    catalog_item_id: "",
    description: "",
    quantity: "1",
    unit_price: "",
    tax_percent: "0",
    discount_percent: "0",
  };
}

export function vatIsEnabled(profile = {}, today = new Date().toISOString().slice(0, 10)) {
  return Boolean(
    profile.vat_registered
      && String(profile.legal_name || "").trim()
      && String(profile.vat_number || "").trim()
      && String(profile.vat_scheme || "").trim()
      && profile.vat_effective_from
      && profile.vat_effective_from <= today
      && (!profile.vat_effective_to || profile.vat_effective_to >= today)
      && profile.tax_codes_approved,
  );
}

export function commercialLinePayload(line, vatEnabled = true) {
  return {
    description: String(line.description || "").trim(),
    quantity: String(line.quantity || "1"),
    unit_price_pence: poundsToMinor(line.unit_price),
    tax_rate_bps: vatEnabled ? Math.round(Number(line.tax_percent || 0) * 100) : 0,
    discount_bps: Math.round(Number(line.discount_percent || 0) * 100),
    catalog_item_id: line.catalog_item_id ? Number(line.catalog_item_id) : undefined,
  };
}

export function commercialLineTotalMinor(line, vatEnabled = true) {
  const payload = commercialLinePayload(line, vatEnabled);
  const quantity = Number(payload.quantity || 1);
  const net = Math.round(payload.unit_price_pence * quantity * (10_000 - payload.discount_bps) / 10_000);
  return net + Math.round(net * payload.tax_rate_bps / 10_000);
}

function localInput(date) {
  const value = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return value.toISOString().slice(0, 16);
}

function defaultMeeting() {
  const start = new Date();
  start.setMinutes(0, 0, 0);
  start.setHours(start.getHours() + 1);
  return { starts_at: localInput(start), ends_at: localInput(new Date(start.getTime() + 3600000)) };
}

function emailAddress(value = "") {
  return String(value).match(/<([^>]+)>/)?.[1] || String(value).trim();
}

function replyDefaults(thread) {
  const messages = thread?.messages || [];
  const inbound = [...messages].reverse().find((message) => message.direction === "inbound");
  const recipient = emailAddress(inbound?.from_email || thread?.from_email || thread?.participants?.[0] || "");
  const subject = String(thread?.subject || "").replace(/^re:\s*/i, "");
  return {
    to: recipient,
    subject: subject ? "Re: " + subject : "",
    body_text: "",
    schedule_at: "",
  };
}

export function WorkflowDialog({
  open,
  title,
  description,
  submitLabel = "Save",
  confirmMessage = "",
  onClose,
  onSubmit,
  children,
  auxiliaryAction,
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const formErrorId = useId();

  useEffect(() => {
    if (open) setError(null);
  }, [open]);

  async function submit(event) {
    event.preventDefault();
    if (confirmMessage && !window.confirm(confirmMessage)) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit();
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppDialog className="workflow-dialog" description={description} onClose={() => !busy && onClose()} open={open} title={title}>
      <form aria-busy={busy} aria-describedby={error ? formErrorId : undefined} className="workflow-form" onSubmit={submit}>
        <div className="workflow-fields">{children}</div>
        {error ? <div className="form-error" id={formErrorId} role="alert"><strong>Could not complete this action.</strong><span>{error.message}</span></div> : null}
        <span aria-live="polite" className="sr-only" role="status">{busy ? `${submitLabel} in progress` : ""}</span>
        <footer>
          <span><CheckCircle2 aria-hidden="true" size={15} /> Changes are written to the local audit trail</span>
          <div>{auxiliaryAction}<button className="button button-quiet" disabled={busy} onClick={onClose} type="button">Cancel</button><button className="button button-primary" disabled={busy} type="submit">{busy ? "Working…" : submitLabel}</button></div>
        </footer>
      </form>
    </AppDialog>
  );
}

export function EmailComposer({ open, onClose, onSent, thread, context }) {
  const [form, setForm] = useState(replyDefaults(thread));
  const [templateId, setTemplateId] = useState("");
  const [templateBusy, setTemplateBusy] = useState(false);
  const [templateError, setTemplateError] = useState("");
  const templatesState = useResource("email/templates");

  useEffect(() => {
    if (!open) return;
    const reply = replyDefaults(thread);
    setForm({
      ...reply,
      to: reply.to || context?.email || context?.billing_email || "",
      subject: reply.subject || (context ? "Regarding " + (context.name || context.title || context.display_name || "") : ""),
    });
    setTemplateId("");
    setTemplateError("");
  }, [open, thread, context]);

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  const messages = thread?.messages || [];
  const replyTo = [...messages].reverse().find((message) => message.rfc_message_id);

  async function applyTemplate(nextId) {
    setTemplateId(nextId);
    setTemplateError("");
    if (!nextId) return;
    setTemplateBusy(true);
    try {
      const name = context?.name || context?.display_name || context?.title || "";
      const preview = await api.post(`email/templates/${nextId}/preview`, {
        values: {
          name,
          first_name: context?.first_name || name.split(" ")[0] || "",
          account_name: context?.account_name || context?.name || "",
          company_name: context?.company_name || context?.name || "",
          title: context?.title || context?.name || "",
          email: context?.email || form.to,
        },
      });
      setForm((current) => ({ ...current, subject: preview.subject, body_text: preview.body_text }));
    } catch (error) {
      setTemplateError(error.message);
    } finally {
      setTemplateBusy(false);
    }
  }

  return (
    <WorkflowDialog
      confirmMessage="Queue this email for Gmail delivery?"
      description={thread ? "Reply in the linked Gmail conversation. The durable worker reconciles delivery before retrying." : "Compose locally, then queue the approved message for Gmail delivery."}
      onClose={onClose}
      onSubmit={async () => {
        const result = await api.post("email/send", {
          to: form.to,
          subject: form.subject,
          body_text: form.body_text,
          schedule_at: form.schedule_at || undefined,
          thread_id: thread?.gmail_thread_id || undefined,
          reply_to_message_id: replyTo?.rfc_message_id || undefined,
        }, { headers: { "X-CRM-Confirmed": "true" } });
        onSent?.(result);
        onClose();
      }}
      open={open}
      submitLabel={form.schedule_at ? "Schedule email" : "Queue email"}
      title={thread ? "Reply to conversation" : "Compose email"}
    >
      <label><span>To</span><input autoComplete="email" onChange={(event) => update("to", event.target.value)} required type="email" value={form.to} /></label>
      <label><span>Template (optional)</span><select aria-describedby={templatesState.error || templateError ? "email-template-error" : undefined} disabled={templateBusy} onChange={(event) => applyTemplate(event.target.value)} value={templateId}><option value="">Write from scratch</option>{templatesState.data.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}</select></label>
      {templatesState.error || templateError ? <small className="field-error-text field-wide" id="email-template-error">{templateError || "Templates could not be loaded; composing still works."}</small> : null}
      <label className="field-wide"><span>Subject</span><input onChange={(event) => update("subject", event.target.value)} required value={form.subject} /></label>
      <label className="field-wide"><span>Message</span><textarea onChange={(event) => update("body_text", event.target.value)} required rows="8" value={form.body_text} /></label>
      <label><span>Send later (optional)</span><input onChange={(event) => update("schedule_at", event.target.value)} type="datetime-local" value={form.schedule_at} /></label>
    </WorkflowDialog>
  );
}

export function CalendarEventDialog({ open, onClose, onCreated, onSaved, onArchived, context, event }) {
  const [form, setForm] = useState({ title: "", body: "", location: "", ...defaultMeeting() });
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [archiveError, setArchiveError] = useState("");

  useEffect(() => {
    if (!open) return;
    setArchiveBusy(false);
    setArchiveError("");
    setForm(event ? {
      title: event.title || "",
      body: event.body || "",
      location: event.location || "",
      starts_at: localInput(new Date(event.starts_at)),
      ends_at: localInput(new Date(event.ends_at)),
    } : {
      title: context ? "Meeting: " + (context.name || context.title || context.display_name || "CRM follow-up") : "",
      body: "",
      location: "",
      ...defaultMeeting(),
    });
  }, [open, context, event?.id, event?.version]);

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  return (
    <WorkflowDialog
      description={event ? "Update the local event. Google Calendar synchronisation will push the new version safely." : "Create the event locally. Google Calendar synchronisation can catch up whenever the connection is available."}
      onClose={onClose}
      onSubmit={async () => {
        const payload = {
          ...form,
          entity_type: event?.entity_type || context?.entity_type || "",
          entity_id: event?.entity_id || context?.id || undefined,
          timezone: event?.timezone || "Europe/London",
        };
        const saved = event
          ? await api.patch(`calendar/events/${event.id}`, { version: event.version, ...payload })
          : await api.post("calendar/events", payload);
        onCreated?.(saved);
        onSaved?.(saved);
        onClose();
      }}
      open={open}
      submitLabel={event ? "Save event" : "Add event"}
      title={event ? "Edit calendar event" : "Add calendar event"}
      auxiliaryAction={event ? <button className="button button-danger" disabled={archiveBusy} onClick={async () => {
        if (!window.confirm(`Archive ${event.title || "this event"}? The calendar history remains in the audit trail.`)) return;
        setArchiveBusy(true);
        setArchiveError("");
        try {
          const archived = await api.post(`calendar/events/${event.id}/archive`, { version: event.version }, { headers: { "X-CRM-Confirmed": "true" } });
          onArchived?.(archived);
          onClose();
        } catch (error) {
          setArchiveError(error.message);
        } finally {
          setArchiveBusy(false);
        }
      }} type="button"><Archive aria-hidden="true" size={14} /> {archiveBusy ? "Archiving…" : "Archive"}</button> : null}
    >
      <label className="field-wide"><span>Title</span><input onChange={(event) => update("title", event.target.value)} required value={form.title} /></label>
      <label><span>Starts</span><input onChange={(event) => update("starts_at", event.target.value)} required type="datetime-local" value={form.starts_at} /></label>
      <label><span>Ends</span><input min={form.starts_at} onChange={(event) => update("ends_at", event.target.value)} required type="datetime-local" value={form.ends_at} /></label>
      <label><span>Location</span><input onChange={(event) => update("location", event.target.value)} value={form.location} /></label>
      <label className="field-wide"><span>Preparation / notes</span><textarea onChange={(event) => update("body", event.target.value)} rows="4" value={form.body} /></label>
      {archiveError ? <div className="form-error" role="alert"><strong>Could not archive this event.</strong><span>{archiveError}</span></div> : null}
    </WorkflowDialog>
  );
}

function nextDate(days) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return date.toISOString().slice(0, 10);
}

function initialCommercial() {
  return {
    account_id: "",
    opportunity_id: "",
    proposal_id: "",
    project_id: "",
    invoice_id: "",
    title: "",
    amount: "",
    valid_until: nextDate(30),
    starts_on: nextDate(0),
    ends_on: nextDate(90),
    due_on: nextDate(14),
    customer_name: "",
    customer_address: "",
    notes: "",
    method: "bank",
    reference: "",
    lines: [blankCommercialLine()],
  };
}

function LineItemsEditor({ catalog, lines, onChange, vatEnabled }) {
  const total = lines.reduce((sum, line) => sum + commercialLineTotalMinor(line, vatEnabled), 0);

  function updateLine(index, field, value) {
    onChange(lines.map((line, lineIndex) => lineIndex === index ? { ...line, [field]: value } : line));
  }

  function selectCatalog(index, value) {
    const item = catalog.find((candidate) => String(candidate.id) === value);
    if (!item) {
      updateLine(index, "catalog_item_id", "");
      return;
    }
    onChange(lines.map((line, lineIndex) => lineIndex === index ? {
      ...line,
      catalog_item_id: value,
      description: item.description || item.name,
      unit_price: (Number(item.unit_price_pence || 0) / 100).toFixed(2),
      tax_percent: vatEnabled ? String(Number(item.tax_rate_bps || 0) / 100) : "0",
    } : line));
  }

  return (
    <fieldset className="commercial-lines field-wide">
      <legend>Line items</legend>
      <div className="line-items-heading"><span>{lines.length} {lines.length === 1 ? "line" : "lines"}</span><strong>{formatMoney(total)}</strong></div>
      <div className="line-items">
        {lines.map((line, index) => (
          <section className="line-item" key={line.key}>
            <header><strong>Line {index + 1}</strong>{lines.length > 1 ? <button aria-label={`Remove line ${index + 1}`} className="icon-button" onClick={() => onChange(lines.filter((_, lineIndex) => lineIndex !== index))} type="button"><Trash2 aria-hidden="true" size={15} /></button> : null}</header>
            <label className="field-wide"><span>Catalog item (optional)</span><select aria-label={`Catalog item for line ${index + 1}`} onChange={(event) => selectCatalog(index, event.target.value)} value={line.catalog_item_id}><option value="">Custom line</option>{catalog.map((item) => <option key={item.id} value={item.id}>{item.name} · {formatMoney(item.unit_price_pence)} / {item.unit}</option>)}</select></label>
            <label className="field-wide"><span>Line description</span><input aria-label={`Line description for line ${index + 1}`} onChange={(event) => updateLine(index, "description", event.target.value)} required value={line.description} /></label>
            <label><span>Quantity</span><input aria-label={`Quantity for line ${index + 1}`} min="0.0001" onChange={(event) => updateLine(index, "quantity", event.target.value)} required step="0.0001" type="number" value={line.quantity} /></label>
            <label><span>Unit price (£)</span><input aria-label={`Unit price / Amount (£) for line ${index + 1}`} min="0" onChange={(event) => updateLine(index, "unit_price", event.target.value)} required step="0.01" type="number" value={line.unit_price} /></label>
            <label><span>Discount (%)</span><input aria-label={`Discount for line ${index + 1}`} max="100" min="0" onChange={(event) => updateLine(index, "discount_percent", event.target.value)} step="0.01" type="number" value={line.discount_percent} /></label>
            <label><span>VAT rate (%)</span><input aria-label={`VAT rate for line ${index + 1}`} disabled={!vatEnabled} max="100" min="0" onChange={(event) => updateLine(index, "tax_percent", event.target.value)} step="0.01" type="number" value={vatEnabled ? line.tax_percent : "0"} /></label>
            <output aria-label={`Total for line ${index + 1}`}>{formatMoney(commercialLineTotalMinor(line, vatEnabled))}</output>
          </section>
        ))}
      </div>
      <button className="button button-quiet add-line-button" onClick={() => onChange([...lines, blankCommercialLine()])} type="button"><Plus aria-hidden="true" size={15} /> Add line</button>
      {!vatEnabled ? <p className="vat-notice">VAT is locked at 0% until the legal identity, VAT scheme, effective date and approved tax codes are configured in Settings.</p> : null}
    </fieldset>
  );
}

export function CommercialCreateDialog({ open, type, onClose, onCreated }) {
  const [form, setForm] = useState(initialCommercial);
  const { data: accounts } = useResource("accounts");
  const { data: opportunities } = useResource("opportunities");
  const { data: proposals } = useResource("proposals");
  const { data: projects } = useResource("projects");
  const { data: invoices } = useResource("invoices");
  const { data: catalog } = useResource("catalog");
  const { data: business } = useResource("settings/business", { list: false });
  const vatEnabled = vatIsEnabled(business || {});

  useEffect(() => {
    if (open) setForm(initialCommercial());
  }, [open, type]);

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  const labels = {
    proposals: ["New proposal", "Create proposal"],
    contracts: ["New contract", "Create contract"],
    invoices: ["New invoice", "Create draft invoice"],
    payments: ["Record payment", "Record payment"],
  };
  const [title, submitLabel] = labels[type] || labels.proposals;
  const selectedAccount = useMemo(() => accounts.find((account) => String(account.id) === String(form.account_id)), [accounts, form.account_id]);

  async function create() {
    const amount = poundsToMinor(form.amount);
    const lines = form.lines.map((line) => commercialLinePayload(line, vatEnabled));
    let payload;
    if (type === "proposals") {
      payload = { account_id: Number(form.account_id), opportunity_id: form.opportunity_id ? Number(form.opportunity_id) : undefined, title: form.title, valid_until: form.valid_until || undefined, notes: form.notes, currency: "GBP", lines };
    } else if (type === "contracts") {
      payload = { account_id: Number(form.account_id), proposal_id: form.proposal_id ? Number(form.proposal_id) : undefined, opportunity_id: form.opportunity_id ? Number(form.opportunity_id) : undefined, title: form.title, value_pence: amount, currency: "GBP", starts_on: form.starts_on || undefined, ends_on: form.ends_on || undefined, notes: form.notes };
    } else if (type === "invoices") {
      payload = { account_id: Number(form.account_id), project_id: form.project_id ? Number(form.project_id) : undefined, due_on: form.due_on, customer_name: form.customer_name || selectedAccount?.name || "Customer", customer_address: form.customer_address, notes: form.notes, currency: "GBP", lines };
    } else {
      payload = { amount_pence: amount, currency: "GBP", method: form.method, reference: form.reference, invoice_id: form.invoice_id ? Number(form.invoice_id) : undefined };
    }
    const created = await api.post(
      type,
      payload,
      type === "payments" ? { headers: { "X-CRM-Confirmed": "true" } } : undefined,
    );
    onCreated?.(created);
    onClose();
  }

  const requiresAccount = type !== "payments";
  const requiresLine = ["proposals", "invoices"].includes(type);
  const issuedInvoices = invoices.filter((invoice) => !["Draft", "Void", "Paid"].includes(invoice.status));

  return (
    <WorkflowDialog confirmMessage={type === "payments" ? "Record this payment and post its receipt journal?" : ""} description="Enter the commercial essentials now. The record remains editable until its controlled issue or send action." onClose={onClose} onSubmit={create} open={open} submitLabel={submitLabel} title={title}>
      {requiresAccount ? <label><span>Account</span><select onChange={(event) => { update("account_id", event.target.value); if (type === "invoices") update("customer_name", accounts.find((item) => String(item.id) === event.target.value)?.name || ""); }} required value={form.account_id}><option value="">Select account</option>{accounts.map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}</select></label> : null}
      {["proposals", "contracts"].includes(type) ? <label><span>Deal (optional)</span><select onChange={(event) => update("opportunity_id", event.target.value)} value={form.opportunity_id}><option value="">No linked deal</option>{opportunities.filter((item) => !form.account_id || String(item.account_id) === String(form.account_id)).map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label> : null}
      {type === "contracts" ? <label><span>Proposal (optional)</span><select onChange={(event) => update("proposal_id", event.target.value)} value={form.proposal_id}><option value="">No linked proposal</option>{proposals.map((item) => <option key={item.id} value={item.id}>{item.title || item.number}</option>)}</select></label> : null}
      {type === "invoices" ? <label><span>Project (optional)</span><select onChange={(event) => update("project_id", event.target.value)} value={form.project_id}><option value="">No linked project</option>{projects.filter((item) => !form.account_id || String(item.account_id) === String(form.account_id)).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label> : null}
      {type === "payments" ? <label className="field-wide"><span>Invoice allocation (optional)</span><select onChange={(event) => { update("invoice_id", event.target.value); const invoice = issuedInvoices.find((item) => String(item.id) === event.target.value); if (invoice) update("amount", ((invoice.outstanding_pence || 0) / 100).toFixed(2)); }} value={form.invoice_id}><option value="">Leave unallocated</option>{issuedInvoices.map((invoice) => <option key={invoice.id} value={invoice.id}>{invoice.number || "Invoice #" + invoice.id} · £{((invoice.outstanding_pence || 0) / 100).toFixed(2)}</option>)}</select></label> : null}
      {["proposals", "contracts"].includes(type) ? <label className="field-wide"><span>Title</span><input onChange={(event) => update("title", event.target.value)} required value={form.title} /></label> : null}
      {!requiresLine ? <label><span>Amount (£)</span><input min="0.01" onChange={(event) => update("amount", event.target.value)} required step="0.01" type="number" value={form.amount} /></label> : null}
      {type === "proposals" ? <label><span>Valid until</span><input onChange={(event) => update("valid_until", event.target.value)} type="date" value={form.valid_until} /></label> : null}
      {type === "contracts" ? <><label><span>Starts</span><input onChange={(event) => update("starts_on", event.target.value)} type="date" value={form.starts_on} /></label><label><span>Ends</span><input min={form.starts_on} onChange={(event) => update("ends_on", event.target.value)} type="date" value={form.ends_on} /></label></> : null}
      {type === "invoices" ? <><label><span>Customer name</span><input onChange={(event) => update("customer_name", event.target.value)} required value={form.customer_name} /></label><label><span>Due date</span><input onChange={(event) => update("due_on", event.target.value)} required type="date" value={form.due_on} /></label><label className="field-wide"><span>Customer address</span><textarea onChange={(event) => update("customer_address", event.target.value)} rows="3" value={form.customer_address} /></label></> : null}
      {requiresLine ? <LineItemsEditor catalog={catalog} lines={form.lines} onChange={(lines) => update("lines", lines)} vatEnabled={vatEnabled} /> : null}
      {type !== "payments" ? <label className="field-wide"><span>Notes (optional)</span><textarea onChange={(event) => update("notes", event.target.value)} rows="3" value={form.notes} /></label> : null}
      {type === "payments" ? <><label><span>Method</span><select onChange={(event) => update("method", event.target.value)} value={form.method}><option value="bank">Bank transfer</option><option value="card">Card</option><option value="cash">Cash</option><option value="stripe">Stripe</option><option value="other">Other</option></select></label><label><span>Reference</span><input onChange={(event) => update("reference", event.target.value)} value={form.reference} /></label></> : null}
    </WorkflowDialog>
  );
}

function catalogForm(item) {
  return {
    name: item?.name || "",
    description: item?.description || "",
    unit: item?.unit || "item",
    unit_price: item ? (Number(item.unit_price_pence || 0) / 100).toFixed(2) : "",
    tax_percent: item ? String(Number(item.tax_rate_bps || 0) / 100) : "0",
    active: item ? Boolean(item.active) : true,
  };
}

export function CatalogItemDialog({ item, onClose, onSaved, open, vatEnabled }) {
  const [form, setForm] = useState(() => catalogForm(item));
  useEffect(() => { if (open) setForm(catalogForm(item)); }, [open, item]);
  const update = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  return (
    <WorkflowDialog description="Keep reusable services and prices in one place. Proposal and invoice lines remain immutable snapshots after issue." onClose={onClose} onSubmit={async () => {
      const payload = {
        name: form.name,
        description: form.description,
        unit: form.unit,
        unit_price_pence: poundsToMinor(form.unit_price),
        tax_rate_bps: vatEnabled ? Math.round(Number(form.tax_percent || 0) * 100) : 0,
        active: Boolean(form.active),
      };
      const saved = item ? await api.patch(`catalog/${item.id}`, { version: item.version, ...payload }) : await api.post("catalog", payload);
      onSaved(saved);
      onClose();
    }} open={open} submitLabel={item ? "Save item" : "Create item"} title={item ? "Edit catalog item" : "New catalog item"}>
      <label className="field-wide"><span>Name</span><input onChange={(event) => update("name", event.target.value)} required value={form.name} /></label>
      <label className="field-wide"><span>Description</span><textarea onChange={(event) => update("description", event.target.value)} rows="3" value={form.description} /></label>
      <label><span>Unit</span><input onChange={(event) => update("unit", event.target.value)} required value={form.unit} /></label>
      <label><span>Unit price (£)</span><input min="0" onChange={(event) => update("unit_price", event.target.value)} required step="0.01" type="number" value={form.unit_price} /></label>
      <label><span>VAT rate (%)</span><input disabled={!vatEnabled} max="100" min="0" onChange={(event) => update("tax_percent", event.target.value)} step="0.01" type="number" value={vatEnabled ? form.tax_percent : "0"} /></label>
      <label className="checkbox-field"><input checked={form.active} onChange={(event) => update("active", event.target.checked)} type="checkbox" /><span>Available for new documents</span></label>
      {!vatEnabled ? <p className="vat-notice field-wide">VAT is disabled in Settings, so this item will use a 0% tax rate.</p> : null}
    </WorkflowDialog>
  );
}

export function CatalogWorkspace() {
  const catalogState = useResource("catalog", { query: { include_inactive: true } });
  const { data: business } = useResource("settings/business", { list: false });
  const [editing, setEditing] = useState(undefined);
  const [feedback, setFeedback] = useState("");
  const vatEnabled = vatIsEnabled(business || {});

  async function archive(item) {
    if (!window.confirm(`Archive ${item.name}? Existing document snapshots will be preserved.`)) return;
    try {
      await api.post(`catalog/${item.id}/archive`, { version: item.version });
      setFeedback(`${item.name} archived.`);
      catalogState.reload();
    } catch (error) {
      setFeedback(error.message);
    }
  }

  return (
    <section className="catalog-workspace">
      <div className="operations-toolbar"><div><strong>Service catalog</strong><span>{catalogState.data.length} reusable items</span></div><button className="button button-primary" onClick={() => setEditing(null)} type="button"><Plus aria-hidden="true" size={15} /> New catalog item</button></div>
      {feedback ? <p aria-live="polite" className="action-feedback">{feedback}</p> : null}
      {catalogState.loading ? <LoadingState label="Loading service catalog" /> : null}
      {catalogState.error ? <UnavailableState error={catalogState.error} onRetry={catalogState.reload} /> : null}
      {!catalogState.loading && !catalogState.error && !catalogState.data.length ? <EmptyState action={<button className="button button-primary" onClick={() => setEditing(null)} type="button">Create first item</button>} title="Your catalog is ready" message="Add services or products once, then reuse them across proposals and invoices." /> : null}
      <div className="catalog-list">{catalogState.data.map((item) => <article className="catalog-row" key={item.id}><div><strong>{item.name}</strong><span>{item.description || `${item.unit} service`}</span></div><dl><div><dt>Price</dt><dd>{formatMoney(item.unit_price_pence)} / {item.unit}</dd></div><div><dt>VAT</dt><dd>{Number(item.tax_rate_bps || 0) / 100}%</dd></div></dl><Badge tone={item.active ? "positive" : "neutral"}>{item.active ? "Active" : "Inactive"}</Badge><div className="record-workflow-actions"><button className="button button-quiet" onClick={() => setEditing(item)} type="button"><Pencil aria-hidden="true" size={14} /> Edit</button><button aria-label={`Archive ${item.name}`} className="icon-button" onClick={() => archive(item)} type="button"><Archive aria-hidden="true" size={15} /></button></div></article>)}</div>
      <CatalogItemDialog item={editing || undefined} onClose={() => setEditing(undefined)} onSaved={() => { setFeedback(editing ? "Catalog item updated." : "Catalog item created."); catalogState.reload(); }} open={editing !== undefined} vatEnabled={vatEnabled} />
    </section>
  );
}

function initialProject() {
  return { name: "", account_id: "", opportunity_id: "", status: "Planned", billing_type: "fixed", budget: "", starts_on: nextDate(0), due_on: nextDate(30), notes: "" };
}

export function ProjectCreateDialog({ onClose, onCreated, open }) {
  const [form, setForm] = useState(initialProject);
  const { data: accounts } = useResource("accounts");
  const { data: opportunities } = useResource("opportunities");
  useEffect(() => { if (open) setForm(initialProject()); }, [open]);
  const update = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  return (
    <WorkflowDialog description="Create delivery work directly when there is no contract. It can still link to an account and deal." onClose={onClose} onSubmit={async () => {
      const created = await api.post("projects", {
        name: form.name,
        account_id: form.account_id ? Number(form.account_id) : undefined,
        opportunity_id: form.opportunity_id ? Number(form.opportunity_id) : undefined,
        status: form.status,
        billing_type: form.billing_type,
        budget_pence: poundsToMinor(form.budget),
        currency: "GBP",
        starts_on: form.starts_on || undefined,
        due_on: form.due_on || undefined,
        notes: form.notes,
      });
      onCreated(created);
      onClose();
    }} open={open} submitLabel="Create project" title="New project">
      <label className="field-wide"><span>Project name</span><input onChange={(event) => update("name", event.target.value)} required value={form.name} /></label>
      <label><span>Account (optional)</span><select onChange={(event) => update("account_id", event.target.value)} value={form.account_id}><option value="">No linked account</option>{accounts.map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}</select></label>
      <label><span>Deal (optional)</span><select onChange={(event) => update("opportunity_id", event.target.value)} value={form.opportunity_id}><option value="">No linked deal</option>{opportunities.filter((item) => !form.account_id || String(item.account_id) === String(form.account_id)).map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
      <label><span>Status</span><select onChange={(event) => update("status", event.target.value)} value={form.status}>{["Planned", "Active", "Blocked", "Complete", "Cancelled"].map((status) => <option key={status}>{status}</option>)}</select></label>
      <label><span>Billing</span><select onChange={(event) => update("billing_type", event.target.value)} value={form.billing_type}><option value="fixed">Fixed fee</option><option value="milestone">Milestone</option><option value="hourly">Hourly</option><option value="retainer">Retainer</option></select></label>
      <label><span>Budget (£)</span><input min="0" onChange={(event) => update("budget", event.target.value)} step="0.01" type="number" value={form.budget} /></label>
      <label><span>Starts</span><input onChange={(event) => update("starts_on", event.target.value)} type="date" value={form.starts_on} /></label>
      <label><span>Due</span><input min={form.starts_on} onChange={(event) => update("due_on", event.target.value)} type="date" value={form.due_on} /></label>
      <label className="field-wide"><span>Delivery notes</span><textarea onChange={(event) => update("notes", event.target.value)} rows="4" value={form.notes} /></label>
    </WorkflowDialog>
  );
}

function successForm(record) {
  return {
    account_id: record?.account_id || "",
    onboarding_status: record?.onboarding_status || "Not started",
    manual_health: record?.manual_health || "",
    open_risks: String(record?.open_risks || 0),
    next_review_on: record?.next_review_on || "",
    renewal_on: record?.renewal_on || "",
    notes: record?.notes || "",
  };
}

export function ClientSuccessDialog({ onClose, onSaved, open, record }) {
  const [form, setForm] = useState(() => successForm(record));
  const { data: accounts } = useResource("accounts");
  useEffect(() => { if (open) setForm(successForm(record)); }, [open, record]);
  const update = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  return (
    <WorkflowDialog description="Maintain onboarding, operator health judgement, open risks, review cadence and the commercial renewal date." onClose={onClose} onSubmit={async () => {
      const accountId = Number(record?.account_id || form.account_id);
      const saved = await api.put(`client-success/${accountId}`, {
        onboarding_status: form.onboarding_status,
        manual_health: form.manual_health || null,
        open_risks: Number(form.open_risks || 0),
        next_review_on: form.next_review_on || null,
        renewal_on: form.renewal_on || null,
        notes: form.notes,
        ...(record?.version ? { version: record.version } : {}),
      });
      onSaved(saved);
      onClose();
    }} open={open} submitLabel={record ? "Update success plan" : "Create success plan"} title={record ? `Update ${record.account_name || "client"}` : "New client success plan"}>
      {!record ? <label className="field-wide"><span>Account</span><select onChange={(event) => update("account_id", event.target.value)} required value={form.account_id}><option value="">Select account</option>{accounts.map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}</select></label> : null}
      <label><span>Onboarding</span><select onChange={(event) => update("onboarding_status", event.target.value)} value={form.onboarding_status}><option>Not started</option><option>In progress</option><option>Complete</option></select></label>
      <label><span>Manual health</span><select onChange={(event) => update("manual_health", event.target.value)} value={form.manual_health}><option value="">Use computed health</option><option>Healthy</option><option>Watch</option><option>At risk</option></select></label>
      <label><span>Open risks</span><input min="0" onChange={(event) => update("open_risks", event.target.value)} required type="number" value={form.open_risks} /></label>
      <label><span>Next review</span><input onChange={(event) => update("next_review_on", event.target.value)} type="date" value={form.next_review_on} /></label>
      <label><span>Renewal date</span><input onChange={(event) => update("renewal_on", event.target.value)} type="date" value={form.renewal_on} /></label>
      <label className="field-wide"><span>Success notes</span><textarea onChange={(event) => update("notes", event.target.value)} rows="5" value={form.notes} /></label>
    </WorkflowDialog>
  );
}

export function creditLinesForGrossMinor(grossMinor, taxRateBps, description) {
  const gross = Math.max(0, Math.round(Number(grossMinor) || 0));
  const rate = Math.max(0, Math.round(Number(taxRateBps) || 0));
  let net = Math.floor(gross * 10_000 / (10_000 + rate));
  const totalFor = (value) => value + Math.round(value * rate / 10_000);
  while (totalFor(net + 1) <= gross) net += 1;
  while (totalFor(net) > gross) net -= 1;
  const lines = [{ description, quantity: "1", unit_price_pence: net, tax_rate_bps: rate, discount_bps: 0 }];
  const remainder = gross - totalFor(net);
  if (remainder) lines.push({ description: "Credit rounding adjustment", quantity: "1", unit_price_pence: remainder, tax_rate_bps: 0, discount_bps: 0 });
  return lines;
}

export function CreditNoteDialog({ invoice, onClose, onIssued, open }) {
  const outstanding = Number(invoice?.outstanding_pence ?? Math.max(Number(invoice?.total_pence || 0) - Number(invoice?.paid_pence || 0) - Number(invoice?.credited_pence || 0), 0));
  const defaultRate = Number(invoice?.lines?.[0]?.tax_rate_bps || 0) / 100;
  const [form, setForm] = useState({ mode: "full", amount: (outstanding / 100).toFixed(2), tax_percent: String(defaultRate), reason: "" });
  useEffect(() => {
    if (open) setForm({ mode: "full", amount: (outstanding / 100).toFixed(2), tax_percent: String(defaultRate), reason: "" });
  }, [open, outstanding, defaultRate]);
  const update = (field, value) => setForm((current) => ({ ...current, [field]: value }));
  return (
    <WorkflowDialog confirmMessage={`Issue this credit note for £${form.amount || "0.00"}? This posts an immutable reversing journal entry.`} description={`Credit up to the outstanding ${formatMoney(outstanding)}. Full credit clears the balance; partial credit leaves the remainder collectible.`} onClose={onClose} onSubmit={async () => {
      const amount = form.mode === "full" ? outstanding : poundsToMinor(form.amount);
      if (amount <= 0 || amount > outstanding) throw new Error("Credit amount must be greater than zero and no more than the outstanding balance.");
      const created = await api.post("credit-notes", {
        invoice_id: invoice.id,
        reason: form.reason,
        lines: creditLinesForGrossMinor(amount, Math.round(Number(form.tax_percent || 0) * 100), `Credit for ${invoice.number || `invoice #${invoice.id}`}`),
      });
      const issued = await api.post(`credit-notes/${created.id}/issue`, {}, { headers: { "X-CRM-Confirmed": "true" } });
      onIssued(issued.credit_note || created);
      onClose();
    }} open={open} submitLabel="Create & issue credit note" title="Credit invoice">
      <label><span>Credit type</span><select onChange={(event) => { const mode = event.target.value; setForm((current) => ({ ...current, mode, amount: mode === "full" ? (outstanding / 100).toFixed(2) : "" })); }} value={form.mode}><option value="full">Full outstanding balance</option><option value="partial">Partial credit</option></select></label>
      <label><span>Credit total (£)</span><input disabled={form.mode === "full"} max={(outstanding / 100).toFixed(2)} min="0.01" onChange={(event) => update("amount", event.target.value)} required step="0.01" type="number" value={form.amount} /></label>
      <label><span>VAT rate (%)</span><input max="100" min="0" onChange={(event) => update("tax_percent", event.target.value)} step="0.01" type="number" value={form.tax_percent} /></label>
      <label className="field-wide"><span>Reason</span><textarea onChange={(event) => update("reason", event.target.value)} required rows="4" value={form.reason} /></label>
    </WorkflowDialog>
  );
}
