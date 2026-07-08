import React, { useState } from "react";
import { UserPlus } from "lucide-react";

import { createClient } from "../api";
import { TableView } from "../components/common";
import { formatCurrency, formatDate, formatDomain } from "../utils/format";

export function ClientsView({ rows, onClientCreated }) {
  const [showAddForm, setShowAddForm] = useState(false);
  const [form, setForm] = useState({
    name: "",
    company: "",
    email: "",
    phone: "",
    website: "",
    owner: "",
    value: "",
    last_contact: "",
  });
  const [saving, setSaving] = useState(false);
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
        value: Number(form.value || 0),
        last_contact: form.last_contact || null,
      });
      onClientCreated(created);
      setMessage(created.last_sync_message || "Contact saved");
      setForm({
        name: "",
        company: "",
        email: "",
        phone: "",
        website: "",
        owner: "",
        value: "",
        last_contact: "",
      });
      setShowAddForm(false);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="clients-workspace">
      <div className="view-actions">
        <span className={error ? "form-message form-error" : "form-message"}>
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
        columns={["Name", "Company", "Website", "Owner", "Value", "Last contact", "Sync"]}
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
            <td>{client.owner || "-"}</td>
            <td>{formatCurrency(client.value || 0)}</td>
            <td>{formatDate(client.last_contact)}</td>
            <td>{client.last_sync_message || "-"}</td>
          </tr>
        )}
      />
    </div>
  );
}
