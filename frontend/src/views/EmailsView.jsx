import React, { useEffect, useState } from "react";
import { Mail, RefreshCw, Save } from "lucide-react";

import { fetchMailSettings, fetchResource, saveMailSettings } from "../api";
import { StatusBadge } from "../components/common";
import { formatDateTime } from "../utils/format";

const emptyForm = {
  host: "",
  port: 993,
  username: "",
  password: "",
  folder: "INBOX",
  use_ssl: true,
};

export function EmailsView() {
  const [rows, setRows] = useState([]);
  const [form, setForm] = useState(emptyForm);
  const [configured, setConfigured] = useState(false);
  const [passwordSaved, setPasswordSaved] = useState(false);
  const [loading, setLoading] = useState(true);
  const [inboxLoading, setInboxLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      try {
        const settings = await fetchMailSettings();
        if (cancelled) {
          return;
        }
        applySettings(settings);
        if (settings.configured) {
          await loadInbox();
        }
      } catch (requestError) {
        if (!cancelled) {
          setError(requestError.message);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, []);

  function applySettings(settings) {
    setConfigured(settings.configured);
    setPasswordSaved(settings.password_saved);
    setForm({
      host: settings.host || "",
      port: settings.port || 993,
      username: settings.username || "",
      password: "",
      folder: settings.folder || "INBOX",
      use_ssl: settings.use_ssl !== false,
    });
  }

  function updateField(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function loadInbox() {
    setInboxLoading(true);
    setError("");
    try {
      setRows(await fetchResource("emails"));
      setMessage("Inbox refreshed");
    } catch (requestError) {
      setRows([]);
      setError(requestError.message);
    } finally {
      setInboxLoading(false);
    }
  }

  async function handleSave(event) {
    event.preventDefault();
    if (!form.host.trim() || !form.username.trim() || saving) {
      return;
    }

    setSaving(true);
    setError("");
    setMessage("");
    try {
      const settings = await saveMailSettings({
        ...form,
        host: form.host.trim(),
        username: form.username.trim(),
        folder: form.folder.trim() || "INBOX",
        port: Number(form.port || 993),
      });
      applySettings(settings);
      setMessage("Mailbox saved");
      await loadInbox();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <div aria-busy="true" className="state" role="status">Loading mail settings...</div>;
  }

  return (
    <div className="emails-workspace">
      <form className="workflow-panel compact-form" onSubmit={handleSave}>
        <div className="section-heading">
          <div>
            <span>Mailbox</span>
            <h2>IMAP account</h2>
          </div>
          <StatusBadge tone={configured ? "green" : "yellow"}>
            {configured ? "Connected" : "Not connected"}
          </StatusBadge>
        </div>

        <div className="field-grid email-settings-grid">
          <label>
            <span>IMAP host</span>
            <input
              onChange={(event) => updateField("host", event.target.value)}
              placeholder="imap.gmail.com"
              required
              type="text"
              value={form.host}
            />
          </label>
          <label>
            <span>Port</span>
            <input
              max="65535"
              min="1"
              onChange={(event) => updateField("port", event.target.value)}
              required
              type="number"
              value={form.port}
            />
          </label>
          <label>
            <span>Email / username</span>
            <input
              onChange={(event) => updateField("username", event.target.value)}
              placeholder="you@example.com"
              required
              type="text"
              value={form.username}
            />
          </label>
          <label>
            <span>Password</span>
            <input
              onChange={(event) => updateField("password", event.target.value)}
              placeholder={passwordSaved ? "Saved - leave blank to keep" : "App password"}
              type="password"
              value={form.password}
            />
          </label>
          <label>
            <span>Folder</span>
            <input
              onChange={(event) => updateField("folder", event.target.value)}
              placeholder="INBOX"
              type="text"
              value={form.folder}
            />
          </label>
          <label className="toggle-row email-toggle-row">
            <input
              checked={form.use_ssl}
              onChange={(event) => updateField("use_ssl", event.target.checked)}
              type="checkbox"
            />
            Use SSL
          </label>
        </div>

        <div className="workflow-actions form-actions">
          <span aria-live="polite" className={error ? "form-message form-error" : "form-message"}>{error || message}</span>
          <button disabled={!form.host.trim() || !form.username.trim() || saving} type="submit">
            <Save size={17} aria-hidden="true" />
            {saving ? "Saving..." : "Save mailbox"}
          </button>
        </div>
      </form>

      <div className="view-actions">
        <button className="secondary-action action-with-icon" disabled={!configured || inboxLoading} onClick={loadInbox} type="button">
          <RefreshCw size={16} aria-hidden="true" />
          {inboxLoading ? "Refreshing..." : "Refresh inbox"}
        </button>
      </div>

      {rows.length ? (
        <div className="email-list">
          {rows.map((email) => (
            <article className={`email-item ${email.unread ? "unread" : ""}`} key={email.id}>
              <div className="email-topline">
                <div>
                  <h3>{email.subject}</h3>
                  <span>
                    {email.from_name || "Unknown"} - {email.from_email}
                  </span>
                </div>
                <StatusBadge tone={email.priority === "High" ? "red" : "yellow"}>
                  {email.priority}
                </StatusBadge>
              </div>
              <p>{email.preview}</p>
              <time>{formatDateTime(email.received_at)}</time>
            </article>
          ))}
        </div>
      ) : (
        <div className="state compact-state" role="status">
          <Mail size={18} aria-hidden="true" />
          {configured ? "No messages loaded yet." : "Add mailbox settings to load your inbox."}
        </div>
      )}
    </div>
  );
}
