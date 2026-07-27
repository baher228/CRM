import React, { useEffect, useId, useRef } from "react";
import { AlertTriangle, ArrowRight, ChevronLeft, ChevronRight, Inbox, LoaderCircle, RefreshCw, WifiOff, X } from "lucide-react";
import { Link } from "react-router-dom";

import { formatDate, formatMoney, initials, recordName, statusTone, titleCase } from "../utils/format";

export function Badge({ children, tone, className = "" }) {
  const resolvedTone = tone || statusTone(children);
  return <span className={`badge badge-${resolvedTone} ${className}`.trim()}>{children}</span>;
}

export function Avatar({ name, size = "md" }) {
  return <span aria-hidden="true" className={`avatar avatar-${size}`}>{initials(name)}</span>;
}

export function SignalLine({ label = "Local workspace", tone = "live" }) {
  return (
    <span className={`signal signal-${tone}`}>
      <span aria-hidden="true" className="signal-dot" />
      {label}
    </span>
  );
}

export function LoadingState({ label = "Loading workspace" }) {
  return (
    <div aria-busy="true" className="loading-state" role="status">
      <LoaderCircle aria-hidden="true" className="spin" size={18} />
      <span>{label}</span>
      <div aria-hidden="true" className="skeleton-lines"><i /><i /><i /></div>
    </div>
  );
}

export function EmptyState({ icon: Icon = Inbox, title = "Nothing here yet", message, action }) {
  return (
    <div className="empty-state">
      <span className="empty-icon"><Icon aria-hidden="true" size={22} /></span>
      <h2>{title}</h2>
      <p>{message || "Create the first record when you are ready."}</p>
      {action}
    </div>
  );
}

export function UnavailableState({ error, onRetry, compact = false }) {
  const isMissing = error?.status === 404;
  const Icon = isMissing ? AlertTriangle : WifiOff;
  return (
    <div className={`unavailable-state ${compact ? "unavailable-compact" : ""}`} role="status">
      <Icon aria-hidden="true" size={20} />
      <div>
        <strong>{isMissing ? "Workspace module is preparing" : "Local service unavailable"}</strong>
        <p>{isMissing ? "This area will appear as soon as its local API is ready." : error?.message || "Reconnect to the local CRM service, then try again."}</p>
        {error?.requestId ? <small>Request {error.requestId}</small> : null}
      </div>
      {onRetry ? (
        <button className="button button-quiet" onClick={onRetry} type="button">
          <RefreshCw aria-hidden="true" size={15} /> Retry
        </button>
      ) : null}
    </div>
  );
}

export function Metric({ label, value, note, tone = "default" }) {
  return (
    <div className={`metric metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {note ? <small>{note}</small> : null}
    </div>
  );
}

export function PageControls({ hasNext, hasPrevious, label = "Results", nextPage, page, previousPage }) {
  if (!hasPrevious && !hasNext) return null;
  return (
    <nav aria-label={`${label} pages`} className="pagination-controls">
      <button className="button button-quiet" disabled={!hasPrevious} onClick={previousPage} type="button"><ChevronLeft aria-hidden="true" size={15} /> Previous</button>
      <span aria-live="polite">Page {page}</span>
      <button className="button button-quiet" disabled={!hasNext} onClick={nextPage} type="button">Next <ChevronRight aria-hidden="true" size={15} /></button>
    </nav>
  );
}

function firstValue(record, keys) {
  return keys.map((key) => record?.[key]).find((value) => value !== null && value !== undefined && value !== "");
}

export function displayField(record, keys = []) {
  const value = firstValue(record, keys);
  if (value === undefined) return "—";
  const key = keys.find((candidate) => record?.[candidate] === value) || "";
  if (key.includes("minor") || key.includes("pence")) return formatMoney(value, record.currency || "GBP");
  if (key.includes("_at") || key.includes("_on") || key.includes("date") || key === "deadline" || key === "expected_close") return formatDate(value);
  if (key.includes("percent")) return `${value}%`;
  if (key.includes("minutes")) return `${Math.floor(Number(value) / 60)}h ${Number(value) % 60}m`;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

export function RecordCard({ record, config, dense = false }) {
  const title = recordName(record, `Untitled ${config.singular}`);
  const status = record.status || record.triage_status || record.state || record.lifecycle_status || record.computed_health || record.health || record.health_status;
  const recordId = config.endpoint === "client-success" ? record.account_id : record.id;
  const path = `${config.path}/${recordId}`;
  return (
    <article className={`record-card ${dense ? "record-card-dense" : ""}`}>
      <div className="record-card-main">
        <Avatar name={title} size={dense ? "sm" : "md"} />
        <div>
          <Link className="record-title" to={path}>{title}</Link>
          <span>{record.subtitle || record.account_name || record.company_name || record.email || titleCase(config.singular)}</span>
        </div>
        {status ? <Badge>{titleCase(status)}</Badge> : null}
      </div>
      <dl className="record-facts">
        {config.fields.slice(0, dense ? 2 : 4).map(([label, ...keys]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{displayField(record, keys)}</dd>
          </div>
        ))}
      </dl>
      <Link aria-label={`Open ${title}`} className="record-open" to={path}>
        Open <ArrowRight aria-hidden="true" size={15} />
      </Link>
    </article>
  );
}

function trapDialogFocus(event) {
  if (event.key !== "Tab") return;
  const dialog = event.currentTarget;
  const focusable = [...dialog.querySelectorAll(
    "a[href], button:not([disabled]), input:not([disabled]):not([type='hidden']), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
  )].filter((element) => element.getClientRects().length > 0);
  if (!focusable.length) {
    event.preventDefault();
    return;
  }
  const [first] = focusable;
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
    event.preventDefault();
    last.focus({ preventScroll: true });
  } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
    event.preventDefault();
    first.focus({ preventScroll: true });
  }
}

export function AppDialog({ open, title, description, onClose, children, className = "" }) {
  const dialogRef = useRef(null);
  const titleId = useId();
  const descriptionId = useId();
  const triggerRef = useRef(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      triggerRef.current = document.activeElement;
      dialog.showModal();
      const initialFocus = dialog.querySelector("[data-dialog-initial-focus]")
        || dialog.querySelector(".command-input input, form input:not([type='hidden']):not([disabled]), form select:not([disabled]), form textarea:not([disabled]), form button:not([disabled])")
        || dialog.querySelector("button:not([disabled])");
      initialFocus?.focus({ preventScroll: true });
    } else if (!open && dialog.open) {
      dialog.close();
      triggerRef.current?.focus?.({ preventScroll: true });
    }
  }, [open]);

  return (
    <dialog
      aria-describedby={description ? descriptionId : undefined}
      aria-labelledby={titleId}
      className={`app-dialog ${className}`}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => event.target === event.currentTarget && onClose()}
      onKeyDown={trapDialogFocus}
      ref={dialogRef}
    >
      <div className="dialog-panel">
        <header>
          <div>
            <h2 id={titleId}>{title}</h2>
            {description ? <p id={descriptionId}>{description}</p> : null}
          </div>
          <button aria-label={`Close ${title}`} className="icon-button" onClick={onClose} type="button">
            <X aria-hidden="true" size={19} />
          </button>
        </header>
        {children}
      </div>
    </dialog>
  );
}
