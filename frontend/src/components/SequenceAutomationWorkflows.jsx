import React, { useEffect, useState } from "react";
import { Ban, FlaskConical, MessageSquareReply, Pause, Pencil, Play, Plus, Power, RefreshCw, RotateCcw, ShieldAlert, UserPlus } from "lucide-react";

import { api, unwrapList } from "../api";
import { useResource } from "../hooks";
import { formatDate, titleCase } from "../utils/format";
import { Badge, EmptyState, LoadingState, PageControls, UnavailableState } from "./common";
import { WorkflowDialog } from "./WorkflowDialogs";

export const AUTOMATION_TRIGGERS = [
  "lead.created",
  "lead.qualified",
  "tender.qualified",
  "deal.stage_changed",
  "proposal.accepted",
  "contract.activated",
  "project.blocked",
  "invoice.overdue",
  "payment.received",
  "renewal.due",
];

const EMPTY_SEQUENCE = {
  name: "",
  description: "",
  timezone: "Europe/London",
  send_window_start: "09:00",
  send_window_end: "17:00",
  daily_cap: "40",
};

const EMPTY_STEP = {
  step_type: "email",
  subject: "",
  body_text: "",
  delay_minutes: "1440",
  task_title: "",
  task_description: "",
};

const EMPTY_AUTOMATION = {
  name: "",
  trigger_name: "lead.created",
  conditions: "[]",
  actions: '[{"type":"create_task","params":{"title":"Follow up"}}]',
  enabled: false,
  dry_run: true,
};

function jsonArray(value, label) {
  let parsed;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error(`${label} must be valid JSON.`);
  }
  if (!Array.isArray(parsed)) throw new Error(`${label} must be a JSON array.`);
  return parsed;
}

export function buildSequencePayload(form) {
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    timezone: form.timezone,
    send_window_start: form.send_window_start,
    send_window_end: form.send_window_end,
    daily_cap: Number(form.daily_cap),
  };
}

export function buildAutomationPayload(form, version) {
  return {
    name: form.name.trim(),
    trigger_name: form.trigger_name,
    conditions: jsonArray(form.conditions, "Conditions"),
    actions: jsonArray(form.actions, "Actions"),
    enabled: Boolean(form.enabled),
    dry_run: Boolean(form.dry_run),
    ...(version === undefined ? {} : { version }),
  };
}

export function parsePreviewRecords(value) {
  let parsed;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error("Sample record must be valid JSON.");
  }
  const records = Array.isArray(parsed) ? parsed : [parsed];
  if (!records.length || records.some((item) => !item || typeof item !== "object" || Array.isArray(item))) {
    throw new Error("Provide one record object or an array of record objects.");
  }
  return records;
}

function useForm(open, value, fallback) {
  const [form, setForm] = useState(fallback);
  useEffect(() => {
    if (open) setForm(value || fallback);
  }, [open]); // Reset only when the dialog opens; parent reloads supply the next version.
  return [form, (field, next) => setForm((current) => ({ ...current, [field]: next }))];
}

function sequenceForm(sequence) {
  if (!sequence) return EMPTY_SEQUENCE;
  return {
    name: sequence.name || "",
    description: sequence.description || "",
    timezone: sequence.timezone || "Europe/London",
    send_window_start: sequence.send_window_start || "09:00",
    send_window_end: sequence.send_window_end || "17:00",
    daily_cap: String(sequence.daily_cap || 40),
  };
}

export function SequenceDialog({ open, sequence, onClose, onSaved }) {
  const initial = sequenceForm(sequence);
  const [form, update] = useForm(open, initial, EMPTY_SEQUENCE);
  const editing = Boolean(sequence?.id);

  async function save() {
    const payload = buildSequencePayload(form);
    const result = editing
      ? await api.patch(`sequences/${sequence.id}`, { ...payload, version: sequence.version })
      : await api.post("sequences", payload);
    await onSaved?.(result);
    onClose();
  }

  return (
    <WorkflowDialog
      description="Set the weekday delivery window and cap. New sequences stay in draft until they have a valid step and are explicitly activated."
      onClose={onClose}
      onSubmit={save}
      open={open}
      submitLabel={editing ? "Save sequence" : "Create draft"}
      title={editing ? "Edit sequence" : "New sequence"}
    >
      <label className="field-wide"><span>Name</span><input onChange={(event) => update("name", event.target.value)} required value={form.name} /></label>
      <label className="field-wide"><span>Description</span><textarea onChange={(event) => update("description", event.target.value)} rows="3" value={form.description} /></label>
      <label><span>Time zone</span><input onChange={(event) => update("timezone", event.target.value)} required value={form.timezone} /></label>
      <label><span>Daily send cap</span><input max="500" min="1" onChange={(event) => update("daily_cap", event.target.value)} required type="number" value={form.daily_cap} /></label>
      <label><span>Send from</span><input onChange={(event) => update("send_window_start", event.target.value)} required type="time" value={form.send_window_start} /></label>
      <label><span>Send until</span><input onChange={(event) => update("send_window_end", event.target.value)} required type="time" value={form.send_window_end} /></label>
    </WorkflowDialog>
  );
}

function sequenceStepForm(step) {
  if (!step) return EMPTY_STEP;
  return {
    step_type: step.step_type || "email",
    subject: step.subject || "",
    body_text: step.body_text || "",
    delay_minutes: String(step.delay_minutes ?? 0),
    task_title: step.task_title || "",
    task_description: step.task_description || "",
  };
}

export function SequenceStepDialog({ open, sequence, step, onClose, onSaved }) {
  const editing = Boolean(step?.id);
  const [form, update] = useForm(open, sequenceStepForm(step), EMPTY_STEP);

  async function save() {
    const common = editing ? { version: step.version } : { step_type: form.step_type };
    const payload = form.step_type === "email"
      ? { ...common, subject: form.subject, body_text: form.body_text }
      : form.step_type === "delay"
        ? { ...common, delay_minutes: Number(form.delay_minutes) }
        : { ...common, task_title: form.task_title, task_description: form.task_description };
    const result = editing
      ? await api.patch(`sequences/${sequence.id}/steps/${step.id}`, payload)
      : await api.post(`sequences/${sequence.id}/steps`, payload);
    await onSaved?.(result);
    onClose();
  }

  return (
    <WorkflowDialog description="Email content is previewable before activation; delays and manual tasks never send externally." onClose={onClose} onSubmit={save} open={open} submitLabel={editing ? "Save step" : "Add step"} title={editing ? "Edit sequence step" : "Add sequence step"}>
      <label className="field-wide"><span>Step type</span><select disabled={editing} onChange={(event) => update("step_type", event.target.value)} value={form.step_type}><option value="email">Email</option><option value="delay">Delay</option><option value="manual_task">Manual task</option></select></label>
      {form.step_type === "email" ? <><label className="field-wide"><span>Subject</span><input onChange={(event) => update("subject", event.target.value)} required value={form.subject} /></label><label className="field-wide"><span>Message</span><textarea onChange={(event) => update("body_text", event.target.value)} required rows="7" value={form.body_text} /></label></> : null}
      {form.step_type === "delay" ? <label className="field-wide"><span>Delay (minutes)</span><input max="525600" min="0" onChange={(event) => update("delay_minutes", event.target.value)} required type="number" value={form.delay_minutes} /></label> : null}
      {form.step_type === "manual_task" ? <><label className="field-wide"><span>Task title</span><input onChange={(event) => update("task_title", event.target.value)} required value={form.task_title} /></label><label className="field-wide"><span>Task detail</span><textarea onChange={(event) => update("task_description", event.target.value)} rows="4" value={form.task_description} /></label></> : null}
    </WorkflowDialog>
  );
}

export function SequenceStepControl({ sequence, step, onChanged }) {
  const [open, setOpen] = useState(false);
  return <><button className="text-button" onClick={() => setOpen(true)} type="button"><Pencil aria-hidden="true" size={13} /> Edit step</button><SequenceStepDialog onClose={() => setOpen(false)} onSaved={onChanged} open={open} sequence={sequence} step={step} /></>;
}

function SequenceActivateDialog({ open, sequence, onClose, onSaved }) {
  async function activate() {
    const result = await api.post(
      `sequences/${sequence.id}/activate`,
      { version: sequence.version },
      { headers: { "X-CRM-Confirmed": "true" } },
    );
    await onSaved?.(result);
    onClose();
  }

  return (
    <WorkflowDialog confirmMessage="Activate this sequence? Future enrollments can schedule approved email steps automatically." description="Activation locks step editing and permits enrollment. Replies, bounces and opt-outs stop recipients automatically." onClose={onClose} onSubmit={activate} open={open} submitLabel="Activate sequence" title="Activate sequence">
      <p>{sequence?.steps?.length || 0} step{sequence?.steps?.length === 1 ? "" : "s"} will become active.</p>
    </WorkflowDialog>
  );
}

function SequenceEnrollDialog({ open, sequence, onClose, onSaved }) {
  const { data: contacts, error: contactsError } = useResource("contacts");
  const empty = { contact_id: "", email: "", start_at: "" };
  const [form, update] = useForm(open, empty, empty);

  async function enroll() {
    const result = await api.post(`sequences/${sequence.id}/enrollments`, {
      contact_id: form.contact_id ? Number(form.contact_id) : undefined,
      email: form.email || undefined,
      start_at: form.start_at || undefined,
    });
    await onSaved?.(result);
    onClose();
  }

  return (
    <WorkflowDialog confirmMessage="Enroll this recipient and schedule the sequence?" description="Choose a CRM contact or enter an email address. Suppressed, opted-out and already-active recipients are rejected." onClose={onClose} onSubmit={enroll} open={open} submitLabel="Enroll recipient" title="Enroll in sequence">
      <label className="field-wide"><span>CRM contact</span><select aria-describedby={contactsError ? "sequence-contact-error" : undefined} onChange={(event) => update("contact_id", event.target.value)} value={form.contact_id}><option value="">Use an email address instead</option>{contacts.map((contact) => <option key={contact.id} value={contact.id}>{contact.display_name || [contact.first_name, contact.last_name].filter(Boolean).join(" ") || contact.email}</option>)}</select></label>
      {contactsError ? <p className="form-error" id="sequence-contact-error">Contacts could not be loaded. Enter an email address below.</p> : null}
      <label className="field-wide"><span>Email {form.contact_id ? "override (optional)" : "address"}</span><input onChange={(event) => update("email", event.target.value)} required={!form.contact_id} type="email" value={form.email} /></label>
      <label className="field-wide"><span>Start at (optional)</span><input onChange={(event) => update("start_at", event.target.value)} type="datetime-local" value={form.start_at} /></label>
    </WorkflowDialog>
  );
}

export function SequenceWorkflowActions({ sequence, onChanged, onEnrollmentChanged }) {
  const [dialog, setDialog] = useState("");
  const state = sequence?.state || sequence?.status;
  const changed = async (result) => onChanged?.(result);
  return (
    <>
      <button className="button button-quiet" onClick={() => setDialog("edit")} type="button"><Pencil aria-hidden="true" size={15} /> Edit</button>
      {state === "Draft" ? <button className="button button-quiet" onClick={() => setDialog("step")} type="button"><Plus aria-hidden="true" size={15} /> Add step</button> : null}
      {state === "Draft" ? <button className="button button-primary" onClick={() => setDialog("activate")} type="button"><Play aria-hidden="true" size={15} /> Activate</button> : null}
      {state === "Active" ? <button className="button button-primary" onClick={() => setDialog("enroll")} type="button"><UserPlus aria-hidden="true" size={15} /> Enroll</button> : null}
      <SequenceDialog onClose={() => setDialog("")} onSaved={changed} open={dialog === "edit"} sequence={sequence} />
      <SequenceStepDialog onClose={() => setDialog("")} onSaved={changed} open={dialog === "step"} sequence={sequence} />
      <SequenceActivateDialog onClose={() => setDialog("")} onSaved={changed} open={dialog === "activate"} sequence={sequence} />
      <SequenceEnrollDialog onClose={() => setDialog("")} onSaved={async (result) => { await changed(result); onEnrollmentChanged?.(result); }} open={dialog === "enroll"} sequence={sequence} />
    </>
  );
}

const enrollmentActions = {
  pause: { label: "Pause", icon: Pause, states: ["Active"], confirm: "Pause this enrollment and its queued messages?" },
  resume: { label: "Resume", icon: RotateCcw, states: ["Paused"], confirm: "Resume this enrollment in the next valid delivery window?" },
  cancel: { label: "Cancel", icon: Ban, states: ["Active", "Paused"], confirm: "Cancel this enrollment and all pending work?" },
  reply: { label: "Reply received", icon: MessageSquareReply, states: ["Active", "Paused"], confirm: "Record a reply and stop this enrollment?" },
  bounce: { label: "Bounced", icon: ShieldAlert, states: ["Active", "Paused"], confirm: "Record a bounce and stop this enrollment?" },
};

function EnrollmentActionDialog({ action, enrollment, onClose, onSaved, open }) {
  const [reason, setReason] = useState("");
  useEffect(() => {
    if (open) setReason("");
  }, [open, action]);
  const definition = enrollmentActions[action];
  if (!definition) return null;
  return (
    <WorkflowDialog confirmMessage={definition.confirm} description="The enrollment version prevents stale controls from changing newer delivery state." onClose={onClose} onSubmit={async () => {
      const saved = await api.post(
        `sequences/enrollments/${enrollment.id}/${action}`,
        { version: enrollment.version, reason },
        { idempotencyKey: `sequence:${enrollment.id}:${action}:${enrollment.version}` },
      );
      await onSaved(saved);
      onClose();
    }} open={open} submitLabel={definition.label} title={`${definition.label}: ${enrollment.email}`}>
      <label className="field-wide"><span>Reason {["cancel", "reply", "bounce"].includes(action) ? "" : "(optional)"}</span><textarea onChange={(event) => setReason(event.target.value)} required={["cancel", "reply", "bounce"].includes(action)} rows="4" value={reason} /></label>
    </WorkflowDialog>
  );
}

function EnrollmentControls({ enrollment, onChanged }) {
  const [action, setAction] = useState("");
  return (
    <>
      <div className="record-workflow-actions">
        {Object.entries(enrollmentActions).filter(([, definition]) => definition.states.includes(enrollment.state)).map(([key, definition]) => {
          const Icon = definition.icon;
          return <button className={key === "cancel" ? "button button-danger" : "button button-quiet"} key={key} onClick={() => setAction(key)} type="button"><Icon aria-hidden="true" size={13} /> {definition.label}</button>;
        })}
      </div>
      <EnrollmentActionDialog action={action} enrollment={enrollment} onClose={() => setAction("")} onSaved={onChanged} open={Boolean(action)} />
    </>
  );
}

export function SequenceEnrollmentsPanel({ sequenceId, refreshToken = 0 }) {
  const [state, setState] = useState("");
  const enrollmentsState = useResource(`sequences/${sequenceId}/enrollments`, { query: { state: state || undefined, limit: 20 }, pageSize: 20 });

  useEffect(() => {
    if (refreshToken) enrollmentsState.reload();
  }, [refreshToken]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section className="record-section sequence-enrollments">
      <header><div><h2>Enrollments</h2><span>Recipient delivery state</span></div><div className="section-header-actions"><label><span className="sr-only">Filter enrollment state</span><select onChange={(event) => setState(event.target.value)} value={state}><option value="">All states</option>{["Active", "Paused", "Completed", "Cancelled", "Replied", "Bounced", "Opted out"].map((item) => <option key={item}>{item}</option>)}</select></label><button aria-label="Refresh enrollments" className="icon-button" onClick={enrollmentsState.reload} type="button"><RefreshCw aria-hidden="true" size={14} /></button></div></header>
      {enrollmentsState.loading ? <LoadingState label="Loading sequence enrollments" /> : null}
      {enrollmentsState.error ? <UnavailableState compact error={enrollmentsState.error} onRetry={enrollmentsState.reload} /> : null}
      {!enrollmentsState.loading && !enrollmentsState.error && !enrollmentsState.data.length ? <EmptyState icon={UserPlus} title="No matching enrollments" message="Enroll a recipient from the sequence actions. Suppressions are checked before anything is queued." /> : null}
      <div className="enrollment-list">
        {enrollmentsState.data.map((enrollment) => <article className="enrollment-row" key={enrollment.id}><div><span><strong>{enrollment.email}</strong><Badge>{titleCase(enrollment.state)}</Badge></span><p>{enrollment.stopped_reason || (enrollment.next_action_at ? `Next action ${formatDate(enrollment.next_action_at, { withTime: true })}` : `Enrolled ${formatDate(enrollment.created_at, { withTime: true })}`)}</p></div><EnrollmentControls enrollment={enrollment} onChanged={async () => enrollmentsState.reload()} /></article>)}
      </div>
      <PageControls hasNext={enrollmentsState.hasNext} hasPrevious={enrollmentsState.hasPrevious} label="Sequence enrollments" nextPage={enrollmentsState.nextPage} page={enrollmentsState.page} previousPage={enrollmentsState.previousPage} />
    </section>
  );
}

function automationForm(rule) {
  if (!rule) return EMPTY_AUTOMATION;
  return {
    name: rule.name || "",
    trigger_name: rule.trigger_name || "lead.created",
    conditions: JSON.stringify(rule.conditions || [], null, 2),
    actions: JSON.stringify(rule.actions || [], null, 2),
    enabled: Boolean(rule.enabled),
    dry_run: Boolean(rule.dry_run),
  };
}

export function AutomationDialog({ open, rule, onClose, onSaved }) {
  const initial = automationForm(rule);
  const [form, update] = useForm(open, initial, EMPTY_AUTOMATION);
  const editing = Boolean(rule?.id);

  async function save() {
    const payload = buildAutomationPayload(form, editing ? rule.version : undefined);
    const result = editing
      ? await api.patch(`automations/${rule.id}`, payload)
      : await api.post("automations", { ...payload, enabled: false, dry_run: true });
    await onSaved?.(result);
    onClose();
  }

  return (
    <WorkflowDialog description={editing ? "Update the allowlisted trigger, typed conditions and actions. Optimistic version checks prevent overwriting newer edits." : "New rules are disabled and remain in dry-run mode until previewed and deliberately enabled."} onClose={onClose} onSubmit={save} open={open} submitLabel={editing ? "Save rule" : "Create dry-run rule"} title={editing ? "Edit automation" : "New automation"}>
      <label className="field-wide"><span>Name</span><input onChange={(event) => update("name", event.target.value)} required value={form.name} /></label>
      <label className="field-wide"><span>Trigger</span><select onChange={(event) => update("trigger_name", event.target.value)} value={form.trigger_name}>{AUTOMATION_TRIGGERS.map((trigger) => <option key={trigger}>{trigger}</option>)}</select></label>
      <label className="field-wide"><span>Conditions (JSON array)</span><textarea aria-describedby="automation-condition-help" onChange={(event) => update("conditions", event.target.value)} rows="5" spellCheck="false" value={form.conditions} /><small id="automation-condition-help">Example: {`[{"field":"status","operator":"equals","value":"New"}]`}</small></label>
      <label className="field-wide"><span>Actions (JSON array)</span><textarea aria-describedby="automation-action-help" onChange={(event) => update("actions", event.target.value)} required rows="6" spellCheck="false" value={form.actions} /><small id="automation-action-help">Only allowlisted CRM actions are accepted; code, SQL, commands and URLs are rejected.</small></label>
      {editing ? <><label className="checkbox-field"><input checked={form.enabled} onChange={(event) => update("enabled", event.target.checked)} type="checkbox" /><span>Rule enabled</span></label><label className="checkbox-field"><input checked={form.dry_run} onChange={(event) => update("dry_run", event.target.checked)} type="checkbox" /><span>Dry-run only</span></label></> : null}
    </WorkflowDialog>
  );
}

function AutomationPreviewDialog({ open, rule, onClose }) {
  const [sample, setSample] = useState('{"id":"sample-1","status":"New"}');
  const [results, setResults] = useState([]);
  useEffect(() => {
    if (open) setResults([]);
  }, [open]);

  async function preview() {
    const payload = await api.post(`automations/${rule.id}/preview`, { records: parsePreviewRecords(sample) });
    setResults(unwrapList(payload));
  }

  return (
    <WorkflowDialog description="Preview evaluates sample records without executing actions or changing CRM data." onClose={onClose} onSubmit={preview} open={open} submitLabel="Run preview" title="Preview automation">
      <label className="field-wide"><span>Sample record JSON</span><textarea onChange={(event) => setSample(event.target.value)} required rows="7" spellCheck="false" value={sample} /></label>
      {results.length ? <output aria-live="polite" className="field-wide"><strong>Preview result</strong><pre>{JSON.stringify(results, null, 2)}</pre></output> : null}
    </WorkflowDialog>
  );
}

function AutomationEnableDialog({ open, rule, onClose, onSaved }) {
  const enabling = !rule?.enabled;
  async function updateState() {
    const result = await api.patch(`automations/${rule.id}`, {
      name: rule.name,
      trigger_name: rule.trigger_name,
      conditions: rule.conditions || [],
      actions: rule.actions || [],
      enabled: enabling,
      dry_run: Boolean(rule.dry_run),
      version: rule.version,
    });
    await onSaved?.(result);
    onClose();
  }
  return (
    <WorkflowDialog confirmMessage={enabling ? `Enable ${rule?.dry_run ? "this dry-run rule" : "this live rule"}?` : "Disable this automation rule?"} description={enabling ? (rule?.dry_run ? "Matched records will be logged without executing actions." : "Matched records can execute the configured CRM actions.") : "Future events will no longer evaluate this rule."} onClose={onClose} onSubmit={updateState} open={open} submitLabel={enabling ? "Enable rule" : "Disable rule"} title={enabling ? "Enable automation" : "Disable automation"}>
      <p>{rule?.name}</p>
    </WorkflowDialog>
  );
}

export function AutomationWorkflowActions({ rule, onChanged }) {
  const [dialog, setDialog] = useState("");
  const changed = async (result) => onChanged?.(result);
  return (
    <>
      <button className="button button-quiet" onClick={() => setDialog("edit")} type="button"><Pencil aria-hidden="true" size={15} /> Edit</button>
      <button className="button button-quiet" onClick={() => setDialog("preview")} type="button"><FlaskConical aria-hidden="true" size={15} /> Preview</button>
      <button className={`button ${rule?.enabled ? "button-quiet" : "button-primary"}`} onClick={() => setDialog("enable")} type="button"><Power aria-hidden="true" size={15} /> {rule?.enabled ? "Disable" : "Enable"}</button>
      <AutomationDialog onClose={() => setDialog("")} onSaved={changed} open={dialog === "edit"} rule={rule} />
      <AutomationPreviewDialog onClose={() => setDialog("")} open={dialog === "preview"} rule={rule} />
      <AutomationEnableDialog onClose={() => setDialog("")} onSaved={changed} open={dialog === "enable"} rule={rule} />
    </>
  );
}
