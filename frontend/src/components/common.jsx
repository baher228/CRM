import React, { useEffect, useRef } from "react";

export function StatusBadge({ children, tone = "neutral" }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function DataState({ loading, error, isEmpty, children, emptyMessage = "No records yet." }) {
  if (loading) {
    return (
      <div aria-busy="true" aria-live="polite" className="state" role="status">
        <strong>Loading</strong>
        <span>Fetching the latest CRM data.</span>
      </div>
    );
  }

  if (error) {
    return (
      <div aria-live="assertive" className="state state-error" role="alert">
        <strong>Something went wrong</strong>
        <span>{error}</span>
      </div>
    );
  }

  if (isEmpty) {
    return (
      <div className="state state-empty" role="status">
        <strong>No records yet</strong>
        <span>{emptyMessage}</span>
      </div>
    );
  }

  return children;
}

export function TableView({ columns, rows, renderRow, label = "CRM records", emptyMessage = "No records match this view." }) {
  return (
    <div aria-label={label} className="table-wrap" role="region" tabIndex="0">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} scope="col">{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map(renderRow)
          ) : (
            <tr className="table-empty-row">
              <td colSpan={columns.length}>
                <strong>No records</strong>
                <span>{emptyMessage}</span>
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Delete",
  cancelLabel = "Cancel",
  tone = "danger",
  busy = false,
  onConfirm,
  onCancel,
}) {
  const dialogRef = useRef(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) {
      return;
    }

    if (open && !dialog.open) {
      dialog.showModal();
      dialog.querySelector("[data-dialog-cancel]")?.focus();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  return (
    <dialog
      aria-labelledby="confirm-dialog-title"
      className={`confirm-dialog confirm-dialog-${tone}`}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) {
          onCancel?.();
        }
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget && !busy) {
          onCancel?.();
        }
      }}
      ref={dialogRef}
    >
      <div className="confirm-dialog-panel">
        <div>
          <h2 id="confirm-dialog-title">{title}</h2>
          {description ? <p>{description}</p> : null}
        </div>
        <div className="confirm-dialog-actions">
          <button data-dialog-cancel disabled={busy} onClick={onCancel} type="button">
            {cancelLabel}
          </button>
          <button className="dialog-confirm" disabled={busy} onClick={onConfirm} type="button">
            {busy ? "Working..." : confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}
