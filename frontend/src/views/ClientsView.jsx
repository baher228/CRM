import React, { useState } from "react";
import { Trash2, UserPlus } from "lucide-react";

import { createClient, deleteClient } from "../api";
import { ConfirmDialog, StatusBadge, TableView } from "../components/common";
import { formatCurrency, formatDate, formatDomain } from "../utils/format";

const emptyForm = {
  name: "",
  company: "",
  email: "",
  phone: "",
  website: "",
  owner: "",
  status: "Active",
  source: "",
  value: "",
  last_contact: "",
  next_action: "",
  notes: "",
};

export function ClientsView({ rows, onClientCreated, onClientDeleted }) {
  const [showAddForm, setShowAddForm] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);
  const [busyClientId, setBusyClientId] = useState(null);
  const [clientToDelete, setClientToDelete] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  function updateField(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (!form.name.trim() || saving) {
      return;
    }

    setSaving(true);
    setMessage("");
    setError("");
    try {
      const created = await createClient({
        name: form.name.trim(),
        company: form.company.trim(),
        email: form.email.trim() || null,
        phone: form.phone.trim(),
        website: form.website.trim(),
        owner: form.owner.trim(),
        status: form.status,
        source: form.source.trim(),
        value: Number(form.value || 0),
        last_contact: form.last_contact || null,
        next_action: form.next_action.trim(),
        notes: form.notes.trim(),
      });
      onClientCreated(created);
      setMessage(created.last_sync_message || "Contact saved");
      setForm(emptyForm);
      setShowAddForm(false);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  async function confirmDeleteClient() {
    if (!clientToDelete) {
      return;
    }

    setBusyClientId(clientToDelete.id);
    setMessage("");
    setError("");
    try {
      await deleteClient(clientToDelete.id);
      onClientDeleted(clientToDelete.id);
      setMessage("Contact deleted");
      setClientToDelete(null);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setBusyClientId(null);
    }
  }

  return (
    <div className="clients-workspace">
      <div className="view-actions">
        <span aria-live="polite" className={error ? "form-message form-error" : "form-message"}>
          {error || message}
        </span>
        <button className="secondary-action action-with-icon" onClick={() => setShowAddForm((open) => !open)} type="button">
          <UserPlus size={17} aria-hidden="true" />
          {showAddForm ? "Close" : "Add contact"}
        </button>
      </div>

      {showAddForm ? (
        <form className="workflow-panel compact-form" onSubmit={handleSubmit}>
          <div className="field-grid client-field-grid">
            <label>
              <span>Name</span>
              <input
                onChange={(event) => updateField("name", event.target.value)}
                placeholder="Jane Smith"
                required
                type="text"
                value={form.name}
              />
            </label>
            <label>
              <span>Company</span>
              <input
                onChange={(event) => updateField("company", event.target.value)}
                placeholder="Acme Ltd"
                type="text"
                value={form.company}
              />
            </label>
            <label>
              <span>Email</span>
              <input
                onChange={(event) => updateField("email", event.target.value)}
                placeholder="jane@example.com"
                type="email"
                value={form.email}
              />
            </label>
            <label>
              <span>Phone</span>
              <input
                onChange={(event) => updateField("phone", event.target.value)}
                placeholder="+44..."
                type="tel"
                value={form.phone}
              />
            </label>
            <label>
              <span>Website</span>
              <input
                onChange={(event) => updateField("website", event.target.value)}
                placeholder="https://example.com"
                type="url"
                value={form.website}
              />
            </label>
            <label>
              <span>Owner</span>
              <input
                onChange={(event) => updateField("owner", event.target.value)}
                placeholder="Maya"
                type="text"
                value={form.owner}
              />
            </label>
            <label>
              <span>Status</span>
              <select onChange={(event) => updateField("status", event.target.value)} value={form.status}>
                {["Active", "Prospect", "Customer", "Paused", "Archived"].map((status) => (
                  <option key={status} value={status}>
                    {status}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Source</span>
              <input
                onChange={(event) => updateField("source", event.target.value)}
                placeholder="Referral"
                type="text"
                value={form.source}
              />
            </label>
            <label>
              <span>Value</span>
              <input
                min="0"
                onChange={(event) => updateField("value", event.target.value)}
                placeholder="0"
                type="number"
                value={form.value}
              />
            </label>
            <label>
              <span>Last contact</span>
              <input
                onChange={(event) => updateField("last_contact", event.target.value)}
                type="date"
                value={form.last_contact}
              />
            </label>
            <label className="wide-field">
              <span>Next action</span>
              <input
                onChange={(event) => updateField("next_action", event.target.value)}
                placeholder="Send proposal"
                type="text"
                value={form.next_action}
              />
            </label>
            <label className="wide-field">
              <span>Notes</span>
              <input
                onChange={(event) => updateField("notes", event.target.value)}
                placeholder="Relationship notes"
                type="text"
                value={form.notes}
              />
            </label>
          </div>
          <div className="workflow-actions form-actions">
            <button disabled={!form.name.trim() || saving} type="submit">
              <UserPlus size={17} aria-hidden="true" />
              {saving ? "Saving..." : "Save contact"}
            </button>
          </div>
        </form>
      ) : null}

      <TableView
        label="Contacts"
        columns={["Name", "Company", "Website", "Status", "Owner", "Value", "Next action", "Sync"]}
        rows={rows}
        renderRow={(client) => (
          <tr key={client.id}>
            <td>
              <strong>{client.name}</strong>
              <span>{client.email || "-"}</span>
              {client.phone ? <span>{client.phone}</span> : null}
            </td>
            <td>{client.company || "-"}</td>
            <td>
              {client.website ? (
                <a className="domain-link" href={client.website} rel="noreferrer" target="_blank">
                  {formatDomain(client.website)}
                </a>
              ) : (
                "-"
              )}
            </td>
            <td>
              <StatusBadge tone={client.status === "Archived" ? "yellow" : "green"}>{client.status || "Active"}</StatusBadge>
              {client.source ? <span>{client.source}</span> : null}
            </td>
            <td>{client.owner || "-"}</td>
            <td>{formatCurrency(client.value || 0)}</td>
            <td>
              {client.next_action || "-"}
              <span>Last contact: {formatDate(client.last_contact)}</span>
            </td>
            <td>
              {client.last_sync_message || client.sync_status || "-"}
              <div className="row-actions">
                <button className="reject-action" disabled={busyClientId === client.id} onClick={() => setClientToDelete(client)} type="button">
                  <Trash2 size={14} aria-hidden="true" />
                  Delete
                </button>
              </div>
            </td>
          </tr>
        )}
      />

      <ConfirmDialog
        busy={Boolean(clientToDelete && busyClientId === clientToDelete.id)}
        description={clientToDelete ? `${clientToDelete.name} will be removed from the local CRM.` : ""}
        onCancel={() => setClientToDelete(null)}
        onConfirm={confirmDeleteClient}
        open={Boolean(clientToDelete)}
        title="Delete contact?"
      />
    </div>
  );
}
