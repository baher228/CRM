import React, { useEffect, useMemo, useState } from "react";
import { Archive, Columns3, Merge, Pencil, Plus, RefreshCw, RotateCcw, Save, Settings2, Tags, X } from "lucide-react";

import { api } from "../api";
import { useResource } from "../hooks";
import { recordName, titleCase } from "../utils/format";
import { AppDialog, Badge, RecordCard } from "./common";

export const archiveResources = {
  accounts: "accounts",
  contacts: "contacts",
  leads: "leads",
  tenders: "tenders",
  opportunities: "opportunities",
  projects: "projects",
  "time-entries": "time-entries",
  proposals: "proposals",
  contracts: "contracts",
  tasks: "tasks",
};

const restorableResources = new Set(["accounts", "contacts"]);
const mergeableResources = new Set(["accounts", "contacts"]);

function confirmedOptions() {
  return { headers: { "X-CRM-Confirmed": "true" } };
}

export function SavedViewControls({
  activeId,
  columns,
  config,
  onApply,
  onSave,
  onToggleColumn,
  savedViews,
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await onSave(name.trim());
      setName("");
      setOpen(false);
    } catch (caught) {
      setError(caught.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <label className="select-field saved-view-select">
        <Settings2 aria-hidden="true" size={15} />
        <span className="sr-only">Saved view</span>
        <select aria-label={`Saved ${config.title} view`} onChange={(event) => onApply(event.target.value)} value={activeId}>
          <option value="">Current view</option>
          {savedViews.map((view) => <option key={view.id} value={view.id}>{view.name}</option>)}
        </select>
      </label>
      <details className="column-menu">
        <summary className="button button-quiet"><Columns3 aria-hidden="true" size={15} /> Columns</summary>
        <fieldset>
          <legend>Visible card columns</legend>
          {config.fields.map(([label]) => (
            <label key={label}>
              <input checked={columns.includes(label)} onChange={() => onToggleColumn(label)} type="checkbox" />
              <span>{label}</span>
            </label>
          ))}
        </fieldset>
      </details>
      <button className="button button-quiet" onClick={() => setOpen(true)} type="button"><Save aria-hidden="true" size={15} /> Save view</button>
      <AppDialog description="Save the current search, status, archive visibility and columns as a reusable local view." onClose={() => setOpen(false)} open={open} title={`Save ${config.singular} view`}>
        <form className="workflow-form" onSubmit={submit}>
          <div className="workflow-fields">
            <label className="field-wide"><span>View name</span><input autoComplete="off" onChange={(event) => setName(event.target.value)} required value={name} /></label>
          </div>
          {error ? <p className="form-error" role="alert">{error}</p> : null}
          <footer><span>Saved views are available only in this local workspace.</span><button className="button button-primary" disabled={busy} type="submit">{busy ? "Saving…" : "Save view"}</button></footer>
        </form>
      </AppDialog>
    </>
  );
}

function inlineValue(record, field) {
  const value = record?.[field.key];
  if (field.editor === "money") return value === null || value === undefined ? "" : (Number(value) / 100).toFixed(2);
  if (field.editor === "percent") return value === null || value === undefined ? "" : String(Number(value) / 100);
  if (field.editor === "datetime-local") return value ? String(value).slice(0, 16) : "";
  if (field.editor === "date") return value ? String(value).slice(0, 10) : "";
  if (field.editor === "checkbox") return Boolean(value);
  return value ?? "";
}

export function initialInlineValues(record, fields = []) {
  return Object.fromEntries(fields.map((field) => [field.key, inlineValue(record, field)]));
}

function serializeInlineValue(value, field) {
  if (field.editor === "checkbox") return Boolean(value);
  if (value === "" && Object.hasOwn(field, "emptyValue")) return field.emptyValue;
  if (value === "" && ["date", "datetime-local"].includes(field.editor)) return null;
  if (field.editor === "money" || field.editor === "percent") return Math.round(Number(value || 0) * 100);
  if (field.editor === "number") return Number(value);
  return typeof value === "string" ? value.trim() : value;
}

export function serializeInlineValues(values, fields = []) {
  return Object.fromEntries(fields.map((field) => [field.key, serializeInlineValue(values[field.key], field)]));
}

function InlineField({ field, value, onChange, error, disabled, recordId }) {
  const id = `inline-${recordId}-${field.key}`;
  const errorId = `${id}-error`;
  const shared = {
    "aria-describedby": error ? errorId : undefined,
    "aria-invalid": Boolean(error),
    disabled,
    id,
    name: field.key,
    onChange: (event) => onChange(field.editor === "checkbox" ? event.target.checked : event.target.value),
    required: field.required,
  };
  let control;
  if (field.editor === "select") {
    control = <select {...shared} value={value}>{field.options.map((option) => <option key={option} value={option}>{titleCase(option)}</option>)}</select>;
  } else if (field.editor === "textarea") {
    control = <textarea {...shared} rows="3" value={value} />;
  } else if (field.editor === "checkbox") {
    return <label className="inline-checkbox" htmlFor={id}><input {...shared} checked={Boolean(value)} type="checkbox" /><span>{field.label}</span>{error ? <small id={errorId}>{error}</small> : null}</label>;
  } else {
    const type = field.editor === "money" || field.editor === "percent" ? "number" : field.editor;
    control = <input {...shared} max={field.max} min={field.min} step={field.editor === "money" ? "0.01" : field.editor === "percent" ? "0.1" : undefined} type={type} value={value} />;
  }
  return <label htmlFor={id}><span>{field.label}{field.editor === "money" ? " (£)" : field.editor === "percent" ? " (%)" : ""}</span>{control}{error ? <small id={errorId}>{error}</small> : null}</label>;
}

export function InlineRecordEditor({ config, onCancel, onReload, onSaved, record }) {
  const fields = config.editableFields || [];
  const [values, setValues] = useState(() => initialInlineValues(record, fields));
  const [activeRecord, setActiveRecord] = useState(record);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    setActiveRecord(record);
    setValues(initialInlineValues(record, fields));
    setError(null);
  }, [record.id, record.version]); // eslint-disable-line react-hooks/exhaustive-deps

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const saved = await api.patch(`${config.endpoint}/${record.id}`, {
        version: activeRecord.version,
        ...serializeInlineValues(values, fields),
      });
      onSaved(saved);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  async function reloadLatest() {
    setBusy(true);
    try {
      const latest = error?.currentRecord || await api.get(`${config.endpoint}/${record.id}`);
      setActiveRecord(latest);
      setValues(initialInlineValues(latest, fields));
      setError(null);
    } catch (caught) {
      setError(caught);
      await onReload?.();
    } finally {
      setBusy(false);
    }
  }

  const fieldErrors = error?.fieldErrors || {};
  const conflict = error?.status === 409;
  return (
    <form aria-busy={busy} className="record-card inline-record-editor" onSubmit={submit}>
      <header>
        <div><strong>Edit {recordName(activeRecord, config.singular)}</strong><span>Version {activeRecord.version}</span></div>
        <button aria-label={`Cancel editing ${recordName(record, config.singular)}`} className="icon-button" disabled={busy} onClick={onCancel} type="button"><X aria-hidden="true" size={16} /></button>
      </header>
      <div className="inline-edit-fields">
        {fields.map((field) => <InlineField disabled={busy || field.editable === false} error={fieldErrors[field.key] || fieldErrors[`body.${field.key}`]} field={field} key={field.key} onChange={(value) => setValues((current) => ({ ...current, [field.key]: value }))} recordId={activeRecord.id} value={values[field.key]} />)}
      </div>
      {error ? <div className="inline-edit-error" role="alert"><strong>{conflict ? "This record changed elsewhere." : "Changes were not saved."}</strong><span>{conflict ? `The server is now at version ${error.currentVersion || "newer"}. Reload it before trying again.` : error.message}</span>{error.requestId ? <small>Request {error.requestId}</small> : null}</div> : null}
      <footer>
        {conflict ? <button className="button button-quiet" disabled={busy} onClick={reloadLatest} type="button"><RefreshCw aria-hidden="true" size={14} /> Reload latest</button> : <span />}
        <div><button className="button button-quiet" disabled={busy} onClick={onCancel} type="button">Cancel</button><button className="button button-primary" disabled={busy} type="submit">{busy ? "Savingâ€¦" : "Save changes"}</button></div>
      </footer>
    </form>
  );
}

export function EditableRecordCard({ config, onReload, onSaved, record }) {
  const [editing, setEditing] = useState(false);
  const editable = Boolean(config.editableFields?.length)
    && !record.archived_at
    && (!config.editableWhen || config.editableWhen(record));
  if (editing) {
    return <InlineRecordEditor config={config} onCancel={() => setEditing(false)} onReload={onReload} onSaved={(saved) => { onSaved(saved); setEditing(false); }} record={record} />;
  }
  return (
    <div className="editable-record">
      <RecordCard config={config} record={record} />
      {editable ? <button aria-label={`Edit ${recordName(record, config.singular)}`} className="button button-quiet record-edit" onClick={() => setEditing(true)} type="button"><Pencil aria-hidden="true" size={13} /> Edit</button> : null}
    </div>
  );
}

export function SelectableRecordGrid({ columns, config, onReload, onRestore, onSaved, onSelect, records, selected }) {
  const visibleConfig = useMemo(
    () => ({ ...config, fields: config.fields.filter(([label]) => columns.includes(label)) }),
    [columns, config],
  );
  return (
    <div className="record-grid">
      {records.map((record) => {
        const archived = Boolean(record.archived_at);
        const title = recordName(record, `Untitled ${config.singular}`);
        return (
          <div className={`selectable-record ${selected.has(record.id) ? "is-selected" : ""} ${archived ? "is-archived" : ""}`} key={record.id}>
            <label className="record-selector">
              <input
                aria-label={`Select ${title}`}
                checked={selected.has(record.id)}
                disabled={archived}
                onChange={(event) => onSelect(record.id, event.target.checked)}
                type="checkbox"
              />
            </label>
            <EditableRecordCard config={visibleConfig} onReload={onReload} onSaved={onSaved} record={record} />
            {archived && onRestore ? <button className="button button-quiet record-restore" onClick={() => onRestore(record)} type="button"><RotateCcw aria-hidden="true" size={14} /> Restore</button> : null}
          </div>
        );
      })}
    </div>
  );
}

function CustomFieldInput({ field, value, onChange, disabled }) {
  const id = `custom-field-${field.id}`;
  const options = Array.isArray(field.options) ? field.options : [];
  if (field.field_type === "boolean") {
    return <label className="checkbox-field" htmlFor={id}><input checked={Boolean(value)} disabled={disabled} id={id} onChange={(event) => onChange(event.target.checked)} type="checkbox" /><span>{field.name}</span></label>;
  }
  if (field.field_type === "select") {
    return <label htmlFor={id}><span>{field.name}</span><select disabled={disabled} id={id} onChange={(event) => onChange(event.target.value)} required={field.required} value={value ?? ""}><option value="">Not set</option>{options.map((option) => <option key={option}>{option}</option>)}</select></label>;
  }
  if (field.field_type === "multiselect") {
    const selected = Array.isArray(value) ? value : [];
    return <label htmlFor={id}><span>{field.name}</span><select disabled={disabled} id={id} multiple onChange={(event) => onChange(Array.from(event.target.selectedOptions, (option) => option.value))} required={field.required} value={selected}>{options.map((option) => <option key={option}>{option}</option>)}</select></label>;
  }
  const type = { date: "date", number: "number", url: "url" }[field.field_type] || "text";
  return <label htmlFor={id}><span>{field.name}</span><input disabled={disabled} id={id} onChange={(event) => onChange(type === "number" && event.target.value !== "" ? Number(event.target.value) : event.target.value)} required={field.required} type={type} value={value ?? ""} /></label>;
}

export function RecordManagementDialog({ entityType, onClose, onNavigate, onReload, open, record, resourceKey }) {
  const tagsState = useResource("tags");
  const assignedState = useResource(`${entityType}/${record.id}/tags`);
  const definitionsState = useResource("custom-fields", { query: { entity_type: entityType } });
  const targetsState = useResource(mergeableResources.has(resourceKey) ? resourceKey : "tags");
  const [selectedTags, setSelectedTags] = useState([]);
  const [custom, setCustom] = useState(record.custom || {});
  const [targetId, setTargetId] = useState("");
  const [tagName, setTagName] = useState("");
  const [tagColor, setTagColor] = useState("blue");
  const [busy, setBusy] = useState("");
  const [feedback, setFeedback] = useState(null);
  const [reloadOnClose, setReloadOnClose] = useState(false);
  const [recordVersion, setRecordVersion] = useState(record.version);
  const archived = Boolean(record.archived_at);
  const archiveEndpoint = archiveResources[resourceKey];
  const canEditCustom = ["accounts", "contacts"].includes(resourceKey) && !archived;
  const targets = mergeableResources.has(resourceKey)
    ? targetsState.data.filter((candidate) => candidate.id !== record.id && !candidate.archived_at)
    : [];

  useEffect(() => setSelectedTags(assignedState.data.map((tag) => tag.id)), [assignedState.data]);
  useEffect(() => setCustom(record.custom || {}), [record.custom, record.id]);
  useEffect(() => setRecordVersion(record.version), [record.id, record.version]);

  async function saveTags(nextIds) {
    const previousIds = selectedTags;
    setSelectedTags(nextIds);
    setBusy("tags");
    setFeedback(null);
    try {
      const result = await api.put(`${entityType}/${record.id}/tags`, nextIds);
      setSelectedTags(result.map((tag) => tag.id));
      setFeedback({ tone: "positive", message: "Tags updated." });
    } catch (caught) {
      setSelectedTags(previousIds);
      setFeedback({ tone: "error", message: caught.message });
    } finally {
      setBusy("");
    }
  }

  async function createAndAssignTag(event) {
    event.preventDefault();
    setBusy("new-tag");
    setFeedback(null);
    try {
      const created = await api.post("tags", { name: tagName, color: tagColor });
      const next = [...new Set([...selectedTags, created.id])];
      const result = await api.put(`${entityType}/${record.id}/tags`, next);
      setSelectedTags(result.map((tag) => tag.id));
      setTagName("");
      await tagsState.reload();
      setFeedback({ tone: "positive", message: "Tag created and assigned." });
    } catch (caught) {
      setFeedback({ tone: "error", message: caught.message });
    } finally {
      setBusy("");
    }
  }

  async function saveCustomFields() {
    setBusy("custom");
    setFeedback(null);
    try {
      const saved = await api.patch(`${resourceKey}/${record.id}`, { version: recordVersion, custom });
      setRecordVersion(saved.version);
      setReloadOnClose(true);
      setFeedback({ tone: "positive", message: "Custom fields updated." });
    } catch (caught) {
      setFeedback({ tone: "error", message: caught.message });
    } finally {
      setBusy("");
    }
  }

  async function archiveOrRestore() {
    const restore = archived && restorableResources.has(resourceKey);
    const verb = restore ? "restore" : "archive";
    if (!window.confirm(`${titleCase(verb)} ${recordName(record)}?${restore ? "" : " The record will leave active views."}`)) return;
    setBusy(verb);
    setFeedback(null);
    try {
      await api.post(`${archiveEndpoint}/${record.id}/${verb}`, { version: recordVersion }, confirmedOptions());
      if (restore) {
        await onReload();
        onClose();
      } else if (restorableResources.has(resourceKey)) {
        await onReload();
      } else {
        onNavigate();
      }
    } catch (caught) {
      setFeedback({ tone: "error", message: caught.message });
    } finally {
      setBusy("");
    }
  }

  async function mergeRecord() {
    const target = targets.find((candidate) => String(candidate.id) === String(targetId));
    if (!target || !window.confirm(`Merge ${recordName(record)} into ${recordName(target)}? This moves linked history and archives the source. This cannot be undone.`)) return;
    setBusy("merge");
    setFeedback(null);
    try {
      const merged = await api.post(`${resourceKey}/merge`, {
        source_id: record.id,
        target_id: target.id,
        source_version: recordVersion,
        target_version: target.version,
      }, confirmedOptions());
      onClose();
      onNavigate(merged.id);
    } catch (caught) {
      setFeedback({ tone: "error", message: caught.message });
    } finally {
      setBusy("");
    }
  }

  return (
    <AppDialog className="record-management-dialog" description="Tags, custom data, duplicate consolidation and archival stay attached to the local source of truth." onClose={() => { onClose(); if (reloadOnClose) { setReloadOnClose(false); onReload(); } }} open={open} title={`Manage ${recordName(record)}`}>
      <div className="management-sections">
        {feedback ? <p aria-live="polite" className={`action-feedback ${feedback.tone}`}>{feedback.message}</p> : null}
        <section>
          <header><div><h3><Tags aria-hidden="true" size={17} /> Tags</h3><p>Use shared labels for filtering and operating context.</p></div></header>
          <div className="tag-picker">
            {tagsState.data.length ? tagsState.data.map((tag) => <label key={tag.id}><input checked={selectedTags.includes(tag.id)} disabled={Boolean(busy) || archived} onChange={(event) => saveTags(event.target.checked ? [...selectedTags, tag.id] : selectedTags.filter((id) => id !== tag.id))} type="checkbox" /><Badge tone="info">{tag.name}</Badge></label>) : <p className="section-empty">No tags yet.</p>}
          </div>
          <form className="inline-create" onSubmit={createAndAssignTag}><label><span>New tag</span><input disabled={archived} onChange={(event) => setTagName(event.target.value)} required value={tagName} /></label><label><span>Colour</span><select disabled={archived} onChange={(event) => setTagColor(event.target.value)} value={tagColor}><option value="blue">Blue</option><option value="cyan">Cyan</option><option value="green">Green</option><option value="amber">Amber</option><option value="red">Red</option></select></label><button className="button button-quiet" disabled={Boolean(busy) || archived} type="submit"><Plus aria-hidden="true" size={14} /> Add</button></form>
        </section>

        {definitionsState.data.length ? <section><header><div><h3><Settings2 aria-hidden="true" size={17} /> Custom fields</h3><p>{canEditCustom ? "Values use the record version to prevent stale updates." : "Definitions are visible here; values are currently editable on accounts and contacts."}</p></div></header><div className="custom-field-grid">{definitionsState.data.map((field) => <CustomFieldInput disabled={!canEditCustom || Boolean(busy)} field={field} key={field.id} onChange={(value) => setCustom((current) => ({ ...current, [field.id]: value }))} value={custom[field.id]} />)}</div>{canEditCustom ? <button className="button button-primary" disabled={Boolean(busy)} onClick={saveCustomFields} type="button">Save custom fields</button> : null}</section> : null}

        {mergeableResources.has(resourceKey) && !archived ? <section><header><div><h3><Merge aria-hidden="true" size={17} /> Merge duplicate</h3><p>The selected target survives; linked records and activity move to it.</p></div></header><div className="management-action-row"><label><span>Surviving {resourceKey === "accounts" ? "account" : "contact"}</span><select onChange={(event) => setTargetId(event.target.value)} value={targetId}><option value="">Choose target</option>{targets.map((target) => <option key={target.id} value={target.id}>{recordName(target)} · #{target.id}</option>)}</select></label><button className="button button-quiet" disabled={!targetId || Boolean(busy)} onClick={mergeRecord} type="button"><Merge aria-hidden="true" size={14} /> Merge</button></div></section> : null}

        {archiveEndpoint ? <section className="danger-zone"><header><div><h3>{archived ? <RotateCcw aria-hidden="true" size={17} /> : <Archive aria-hidden="true" size={17} />} {archived ? "Restore record" : "Archive record"}</h3><p>{archived ? "Return this record to active lists." : "Linked history is preserved; financial and audit records remain immutable."}</p></div></header>{archived && !restorableResources.has(resourceKey) ? <p className="section-empty">This record is archived and cannot be restored by the current API.</p> : <button className="button button-quiet" disabled={Boolean(busy)} onClick={archiveOrRestore} type="button">{archived ? "Restore record" : "Archive record"}</button>}</section> : null}
      </div>
    </AppDialog>
  );
}

const customFieldEntities = [
  ["account", "Account"],
  ["contact", "Contact"],
];

export function CustomFieldManager() {
  const fieldsState = useResource("custom-fields");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ entity_type: "account", name: "", field_type: "text", options: "", required: false });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.post("custom-fields", {
        entity_type: form.entity_type,
        name: form.name,
        field_type: form.field_type,
        options: ["select", "multiselect"].includes(form.field_type) ? form.options.split(",").map((item) => item.trim()).filter(Boolean) : [],
        required: form.required,
        position: fieldsState.data.filter((field) => field.entity_type === form.entity_type).length,
      });
      await fieldsState.reload();
      setForm({ entity_type: "account", name: "", field_type: "text", options: "", required: false });
      setOpen(false);
    } catch (caught) {
      setError(caught.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="settings-section custom-fields-settings">
      <header><span className="section-kicker">Data model</span><h2>Custom fields</h2><p>Define operator-specific data for account and contact workspaces. The API currently supports values on these two record types.</p></header>
      <div className="definition-toolbar"><span>{fieldsState.data.length} definition{fieldsState.data.length === 1 ? "" : "s"}</span><button className="button button-primary" onClick={() => setOpen(true)} type="button"><Plus aria-hidden="true" size={15} /> New field</button></div>
      {fieldsState.error ? <p className="form-error" role="alert">{fieldsState.error.message}</p> : null}
      <div className="definition-list">
        {fieldsState.data.map((field) => <article key={field.id}><div><strong>{field.name}</strong><span>{titleCase(field.entity_type)} · {titleCase(field.field_type)}</span></div>{field.required ? <Badge tone="warning">Required</Badge> : <Badge tone="neutral">Optional</Badge>}</article>)}
        {!fieldsState.loading && !fieldsState.data.length ? <p className="section-empty">No custom field definitions yet.</p> : null}
      </div>
      <AppDialog description="Definitions are append-only in the current API so existing record values remain interpretable." onClose={() => setOpen(false)} open={open} title="New custom field">
        <form className="workflow-form" onSubmit={submit}><div className="workflow-fields"><label><span>Record type</span><select onChange={(event) => setForm((current) => ({ ...current, entity_type: event.target.value }))} value={form.entity_type}>{customFieldEntities.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label><span>Field type</span><select onChange={(event) => setForm((current) => ({ ...current, field_type: event.target.value }))} value={form.field_type}>{["text", "number", "date", "boolean", "select", "multiselect", "url"].map((type) => <option key={type} value={type}>{titleCase(type)}</option>)}</select></label><label className="field-wide"><span>Field name</span><input onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} required value={form.name} /></label>{["select", "multiselect"].includes(form.field_type) ? <label className="field-wide"><span>Options (comma separated)</span><input onChange={(event) => setForm((current) => ({ ...current, options: event.target.value }))} required value={form.options} /></label> : null}<label className="checkbox-field"><input checked={form.required} onChange={(event) => setForm((current) => ({ ...current, required: event.target.checked }))} type="checkbox" /><span>Required value</span></label></div>{error ? <p className="form-error" role="alert">{error}</p> : null}<footer><span>New definitions appear immediately in matching record workspaces.</span><button className="button button-primary" disabled={busy} type="submit">{busy ? "Creating…" : "Create field"}</button></footer></form>
      </AppDialog>
    </section>
  );
}
