import React, { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  Download,
  FilePlus2,
  FolderSync,
  LoaderCircle,
  Pencil,
  RefreshCw,
  RotateCcw,
  Trash2,
  Upload,
} from "lucide-react";

import { ApiError, api, initializeSession } from "../api";
import { useResource } from "../hooks";
import { formatDate } from "../utils/format";
import { AppDialog, Badge, EmptyState, LoadingState, PageControls, UnavailableState } from "./common";
import { WorkflowDialog } from "./WorkflowDialogs";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "/api/v1").replace(/\/$/, "");

const EXPORTS = [
  ["report:overview", "Management overview"],
  ["report:finance", "Revenue, collections & VAT"],
  ["report:projects", "Delivery profitability"],
  ["report:renewals", "Client renewals"],
  ["report:ledger", "Ledger journal"],
  ["accounts", "Source · Accounts"],
  ["contacts", "Source · Contacts"],
  ["leads", "Source · Leads"],
  ["tenders", "Source · Tenders"],
  ["opportunities", "Source · Opportunities"],
];

const IMPORT_FIELDS = {
  accounts: ["name", "legal_name", "domain", "website", "phone", "billing_email", "company_number", "vat_number", "source", "payment_terms_days", "status", "health_status", "health_score", "renewal_date", "notes", "roles", "custom"],
  contacts: ["account_id", "account_name", "first_name", "last_name", "display_name", "job_title", "email", "phone", "mobile", "preferred_channel", "source", "lawful_basis", "status", "notes", "custom"],
  leads: ["account_id", "account_name", "contact_id", "contact_email", "title", "company", "email", "phone", "source", "status", "score", "estimated_value_minor", "next_action", "notes"],
  opportunities: ["account_id", "account_name", "primary_contact_id", "contact_email", "tender_id", "stage_id", "stage_name", "type", "title", "value_minor", "probability_bps", "expected_close_date", "source", "next_action", "notes"],
};

const IMPORT_ALIASES = {
  account: "account_name",
  company_name: "account_name",
  contact: "contact_email",
  deal_name: "title",
  opportunity_name: "title",
  lead_name: "title",
  name_of_account: "name",
};

function fieldName(value = "") {
  return String(value).trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

export function csvHeaders(csvText = "") {
  const headers = [];
  let current = "";
  let quoted = false;
  let finished = false;
  const input = String(csvText).replace(/^\ufeff/, "");
  for (let index = 0; index < input.length; index += 1) {
    const character = input[index];
    if (character === '"') {
      if (quoted && input[index + 1] === '"') {
        current += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (character === "," && !quoted) {
      headers.push(current.trim());
      current = "";
    } else if ((character === "\n" || character === "\r") && !quoted) {
      headers.push(current.trim());
      finished = true;
      break;
    } else {
      current += character;
    }
  }
  if (!finished && (current || (!headers.length && input))) headers.push(current.trim());
  return headers.filter(Boolean);
}

export function suggestedImportMapping(entityType, headers) {
  const allowed = new Set(IMPORT_FIELDS[entityType] || []);
  return Object.fromEntries(headers.flatMap((header) => {
    const normal = fieldName(header);
    const target = IMPORT_ALIASES[normal] || normal;
    return allowed.has(target) ? [[header, target]] : [];
  }));
}

function absoluteApiUrl(path) {
  const base = /^https?:\/\//i.test(API_BASE_URL)
    ? API_BASE_URL
    : new URL(API_BASE_URL.replace(/^\//, "") + "/", window.location.origin + "/").toString().replace(/\/$/, "");
  return `${base}/${String(path).replace(/^\//, "")}`;
}

function downloadName(response, fallback) {
  const disposition = response.headers.get("content-disposition") || "";
  return disposition.match(/filename="?([^";]+)"?/i)?.[1] || fallback;
}

export async function downloadRecordsCsv(entityType, { includeArchived = false } = {}) {
  await initializeSession();
  const report = entityType.startsWith("report:");
  const query = !report && includeArchived ? "?include_archived=true" : "";
  const path = report
    ? `reports/${encodeURIComponent(entityType.slice(7))}.csv`
    : `exports/${encodeURIComponent(entityType)}.csv`;
  const response = await fetch(absoluteApiUrl(`${path}${query}`), {
    credentials: "same-origin",
    headers: { Accept: "text/csv" },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new ApiError(payload.message || payload.detail || `Export failed (${response.status})`, {
      status: response.status,
      code: payload.code || "export_failed",
    });
  }
  const objectUrl = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = downloadName(response, `${entityType.replace(":", "-")}.csv`);
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

function addressFrom(profile = {}) {
  const address = profile.registered_address || {};
  return {
    line1: address.line1 || "",
    line2: address.line2 || "",
    city: address.city || "",
    region: address.region || "",
    postcode: address.postcode || "",
    country_code: address.country_code || "GB",
  };
}

function businessForm(profile = {}) {
  return {
    legal_name: profile.legal_name || "",
    trading_name: profile.trading_name || "",
    company_number: profile.company_number || "",
    invoice_email: profile.invoice_email || "",
    invoice_phone: profile.invoice_phone || "",
    bank_details: profile.bank_details || "",
    currency: profile.currency || "GBP",
    timezone: profile.timezone || "Europe/London",
    default_payment_terms_days: String(profile.default_payment_terms_days ?? 14),
    vat_registered: Boolean(profile.vat_registered),
    vat_number: profile.vat_number || "",
    vat_scheme: profile.vat_scheme || "Standard",
    vat_effective_from: profile.vat_effective_from || "",
    vat_effective_to: profile.vat_effective_to || "",
    tax_codes_approved: Boolean(profile.tax_codes_approved),
    default_vat_percent: String((Number(profile.default_vat_bps ?? 2000) / 100).toFixed(2)).replace(/\.00$/, ""),
    registered_address: addressFrom(profile),
  };
}

export function BusinessProfileDialog({ open, profile = {}, onClose, onSaved }) {
  const [form, setForm] = useState(() => businessForm(profile));

  useEffect(() => {
    if (open) setForm(businessForm(profile));
  }, [open, profile?.version]);

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function updateAddress(key, value) {
    setForm((current) => ({
      ...current,
      registered_address: { ...current.registered_address, [key]: value },
    }));
  }

  async function save() {
    if (form.vat_registered && !form.vat_number.trim()) throw new Error("Enter the VAT registration number before enabling VAT.");
    if (form.vat_registered && !form.vat_effective_from) throw new Error("Enter the date VAT registration became effective.");
    if (form.vat_registered && !form.tax_codes_approved) throw new Error("Confirm that an accountant has approved the VAT tax codes.");
    const payload = {
      version: Number(profile.version || 1),
      legal_name: form.legal_name.trim(),
      trading_name: form.trading_name.trim(),
      company_number: form.company_number.trim(),
      registered_address: form.registered_address,
      invoice_email: form.invoice_email.trim(),
      invoice_phone: form.invoice_phone.trim(),
      bank_details: form.bank_details.trim(),
      currency: form.currency.trim().toUpperCase(),
      timezone: form.timezone,
      default_payment_terms_days: Number(form.default_payment_terms_days),
      vat_registered: form.vat_registered,
      ...(form.vat_registered ? {
        vat_number: form.vat_number.trim(),
        vat_scheme: form.vat_scheme,
        vat_effective_from: form.vat_effective_from,
        vat_effective_to: form.vat_effective_to || null,
        tax_codes_approved: true,
        default_vat_bps: Math.round(Number(form.default_vat_percent) * 100),
      } : {}),
    };
    const saved = await api.patch("settings/business", payload);
    onSaved?.(saved);
    onClose();
  }

  return (
    <WorkflowDialog
      description="Legal identity and billing defaults are frozen into commercial records when they are issued."
      onClose={onClose}
      onSubmit={save}
      open={open}
      submitLabel="Save profile"
      title="Business & VAT profile"
    >
      <label className="field-wide"><span>Legal name</span><input autoComplete="organization" onChange={(event) => update("legal_name", event.target.value)} required value={form.legal_name} /></label>
      <label><span>Trading name</span><input onChange={(event) => update("trading_name", event.target.value)} value={form.trading_name} /></label>
      <label><span>Company number</span><input onChange={(event) => update("company_number", event.target.value)} value={form.company_number} /></label>
      <label className="field-wide"><span>Registered address line 1</span><input autoComplete="address-line1" onChange={(event) => updateAddress("line1", event.target.value)} required value={form.registered_address.line1} /></label>
      <label className="field-wide"><span>Registered address line 2</span><input autoComplete="address-line2" onChange={(event) => updateAddress("line2", event.target.value)} value={form.registered_address.line2} /></label>
      <label><span>Town or city</span><input autoComplete="address-level2" onChange={(event) => updateAddress("city", event.target.value)} required value={form.registered_address.city} /></label>
      <label><span>County or region</span><input autoComplete="address-level1" onChange={(event) => updateAddress("region", event.target.value)} value={form.registered_address.region} /></label>
      <label><span>Postcode</span><input autoComplete="postal-code" onChange={(event) => updateAddress("postcode", event.target.value)} required value={form.registered_address.postcode} /></label>
      <label><span>Country code</span><input autoComplete="country" maxLength="2" onChange={(event) => updateAddress("country_code", event.target.value.toUpperCase())} required value={form.registered_address.country_code} /></label>
      <label><span>Invoice email</span><input autoComplete="email" onChange={(event) => update("invoice_email", event.target.value)} required type="email" value={form.invoice_email} /></label>
      <label><span>Invoice phone</span><input autoComplete="tel" onChange={(event) => update("invoice_phone", event.target.value)} type="tel" value={form.invoice_phone} /></label>
      <label><span>Currency</span><input maxLength="3" onChange={(event) => update("currency", event.target.value.toUpperCase())} required value={form.currency} /></label>
      <label><span>Business timezone</span><select onChange={(event) => update("timezone", event.target.value)} value={form.timezone}><option value="Europe/London">Europe/London</option></select></label>
      <label><span>Default payment terms (days)</span><input max="365" min="0" onChange={(event) => update("default_payment_terms_days", event.target.value)} required type="number" value={form.default_payment_terms_days} /></label>
      <label className="field-wide"><span>Bank details shown on invoices</span><textarea onChange={(event) => update("bank_details", event.target.value)} rows="3" value={form.bank_details} /></label>
      <label className="checkbox-field field-wide"><input checked={form.vat_registered} onChange={(event) => update("vat_registered", event.target.checked)} type="checkbox" /><span>VAT registered</span></label>
      {form.vat_registered ? <>
        <label><span>VAT number</span><input onChange={(event) => update("vat_number", event.target.value)} required value={form.vat_number} /></label>
        <label><span>VAT scheme</span><select onChange={(event) => update("vat_scheme", event.target.value)} value={form.vat_scheme}><option value="Standard">Standard accounting</option><option value="Cash Accounting">Cash accounting</option><option value="Flat Rate">Flat Rate Scheme</option></select></label>
        <label><span>VAT effective from</span><input onChange={(event) => update("vat_effective_from", event.target.value)} required type="date" value={form.vat_effective_from} /></label>
        <label><span>VAT effective to (optional)</span><input min={form.vat_effective_from} onChange={(event) => update("vat_effective_to", event.target.value)} type="date" value={form.vat_effective_to} /></label>
        <label><span>Default VAT rate (%)</span><input max="100" min="0" onChange={(event) => update("default_vat_percent", event.target.value)} required step="0.01" type="number" value={form.default_vat_percent} /></label>
        <label className="checkbox-field"><input checked={form.tax_codes_approved} onChange={(event) => update("tax_codes_approved", event.target.checked)} required type="checkbox" /><span>Tax codes approved by accountant</span></label>
      </> : null}
    </WorkflowDialog>
  );
}

export function BusinessProfileControl({ profile, onSaved, className = "button button-quiet", children = "Edit profile" }) {
  const [open, setOpen] = useState(false);
  return <><button aria-haspopup="dialog" className={className} onClick={() => setOpen(true)} type="button">{children}</button><BusinessProfileDialog onClose={() => setOpen(false)} onSaved={onSaved} open={open} profile={profile} /></>;
}

export function IntegrityCheckDialog({ open, onClose, onChecked }) {
  const [result, setResult] = useState(null);

  useEffect(() => {
    if (open) setResult(null);
  }, [open]);

  return (
    <WorkflowDialog
      description="Runs SQLite's full consistency check against the live local workspace."
      onClose={onClose}
      onSubmit={async () => {
        const checked = await api.post("settings/integrity", {});
        setResult(checked);
        onChecked?.(checked);
      }}
      open={open}
      submitLabel="Run full check"
      title="Check local database"
    >
      <div className="field-wide" role="status">
        {result ? <p className={`credential-feedback ${result.database === "ok" ? "positive" : "error"}`}><CheckCircle2 aria-hidden="true" size={15} /> Database result: {result.database}</p> : <p>The check is read-only and does not interrupt local CRM work.</p>}
      </div>
    </WorkflowDialog>
  );
}

export function IntegrityCheckControl({ onChecked, className = "button button-quiet", children = "Run integrity check" }) {
  const [open, setOpen] = useState(false);
  return <><button aria-haspopup="dialog" className={className} onClick={() => setOpen(true)} type="button"><Database aria-hidden="true" size={15} /> {children}</button><IntegrityCheckDialog onChecked={onChecked} onClose={() => setOpen(false)} open={open} /></>;
}

export function BackupCreateDialog({ open, onClose, onQueued }) {
  const [destination, setDestination] = useState("");

  useEffect(() => {
    if (open) setDestination("");
  }, [open]);

  return (
    <WorkflowDialog
      confirmMessage="Queue a verified SQLite backup in this folder?"
      description="Choose an absolute Windows folder. Secrets are excluded and integrations require reauthorisation after restore."
      onClose={onClose}
      onSubmit={async () => {
        const queued = await api.post("backups", { destination_directory: destination.trim() });
        onQueued?.(queued);
        onClose();
      }}
      open={open}
      submitLabel="Queue backup"
      title="Create local backup"
    >
      <label className="field-wide"><span>Destination folder</span><input onChange={(event) => setDestination(event.target.value)} placeholder="C:\\Users\\you\\Documents\\CRM Backups" required value={destination} /></label>
      <p className="field-wide">The durable worker verifies the backup before marking its job complete.</p>
    </WorkflowDialog>
  );
}

export function BackupCreateControl({ onQueued, className = "button button-quiet", children = "Create backup" }) {
  const [open, setOpen] = useState(false);
  return <><button aria-haspopup="dialog" className={className} onClick={() => setOpen(true)} type="button"><RefreshCw aria-hidden="true" size={15} /> {children}</button><BackupCreateDialog onClose={() => setOpen(false)} onQueued={onQueued} open={open} /></>;
}

export function ReportsCsvExportDialog({ open, onClose, onDownloaded }) {
  const [entityType, setEntityType] = useState("report:overview");
  const [includeArchived, setIncludeArchived] = useState(false);

  useEffect(() => {
    if (open) {
      setEntityType("report:overview");
      setIncludeArchived(false);
    }
  }, [open]);

  return (
    <WorkflowDialog
      description="Download a curated management report or its underlying source records."
      onClose={onClose}
      onSubmit={async () => {
        await downloadRecordsCsv(entityType, { includeArchived });
        onDownloaded?.(entityType);
        onClose();
      }}
      open={open}
      submitLabel="Download CSV"
      title="Export report data"
    >
      <label className="field-wide"><span>Dataset</span><select onChange={(event) => setEntityType(event.target.value)} value={entityType}>{EXPORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      {!entityType.startsWith("report:") ? <label className="checkbox-field field-wide"><input checked={includeArchived} onChange={(event) => setIncludeArchived(event.target.checked)} type="checkbox" /><span>Include archived records</span></label> : null}
    </WorkflowDialog>
  );
}

export function ReportsCsvExportControl({ onDownloaded, className = "button button-quiet", children = "Export CSV" }) {
  const [open, setOpen] = useState(false);
  return <><button aria-haspopup="dialog" className={className} onClick={() => setOpen(true)} type="button"><Download aria-hidden="true" size={15} /> {children}</button><ReportsCsvExportDialog onClose={() => setOpen(false)} onDownloaded={onDownloaded} open={open} /></>;
}

function importSummary(result) {
  if (!result) return "";
  if (result.dry_run) {
    return `${result.total_rows} rows checked · ${result.create_count} ready · ${result.duplicate_count} duplicates · ${result.error_count} errors`;
  }
  return `${result.created_count} records created · ${result.duplicate_count} duplicates skipped · ${result.error_count} errors`;
}

export function CsvImportDialog({ open, onClose, onImported }) {
  const [entityType, setEntityType] = useState("accounts");
  const [csvText, setCsvText] = useState("");
  const [filename, setFilename] = useState("import.csv");
  const [mapping, setMapping] = useState({});
  const [preview, setPreview] = useState(null);
  const headers = useMemo(() => csvHeaders(csvText), [csvText]);

  useEffect(() => {
    if (!open) return;
    setEntityType("accounts");
    setCsvText("");
    setFilename("import.csv");
    setMapping({});
    setPreview(null);
  }, [open]);

  function replaceCsv(text, name = filename, type = entityType) {
    const nextHeaders = csvHeaders(text);
    setCsvText(text);
    setFilename(name || "import.csv");
    setMapping(suggestedImportMapping(type, nextHeaders));
    setPreview(null);
  }

  function changeEntity(nextType) {
    setEntityType(nextType);
    setMapping(suggestedImportMapping(nextType, headers));
    setPreview(null);
  }

  function mapColumn(source, target) {
    setMapping((current) => {
      const next = { ...current };
      if (target) next[source] = target;
      else delete next[source];
      return next;
    });
    setPreview(null);
  }

  const request = { entity_type: entityType, csv_text: csvText, mapping, filename };
  return (
    <WorkflowDialog
      confirmMessage={preview ? `Create ${preview.create_count} ${entityType} records? Duplicates will be skipped.` : ""}
      description="Load a CSV locally, map its columns, preview validation and duplicates, then commit in a separate confirmed step."
      onClose={onClose}
      onSubmit={async () => {
        if (!Object.keys(mapping).length) throw new Error("Map at least one CSV column to a CRM field.");
        if (!preview) {
          setPreview(await api.post("imports/csv/preview", request));
          return;
        }
        const result = await api.post("imports/csv/commit", request);
        onImported?.(result);
        onClose();
      }}
      open={open}
      submitLabel={preview ? "Commit import" : "Preview import"}
      title="Import CRM records"
    >
      <label><span>Record type</span><select onChange={(event) => changeEntity(event.target.value)} value={entityType}>{Object.keys(IMPORT_FIELDS).map((type) => <option key={type} value={type}>{type[0].toUpperCase() + type.slice(1)}</option>)}</select></label>
      <label><span>CSV file</span><input accept=".csv,text/csv" onChange={async (event) => { const file = event.target.files?.[0]; if (file) replaceCsv(await file.text(), file.name); }} type="file" /></label>
      <label className="field-wide"><span>Or paste CSV</span><textarea onChange={(event) => replaceCsv(event.target.value, filename)} placeholder="name,domain,status&#10;North Star,northstar.example,Prospect" rows="5" value={csvText} /></label>
      {headers.length ? <fieldset className="field-wide import-mapping"><legend>Column mapping</legend>{headers.map((header) => <label key={header}><span>{header}</span><select aria-label={`Map ${header}`} onChange={(event) => mapColumn(header, event.target.value)} value={mapping[header] || ""}><option value="">Do not import</option>{IMPORT_FIELDS[entityType].map((field) => <option key={field} value={field}>{field.replace(/_/g, " ")}</option>)}</select></label>)}</fieldset> : null}
      {preview ? <div aria-live="polite" className="field-wide import-preview" role="status"><strong>Preview complete</strong><p>{importSummary(preview)}</p>{preview.rows?.some((row) => row.status === "error") ? <ul>{preview.rows.filter((row) => row.status === "error").slice(0, 5).map((row) => <li key={row.row}>Row {row.row}: {row.error || row.reason}</li>)}</ul> : null}</div> : null}
    </WorkflowDialog>
  );
}

export function CsvImportControl({ onImported, className = "button button-quiet", children = "Import CSV" }) {
  const [open, setOpen] = useState(false);
  return <><button aria-haspopup="dialog" className={className} onClick={() => setOpen(true)} type="button"><Upload aria-hidden="true" size={15} /> {children}</button><CsvImportDialog onClose={() => setOpen(false)} onImported={onImported} open={open} /></>;
}

export function BackupRestoreDialog({ open, onClose, onQueued }) {
  const [backupPath, setBackupPath] = useState("");
  const [confirmation, setConfirmation] = useState("");

  useEffect(() => {
    if (open) {
      setBackupPath("");
      setConfirmation("");
    }
  }, [open]);

  return (
    <WorkflowDialog
      confirmMessage="Stage this restore? CRM Workspace will preserve the current database, stop applying changes, and require a restart."
      description="Validate and stage a known SQLite backup for the next application start. The current database is preserved as a safety copy."
      onClose={onClose}
      onSubmit={async () => {
        if (confirmation !== "RESTORE") throw new Error("Type RESTORE exactly to confirm this recovery operation.");
        const queued = await api.post("backups/restore", { backup_path: backupPath.trim(), confirmed: true }, { headers: { "X-CRM-Confirmed": "true" } });
        onQueued?.(queued);
        onClose();
      }}
      open={open}
      submitLabel="Stage restore"
      title="Restore CRM workspace"
    >
      <div className="field-wide destructive-notice"><AlertTriangle aria-hidden="true" size={18} /><p><strong>Recovery changes the source of truth.</strong> Pending local changes after the selected backup will not be present after restart. Integration credentials are excluded and must be reauthorised.</p></div>
      <label className="field-wide"><span>Absolute backup file path</span><input onChange={(event) => setBackupPath(event.target.value)} placeholder="C:\\CRM Backups\\crm-20260710.sqlite3" required value={backupPath} /></label>
      <label className="field-wide"><span>Type RESTORE to continue</span><input autoComplete="off" onChange={(event) => setConfirmation(event.target.value)} pattern="RESTORE" required value={confirmation} /></label>
    </WorkflowDialog>
  );
}

export function BackupRestoreControl({ onQueued, className = "button button-quiet", children = "Restore backup" }) {
  const [open, setOpen] = useState(false);
  return <><button aria-haspopup="dialog" className={className} onClick={() => setOpen(true)} type="button"><RotateCcw aria-hidden="true" size={15} /> {children}</button><BackupRestoreDialog onClose={() => setOpen(false)} onQueued={onQueued} open={open} /></>;
}

function jobLabel(job) {
  return String(job.kind || "Job").replace(/[._]/g, " ");
}

export function JobRecoveryDialog({ open, onClose }) {
  const jobsState = useResource("jobs");
  const [feedback, setFeedback] = useState("");
  const jobs = (jobsState.data || []).filter((job) => ["failed", "unknown", "retry_wait", "running", "queued"].includes(job.state));

  useEffect(() => {
    if (open) {
      setFeedback("");
      jobsState.reload();
    }
  }, [open]);

  async function retry(job) {
    if (!window.confirm(`Retry failed ${jobLabel(job)} job?`)) return;
    await api.post(`jobs/${job.id}/retry`, {});
    setFeedback("Failed job queued for a deliberate retry.");
    jobsState.reload();
  }

  async function reconcile(job, succeeded) {
    const message = succeeded
      ? "Confirm that the provider completed this action? The CRM will mark it successful without sending again."
      : "Confirm that no provider side effect occurred? The CRM may safely retry this action.";
    if (!window.confirm(message)) return;
    await api.post(
      `jobs/${job.id}/reconcile`,
      { succeeded, result: { operator_reconciled: true } },
      { headers: { "X-CRM-Confirmed": "true" } },
    );
    setFeedback(succeeded ? "Unknown outcome marked successful." : "No side effect confirmed; job released for retry.");
    jobsState.reload();
  }

  return (
    <AppDialog className="workflow-dialog job-dialog" description="Inspect pending work, deliberately retry failures, and reconcile unknown external outcomes before any resend." onClose={onClose} open={open} title="Durable job recovery">
      <div className="job-toolbar"><p>{jobs.length ? `${jobs.length} active or attention-needed jobs` : "No jobs need attention."}</p><button className="button button-quiet" onClick={jobsState.reload} type="button"><RefreshCw aria-hidden="true" size={15} /> Refresh</button></div>
      {jobsState.loading ? <p aria-live="polite" className="job-empty"><LoaderCircle aria-hidden="true" className="spin" size={16} /> Loading durable jobs…</p> : null}
      {jobsState.error ? <p className="form-error" role="alert">{jobsState.error.message}</p> : null}
      {!jobsState.loading && !jobsState.error ? <div className="job-list">{jobs.map((job) => <article className={`job-row job-${job.state}`} key={job.id}><div><span><strong>{jobLabel(job)}</strong><small>{job.state}</small></span><p>{job.last_error || `Attempt ${job.attempts} of ${job.max_attempts}`}</p><time dateTime={job.updated_at}>{new Date(job.updated_at).toLocaleString("en-GB")}</time></div><div>{job.state === "failed" ? <button className="button button-quiet" onClick={() => retry(job)} type="button">Retry</button> : null}{job.state === "unknown" ? <><button className="button button-quiet" onClick={() => reconcile(job, true)} type="button">Completed remotely</button><button className="button button-quiet" onClick={() => reconcile(job, false)} type="button">No side effect</button></> : null}</div></article>)}{!jobs.length ? <div className="job-empty"><CheckCircle2 aria-hidden="true" size={18} /><strong>Queue is healthy</strong><span>Completed jobs remain in the local attempt history and audit trail.</span></div> : null}</div> : null}
      {feedback ? <p aria-live="polite" className="action-feedback positive">{feedback}</p> : null}
      <footer className="dialog-actions"><span><Activity aria-hidden="true" size={15} /> Unknown outcomes are never blindly retried</span><button className="button button-primary" onClick={onClose} type="button">Done</button></footer>
    </AppDialog>
  );
}

export function JobRecoveryControl({ className = "button button-quiet", children = "Open job recovery" }) {
  const [open, setOpen] = useState(false);
  return <><button aria-haspopup="dialog" className={className} onClick={() => setOpen(true)} type="button"><Activity aria-hidden="true" size={15} /> {children}</button><JobRecoveryDialog onClose={() => setOpen(false)} open={open} /></>;
}

function linkedName(record) {
  return record.name || record.title || record.number || record.display_name || `#${record.id}`;
}

function contextLink(context) {
  if (!context?.id) return "";
  const type = String(context.entity_type || context.resource_type || "").replace(/ies$/, "y").replace(/s$/, "");
  return type ? `${type}:${context.id}` : "";
}

export function DocumentCreateDialog({ open, context, onClose, onCreated }) {
  const accounts = useResource("accounts").data;
  const opportunities = useResource("opportunities").data;
  const projects = useResource("projects").data;
  const invoices = useResource("invoices").data;
  const templates = useResource("document-templates").data;
  const [form, setForm] = useState({ title: "", source: "drive_new", link: "", google_file_id: "", drive_url: "", template_id: "", template_name: "", merge_data: "{}" });

  useEffect(() => {
    if (open) setForm({ title: "", source: "drive_new", link: contextLink(context), google_file_id: "", drive_url: "", template_id: "", template_name: "", merge_data: "{}" });
  }, [open, context?.entity_type, context?.resource_type, context?.id]);

  const groups = useMemo(() => [
    ["account", "Accounts", accounts],
    ["opportunity", "Opportunities", opportunities],
    ["project", "Projects", projects],
    ["invoice", "Invoices", invoices],
  ], [accounts, opportunities, projects, invoices]);

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function create() {
    const [entityType = "", rawId = ""] = form.link.split(":");
    if (form.source === "drive_existing" && !form.google_file_id.trim()) throw new Error("Enter the Google Drive file ID.");
    let templateId = form.template_id;
    if (form.source === "drive_template" && templateId === "new") {
      if (!form.google_file_id.trim()) throw new Error("Enter the Google Drive template file ID.");
      const registered = await api.post("document-templates", {
        name: form.template_name.trim() || `${form.title.trim()} template`,
        google_file_id: form.google_file_id.trim(),
        category: "Commercial",
        merge_schema: {},
      });
      templateId = String(registered.id);
    }
    if (form.source === "drive_template" && !templateId) throw new Error("Choose or register a document template.");
    let mergeData = {};
    if (form.source === "drive_template") {
      try {
        mergeData = JSON.parse(form.merge_data || "{}");
      } catch {
        throw new Error("Merge data must be a valid JSON object.");
      }
      if (!mergeData || Array.isArray(mergeData) || typeof mergeData !== "object") throw new Error("Merge data must be a JSON object.");
    }
    const created = await api.post("documents", {
      title: form.title.trim(),
      entity_type: entityType,
      entity_id: rawId ? Number(rawId) : undefined,
      mime_type: form.source === "local" ? "application/octet-stream" : "application/vnd.google-apps.document",
      queue_drive: ["drive_new", "drive_template"].includes(form.source),
      template_id: form.source === "drive_template" ? Number(templateId) : undefined,
      merge_data: mergeData,
      google_file_id: form.source === "drive_existing" ? form.google_file_id.trim() : "",
      drive_url: form.source === "drive_existing" ? form.drive_url.trim() : "",
    });
    onCreated?.(created);
    onClose();
  }

  return (
    <WorkflowDialog
      description="Create local metadata, copy and merge a Drive template, start a blank Google Doc, or link an existing file."
      onClose={onClose}
      onSubmit={create}
      open={open}
      submitLabel={["drive_new", "drive_template"].includes(form.source) ? "Queue document" : "Create file record"}
      title="New file"
    >
      <label className="field-wide"><span>Title</span><input onChange={(event) => update("title", event.target.value)} required value={form.title} /></label>
      <label><span>Source</span><select onChange={(event) => update("source", event.target.value)} value={form.source}><option value="drive_new">New blank Google Doc</option><option value="drive_template">Google Doc from template</option><option value="drive_existing">Existing Drive file</option><option value="local">Local metadata only</option></select></label>
      <label><span>Linked record (optional)</span><select onChange={(event) => update("link", event.target.value)} value={form.link}><option value="">Not linked</option>{groups.map(([type, label, items]) => <optgroup key={type} label={label}>{items.map((item) => <option key={`${type}:${item.id}`} value={`${type}:${item.id}`}>{linkedName(item)}</option>)}</optgroup>)}</select></label>
      {form.source === "drive_existing" ? <><label><span>Google Drive file ID</span><input onChange={(event) => update("google_file_id", event.target.value)} required value={form.google_file_id} /></label><label><span>Drive URL (optional)</span><input onChange={(event) => update("drive_url", event.target.value)} type="url" value={form.drive_url} /></label></> : null}
      {form.source === "drive_template" ? <><label><span>Template</span><select onChange={(event) => update("template_id", event.target.value)} required value={form.template_id}><option value="">Choose template</option>{templates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}<option value="new">Register a Drive template…</option></select></label>{form.template_id === "new" ? <><label><span>Template name</span><input onChange={(event) => update("template_name", event.target.value)} required value={form.template_name} /></label><label className="field-wide"><span>Google Drive template file ID</span><input onChange={(event) => update("google_file_id", event.target.value)} required value={form.google_file_id} /></label></> : null}<label className="field-wide"><span>Merge data (JSON)</span><textarea onChange={(event) => update("merge_data", event.target.value)} rows="5" value={form.merge_data} /></label><p className="field-wide">Template tokens use double braces, for example <code>{"{{client.name}}"}</code>.</p></> : null}
    </WorkflowDialog>
  );
}

export function DocumentCreateControl({ context, onCreated, className = "button button-primary", children = "New file" }) {
  const [open, setOpen] = useState(false);
  return <><button aria-haspopup="dialog" className={className} onClick={() => setOpen(true)} type="button"><FilePlus2 aria-hidden="true" size={15} /> {children}</button><DocumentCreateDialog context={context} onClose={() => setOpen(false)} onCreated={onCreated} open={open} /></>;
}

export function DocumentSyncControl({ document: file, onQueued, className = "button button-quiet", children = "Sync from Drive" }) {
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const disabled = busy || !file?.google_file_id || file?.sync_state === "Queued";

  async function sync() {
    if (!window.confirm("Queue a Drive sync and store a checksummed local PDF version?")) return;
    setBusy(true);
    setFeedback("");
    try {
      const queued = await api.post(`documents/${file.id}/sync`, {});
      setFeedback("Drive sync queued.");
      onQueued?.(queued);
    } catch (error) {
      setFeedback(error.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span>
      <button className={className} disabled={disabled} onClick={sync} title={!file?.google_file_id ? "Link a Google Drive file before syncing" : undefined} type="button">
        {busy ? <LoaderCircle aria-hidden="true" className="spin" size={15} /> : <FolderSync aria-hidden="true" size={15} />} {busy ? "Queuing…" : children}
      </button>
      {feedback ? <span aria-live="polite" className={`credential-feedback ${feedback.endsWith("queued.") ? "positive" : "error"}`}>{feedback}</span> : null}
    </span>
  );
}

const EMPTY_DOCUMENT_TEMPLATE = {
  name: "",
  category: "",
  google_file_id: "",
  mime_type: "application/vnd.google-apps.document",
  merge_schema: "{}",
};

function documentTemplateForm(template) {
  return template ? {
    name: template.name || "",
    category: template.category || "",
    google_file_id: template.google_file_id || "",
    mime_type: template.mime_type || "application/vnd.google-apps.document",
    merge_schema: JSON.stringify(template.merge_schema || {}, null, 2),
  } : EMPTY_DOCUMENT_TEMPLATE;
}

export function DocumentTemplateDialog({ onClose, onSaved, open, template }) {
  const [form, setForm] = useState(() => documentTemplateForm(template));
  useEffect(() => {
    if (open) setForm(documentTemplateForm(template));
  }, [open, template?.id, template?.version]);

  function update(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  return (
    <WorkflowDialog description="Register a Google Docs source and the merge fields operators should provide when creating files." onClose={onClose} onSubmit={async () => {
      let mergeSchema;
      try {
        mergeSchema = JSON.parse(form.merge_schema || "{}");
      } catch {
        throw new Error("Merge schema must be valid JSON.");
      }
      if (!mergeSchema || Array.isArray(mergeSchema) || typeof mergeSchema !== "object") throw new Error("Merge schema must be a JSON object.");
      const payload = {
        name: form.name.trim(),
        category: form.category.trim(),
        google_file_id: form.google_file_id.trim(),
        mime_type: form.mime_type.trim(),
        merge_schema: mergeSchema,
      };
      const saved = template
        ? await api.patch(`document-templates/${template.id}`, { version: template.version, ...payload })
        : await api.post("document-templates", payload);
      await onSaved(saved);
      onClose();
    }} open={open} submitLabel={template ? "Save template" : "Create template"} title={template ? "Edit document template" : "New document template"}>
      <label><span>Name</span><input onChange={(event) => update("name", event.target.value)} required value={form.name} /></label>
      <label><span>Category</span><input onChange={(event) => update("category", event.target.value)} value={form.category} /></label>
      <label className="field-wide"><span>Google Drive file ID</span><input onChange={(event) => update("google_file_id", event.target.value)} required value={form.google_file_id} /></label>
      <label className="field-wide"><span>MIME type</span><input onChange={(event) => update("mime_type", event.target.value)} required value={form.mime_type} /></label>
      <label className="field-wide"><span>Merge schema (JSON object)</span><textarea onChange={(event) => update("merge_schema", event.target.value)} rows="7" spellCheck="false" value={form.merge_schema} /></label>
    </WorkflowDialog>
  );
}

export function DocumentTemplateManager() {
  const templatesState = useResource("document-templates", { pageSize: 25 });
  const [dialog, setDialog] = useState({ open: false, template: null });
  const [feedback, setFeedback] = useState("");

  async function archive(template) {
    if (!window.confirm(`Archive ${template.name}? Existing documents and versions remain available.`)) return;
    try {
      await api.post(`document-templates/${template.id}/archive`, { version: template.version }, { headers: { "X-CRM-Confirmed": "true" } });
      setFeedback("Document template archived.");
      await templatesState.reload();
    } catch (error) {
      setFeedback(error.message);
    }
  }

  return (
    <section aria-label="Document templates" className="operations-panel">
      <div className="operations-toolbar"><div><strong>Document templates</strong><span>Drive-backed sources, categories, and merge schemas.</span></div><button className="button button-primary" onClick={() => setDialog({ open: true, template: null })} type="button"><FilePlus2 aria-hidden="true" size={15} /> New template</button></div>
      {feedback ? <p aria-live="polite" className="action-feedback">{feedback}</p> : null}
      {templatesState.loading ? <LoadingState label="Loading document templates" /> : null}
      {templatesState.error ? <UnavailableState error={templatesState.error} onRetry={templatesState.reload} /> : null}
      {!templatesState.loading && !templatesState.error && !templatesState.data.length ? <EmptyState icon={FilePlus2} title="No document templates yet" message="Register a Google Doc to create consistent proposals, plans, and reports." /> : null}
      <div className="control-list">
        {templatesState.data.map((template) => <article className="control-row" key={template.id}><div><span><strong>{template.name}</strong><Badge tone="info">{template.category || "Uncategorised"}</Badge></span><p>{template.google_file_id || "No Drive source"} Â· version {template.version}</p></div><div><button className="button button-quiet" onClick={() => setDialog({ open: true, template })} type="button"><Pencil aria-hidden="true" size={13} /> Edit</button><button className="button button-danger" onClick={() => archive(template)} type="button"><Trash2 aria-hidden="true" size={13} /> Archive</button></div></article>)}
      </div>
      <PageControls hasNext={templatesState.hasNext} hasPrevious={templatesState.hasPrevious} label="Document templates" nextPage={templatesState.nextPage} page={templatesState.page} previousPage={templatesState.previousPage} />
      <DocumentTemplateDialog onClose={() => setDialog({ open: false, template: null })} onSaved={async () => { setFeedback("Document template saved."); await templatesState.reload(); }} open={dialog.open} template={dialog.template} />
    </section>
  );
}

export function DocumentVersionsPanel({ document: file }) {
  const versions = Array.isArray(file?.versions) ? file.versions : [];
  return (
    <section className="record-section document-versions">
      <header><h2>Stored versions</h2><span>{versions.length}</span></header>
      {!versions.length ? <EmptyState icon={FolderSync} title="No stored versions" message="A successful Drive sync stores a checksummed local PDF version here." /> : (
        <ol>
          {versions.map((version) => <li key={version.id}><div><span><strong>Version {version.version_number}</strong>{version.issued ? <Badge tone="positive">Issued</Badge> : <Badge tone="neutral">Working</Badge>}</span><p>{version.mime_type} Â· {Number(version.size_bytes || 0).toLocaleString("en-GB")} bytes Â· {version.source || "local"}</p><code>{version.checksum_sha256}</code></div><time dateTime={version.created_at}>{formatDate(version.created_at, { withTime: true })}</time></li>)}
        </ol>
      )}
    </section>
  );
}
