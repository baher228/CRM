import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  CalendarPlus,
  CalendarDays,
  Mail,
  Megaphone,
  UserPlus,
  Search,
  Users,
  WalletCards,
} from "lucide-react";
import {
  confirmLead,
  createCalendarItem,
  createClient,
  fetchDiscoveryJob,
  fetchResource,
  rejectLead,
  startDiscoveryJob,
  updateLead,
} from "./api";
import "./styles.css";

const tabs = [
  { id: "clients", label: "Clients", icon: Users },
  { id: "leads", label: "Leads", icon: Megaphone },
  { id: "events", label: "Events", icon: WalletCards },
  { id: "emails", label: "Emails", icon: Mail },
  { id: "calendar", label: "Calendar", icon: CalendarDays },
  { id: "discover", label: "Discover", icon: Search },
];

const DISCOVERY_PORTAL_OPTIONS = [
  { name: "Find a Tender Service", base: 100, label: "Core", tokens: ["uk", "england", "public"] },
  { name: "Contracts Finder", base: 95, label: "Core", tokens: ["uk", "england", "sme", "local"] },
  { name: "Public Contracts Scotland", base: 72, label: "Regional", tokens: ["scotland", "glasgow", "edinburgh"] },
  { name: "Sell2Wales", base: 72, label: "Regional", tokens: ["wales", "cardiff", "swansea"] },
  { name: "eTendersNI", base: 68, label: "Regional", tokens: ["northern ireland", "belfast", "ni"] },
  { name: "eSourcingNI / NIHE", base: 66, label: "Regional", tokens: ["northern ireland", "belfast", "housing", "repairs", "facilities"] },
  { name: "TED / Tenders Electronic Daily", base: 38, label: "EU", tokens: ["eu", "europe", "cross-border", "international"] },
  { name: "London Tenders Portal", base: 74, label: "Regional", tokens: ["london"] },
  { name: "The Chest", base: 70, label: "Regional", tokens: ["north west", "manchester", "liverpool", "lancashire", "cheshire"] },
  { name: "NHS Supply Chain / Jaggaer", base: 62, label: "Healthcare", tokens: ["nhs", "healthcare", "clinical", "medical", "hospital", "estates", "facilities"] },
  { name: "NHS Shared Business Services", base: 62, label: "Framework", tokens: ["nhs", "healthcare", "framework", "medical", "hospital"] },
  { name: "Crown Commercial Service (CCS)", base: 60, label: "Framework", tokens: ["framework", "dps", "supplier", "ccs", "procurement route"] },
  { name: "ESPO", base: 56, label: "Framework", tokens: ["framework", "supplier", "espo"] },
  { name: "YPO", base: 56, label: "Framework", tokens: ["framework", "supplier", "ypo", "yorkshire"] },
  { name: "NEPO", base: 58, label: "Regional", tokens: ["north east", "newcastle", "durham", "sunderland", "tees valley"] },
];

const DISCOVERY_PORTALS = DISCOVERY_PORTAL_OPTIONS.map((portal) => portal.name);

const DEFAULT_DISCOVERY_PORTALS = DISCOVERY_PORTAL_OPTIONS
  .filter((portal) => portal.label !== "EU")
  .map((portal) => portal.name);
const PORTAL_SELECTION_STORAGE_KEY = "crm.discovery.selectedPortals";

const storedDiscoveryPortals = () => {
  if (typeof window === "undefined") {
    return DEFAULT_DISCOVERY_PORTALS;
  }

  try {
    const parsed = JSON.parse(window.localStorage.getItem(PORTAL_SELECTION_STORAGE_KEY) || "null");
    if (!Array.isArray(parsed)) {
      return DEFAULT_DISCOVERY_PORTALS;
    }

    const validPortals = parsed.filter((portal, index) =>
      DISCOVERY_PORTALS.includes(portal) && parsed.indexOf(portal) === index
    );
    return parsed.length && !validPortals.length ? DEFAULT_DISCOVERY_PORTALS : validPortals;
  } catch {
    return DEFAULT_DISCOVERY_PORTALS;
  }
};

const formatCurrency = (value) =>
  new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: "GBP",
    maximumFractionDigits: 0,
  }).format(value);

const formatDate = (value) =>
  new Intl.DateTimeFormat("en-GB", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));

const formatDateTime = (value) =>
  new Intl.DateTimeFormat("en-GB", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));

const formatDomain = (url) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};

const isBadSourceUrl = (url) => {
  try {
    const parsed = new URL(url);
    return parsed.hostname.endsWith("contractsfinder.service.gov.uk")
      && parsed.pathname.toLowerCase().startsWith("/search/");
  } catch {
    return true;
  }
};

const isKnown = (value) => Boolean(value && String(value).trim() && String(value).trim() !== "Unknown");
const showValue = (value, fallback = "-") => (isKnown(value) ? value : fallback);
const firstKnown = (...values) => values.find((value) => isKnown(value));

const priorityTone = (label) =>
  ({ Hot: "red", Warm: "green", Watch: "yellow", Low: "blue" })[label] || "blue";

const availabilityTone = (status) =>
  ({ Available: "green", Unavailable: "red", Unverified: "yellow" })[status] || "yellow";

const portalMatchScore = (portal, niche, region) => {
  const text = `${niche} ${region}`.toLowerCase();
  const tokenBoost = portal.tokens.some((token) => text.includes(token)) ? 35 : 0;
  const healthcareBoost = /nhs|healthcare|clinical|medical|hospital|estates|facilities/.test(text)
    && portal.label === "Healthcare" ? 35 : 0;
  const frameworkBoost = /framework|dps|supplier|procurement route|ccs|espo|ypo/.test(text)
    && portal.label === "Framework" ? 35 : 0;
  const euBoost = /eu|europe|cross-border|international|non-uk/.test(text)
    && portal.label === "EU" ? 45 : 0;
  return portal.base + tokenBoost + healthcareBoost + frameworkBoost + euBoost;
};

function StatusBadge({ children, tone = "neutral" }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

function DataState({ loading, error, isEmpty, children }) {
  if (loading) {
    return <div className="state">Loading CRM data...</div>;
  }

  if (error) {
    return <div className="state state-error">{error}</div>;
  }

  if (isEmpty) {
    return <div className="state">No records yet.</div>;
  }

  return children;
}

function TableView({ columns, rows, renderRow }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>{rows.map(renderRow)}</tbody>
      </table>
    </div>
  );
}

function ClientsView({ rows, onClientCreated }) {
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

function LeadsView({ rows, onLeadUpdated }) {
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("All");
  const [portalFilter, setPortalFilter] = useState("All");
  const [availabilityFilter, setAvailabilityFilter] = useState("Open/Unverified");
  const [sortMode, setSortMode] = useState("priority");
  const [selectedLead, setSelectedLead] = useState(null);
  const [busyLeadId, setBusyLeadId] = useState(null);
  const [actionError, setActionError] = useState("");
  const tones = {
    New: "blue",
    Reviewing: "yellow",
    Confirmed: "green",
    "Needs Contact": "yellow",
    Rejected: "red",
    Contacted: "yellow",
    Qualified: "green",
    Proposal: "purple",
  };

  const portals = ["All", ...Array.from(new Set(rows.map((lead) => firstKnown(lead.portal_name, lead.source)).filter(Boolean)))];
  const statuses = ["All", ...Array.from(new Set(rows.map((lead) => lead.status).filter(Boolean)))];
  const filteredRows = rows
    .filter((lead) => statusFilter === "All" || lead.status === statusFilter)
    .filter((lead) => portalFilter === "All" || firstKnown(lead.portal_name, lead.source) === portalFilter)
    .filter((lead) => {
      const availability = lead.availability_status || "Unverified";
      if (availabilityFilter === "All") {
        return true;
      }
      if (availabilityFilter === "Open/Unverified") {
        return availability !== "Unavailable";
      }
      return availability === availabilityFilter;
    })
    .filter((lead) => {
      const haystack = [
        lead.name,
        lead.company,
        lead.contract_title,
        lead.buyer_name,
        lead.portal_name,
        lead.source,
        lead.outreach_angle,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(query.trim().toLowerCase());
    })
    .sort((left, right) => {
      if (sortMode === "priority") {
        return (
          (right.priority_score || 0) - (left.priority_score || 0)
          || parseDateish(left.deadline) - parseDateish(right.deadline)
          || (right.confidence_score || 0) - (left.confidence_score || 0)
        );
      }
      if (sortMode === "confidence") {
        return (right.confidence_score || 0) - (left.confidence_score || 0);
      }
      if (sortMode === "newest") {
        return Date.parse(right.created_at || 0) - Date.parse(left.created_at || 0);
      }
      return parseDateish(left.deadline) - parseDateish(right.deadline);
    });

  const leadTitle = (lead) => firstKnown(lead.contract_title, lead.name) || "Untitled tender";
  const buyerName = (lead) => firstKnown(lead.buyer_name, lead.company) || "Buyer not parsed yet";
  const portalName = (lead) => firstKnown(lead.portal_name, lead.source) || "-";
  const leadUrl = (lead) =>
    [lead.contract_url, ...(lead.source_urls || []), lead.website].find((url) => isKnown(url) && !isBadSourceUrl(url)) || "";
  const leadValue = (lead) =>
    isKnown(lead.contract_value) ? lead.contract_value : lead.estimated_value ? formatCurrency(lead.estimated_value) : "-";
  const leadDetailValue = (lead) =>
    isKnown(lead.contract_value) ? lead.contract_value : lead.estimated_value ? formatCurrency(lead.estimated_value) : "Value not parsed";
  const hasDecision = (lead) =>
    lead.status === "Confirmed" || lead.status === "Rejected" || Boolean(lead.confirmed_at) || Boolean(lead.rejected_at);
  const canMarkReviewing = (lead) => !hasDecision(lead) && lead.status !== "Reviewing";

  async function runAction(lead, action) {
    setBusyLeadId(lead.id);
    setActionError("");
    try {
      const updated = action === "confirm" ? await confirmLead(lead.id) : await rejectLead(lead.id);
      onLeadUpdated(updated);
      setSelectedLead(updated);
    } catch (error) {
      setActionError(error.message);
    } finally {
      setBusyLeadId(null);
    }
  }

  async function markReviewing(lead) {
    setBusyLeadId(lead.id);
    setActionError("");
    try {
      const updated = await updateLead(lead.id, { status: "Reviewing" });
      onLeadUpdated(updated);
      setSelectedLead(updated);
    } catch (error) {
      setActionError(error.message);
    } finally {
      setBusyLeadId(null);
    }
  }

  if (selectedLead) {
    const sources = (selectedLead.source_urls?.length
      ? [selectedLead.contact_source_url, leadUrl(selectedLead), ...selectedLead.source_urls]
      : [selectedLead.contact_source_url, leadUrl(selectedLead)]
    ).filter((url, index, all) => url && !isBadSourceUrl(url) && all.indexOf(url) === index);

    return (
      <div className="tender-review-page">
        <div className="review-topbar">
          <button className="secondary-action" onClick={() => setSelectedLead(null)} type="button">
            Back to leads
          </button>
          {!hasDecision(selectedLead) ? (
            <div className="review-actions">
              {canMarkReviewing(selectedLead) ? (
                <button className="secondary-action" disabled={busyLeadId === selectedLead.id} onClick={() => markReviewing(selectedLead)} type="button">
                  Mark reviewing
                </button>
              ) : null}
              <button className="confirm-action" disabled={busyLeadId === selectedLead.id} onClick={() => runAction(selectedLead, "confirm")} type="button">
                Confirm
              </button>
              <button className="reject-action" disabled={busyLeadId === selectedLead.id} onClick={() => runAction(selectedLead, "reject")} type="button">
                Reject
              </button>
            </div>
          ) : null}
        </div>

        {actionError ? <div className="state state-error compact-state">{actionError}</div> : null}

        <section className="tender-hero">
          <div>
            <StatusBadge tone={tones[selectedLead.status]}>{selectedLead.status}</StatusBadge>
            <h2>{leadTitle(selectedLead)}</h2>
            <p>{buyerName(selectedLead)}</p>
          </div>
          {sources[0] ? (
            <a className="primary-source-link" href={sources[0]} rel="noreferrer" target="_blank">
              Open source
            </a>
          ) : null}
        </section>

        <div className="review-summary-grid">
          <article>
            <span>Priority</span>
            <strong>{selectedLead.priority_label || "Low"} {selectedLead.priority_score || 0}</strong>
          </article>
          <article>
            <span>Fit</span>
            <strong>{selectedLead.confidence_score || 0}</strong>
          </article>
          <article>
            <span>Portal</span>
            <strong>{portalName(selectedLead)}</strong>
          </article>
          <article>
            <span>Value</span>
            <strong>{leadValue(selectedLead)}</strong>
          </article>
          <article>
            <span>Deadline</span>
            <strong>{showValue(selectedLead.deadline, "Not parsed yet")}</strong>
          </article>
          <article>
            <span>Stage</span>
            <strong>{showValue(selectedLead.procurement_stage, "Not parsed yet")}</strong>
          </article>
          <article>
            <span>Availability</span>
            <strong>{selectedLead.availability_status || "Unverified"}</strong>
          </article>
        </div>

        <div className="review-sections">
          <section>
            <h3>Tender notes</h3>
            <p>{showValue(selectedLead.outreach_angle, "No notes parsed yet.")}</p>
          </section>
          <section>
            <h3>Availability</h3>
            <p>
              <StatusBadge tone={availabilityTone(selectedLead.availability_status)}>
                {selectedLead.availability_status || "Unverified"}
              </StatusBadge>
              {" "}
              {selectedLead.availability_reason || "No availability check saved yet."}
            </p>
          </section>
          <section>
            <h3>Priority reasons</h3>
            <div className="reason-list">
              {(selectedLead.priority_reasons?.length ? selectedLead.priority_reasons : ["No priority reasons yet"]).map((reason) => (
                <span key={reason}>{reason}</span>
              ))}
            </div>
          </section>
          <section>
            <h3>Contact</h3>
            <dl>
              <div>
                <dt>Name</dt>
                <dd>{showValue(selectedLead.contact_name, "No contact parsed yet")}</dd>
              </div>
              <div>
                <dt>Email</dt>
                <dd>{showValue(selectedLead.contact_email, "No email parsed yet")}</dd>
              </div>
              <div>
                <dt>Phone</dt>
                <dd>{showValue(selectedLead.contact_phone, "No phone parsed yet")}</dd>
              </div>
              <div>
                <dt>Source</dt>
                <dd>
                  {selectedLead.contact_source_url ? (
                    <a href={selectedLead.contact_source_url} rel="noreferrer" target="_blank">
                      {formatDomain(selectedLead.contact_source_url)}
                    </a>
                  ) : (
                    "No contact source yet"
                  )}
                </dd>
              </div>
            </dl>
          </section>
          <section>
            <h3>Attio sync</h3>
            <p>{selectedLead.last_sync_message || "Not synced yet"}</p>
          </section>
          <section className="draft-email-section">
            <h3>Draft email</h3>
            {selectedLead.draft_email_subject || selectedLead.draft_email_body ? (
              <div className="draft-email">
                <strong>{selectedLead.draft_email_subject || "No subject generated"}</strong>
                <p>{selectedLead.draft_email_body || "No body generated"}</p>
              </div>
            ) : (
              <p>Confirm this lead with a contact to generate a draft email.</p>
            )}
          </section>
          <section>
            <h3>Sources</h3>
            <div className="drawer-links">
              {sources.length ? (
                sources.map((url) => (
                  <a href={url} key={url} rel="noreferrer" target="_blank">
                    {formatDomain(url)}
                  </a>
                ))
              ) : (
                <span>No source link saved.</span>
              )}
            </div>
          </section>
          <section>
            <h3>Seen</h3>
            <p>
              Seen {selectedLead.seen_count || 1} time{(selectedLead.seen_count || 1) === 1 ? "" : "s"}
              {selectedLead.last_seen_at ? ` · Last seen ${formatDate(selectedLead.last_seen_at)}` : ""}
            </p>
          </section>
        </div>
      </div>
    );
  }

  return (
    <div className="leads-workspace">
      <div className="leads-toolbar">
        <input
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search tender, buyer, portal..."
          type="search"
          value={query}
        />
        <select onChange={(event) => setStatusFilter(event.target.value)} value={statusFilter}>
          {statuses.map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
        <select onChange={(event) => setPortalFilter(event.target.value)} value={portalFilter}>
          {portals.map((portal) => (
            <option key={portal} value={portal}>
              {portal}
            </option>
          ))}
        </select>
        <select onChange={(event) => setAvailabilityFilter(event.target.value)} value={availabilityFilter}>
          {["Open/Unverified", "Available", "Unverified", "Unavailable", "All"].map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
        <select onChange={(event) => setSortMode(event.target.value)} value={sortMode}>
          <option value="priority">Priority</option>
          <option value="deadline">Deadline</option>
          <option value="confidence">Confidence</option>
          <option value="newest">Newest</option>
        </select>
      </div>

      {actionError ? <div className="state state-error compact-state">{actionError}</div> : null}

      <div className="leads-layout">
        <TableView
          columns={["Lead", "Priority", "Status", "Fit", "Portal", "Details"]}
          rows={filteredRows}
          renderRow={(lead) => (
            <tr key={lead.id} className={selectedLead?.id === lead.id ? "selected-row" : ""}>
              <td className="lead-main-cell">
                <strong>{leadTitle(lead)}</strong>
                <span>{buyerName(lead)}</span>
                {isKnown(lead.procurement_stage) ? <span>Stage: {lead.procurement_stage}</span> : null}
                {isKnown(lead.outreach_angle) ? <span className="lead-note">{lead.outreach_angle}</span> : null}
                <div className="row-actions">
                  <button className="secondary-action" onClick={() => setSelectedLead(lead)} type="button">
                    Review
                  </button>
                  {!hasDecision(lead) ? (
                    <>
                      <button className="confirm-action" disabled={busyLeadId === lead.id} onClick={() => runAction(lead, "confirm")} type="button">
                        Confirm
                      </button>
                      <button className="reject-action" disabled={busyLeadId === lead.id} onClick={() => runAction(lead, "reject")} type="button">
                        Reject
                      </button>
                    </>
                  ) : null}
                  {leadUrl(lead) ? (
                    <a className="secondary-action" href={leadUrl(lead)} rel="noreferrer" target="_blank">
                      Source
                    </a>
                  ) : null}
                </div>
              </td>
              <td>
                <StatusBadge tone={priorityTone(lead.priority_label)}>
                  {lead.priority_label || "Low"} {lead.priority_score || 0}
                </StatusBadge>
              </td>
              <td>
                <StatusBadge tone={tones[lead.status]}>{lead.status}</StatusBadge>
              </td>
              <td>
                <StatusBadge tone={lead.confidence_score >= 85 ? "green" : "blue"}>
                  {lead.confidence_score}
                </StatusBadge>
              </td>
              <td>{portalName(lead)}</td>
              <td className="lead-detail-cell">
                <span>{leadDetailValue(lead)}</span>
                <span>{isKnown(lead.deadline) ? `Deadline: ${lead.deadline}` : "No deadline parsed"}</span>
                <span>
                  <StatusBadge tone={availabilityTone(lead.availability_status)}>
                    {lead.availability_status || "Unverified"}
                  </StatusBadge>
                </span>
              </td>
            </tr>
          )}
        />

      </div>
    </div>
  );
}

function parseDateish(value) {
  const timestamp = Date.parse(value || "");
  return Number.isNaN(timestamp) ? Number.MAX_SAFE_INTEGER : timestamp;
}

function EventsView({ rows }) {
  return (
    <div className="card-grid">
      {rows.map((event) => (
        <article className="record-card" key={event.id}>
          <div>
            <StatusBadge>{event.type}</StatusBadge>
            <h3>{event.title}</h3>
          </div>
          <dl>
            <div>
              <dt>Client</dt>
              <dd>{event.client}</dd>
            </div>
            <div>
              <dt>Starts</dt>
              <dd>{formatDateTime(event.starts_at)}</dd>
            </div>
            <div>
              <dt>Location</dt>
              <dd>{event.location}</dd>
            </div>
            <div>
              <dt>Owner</dt>
              <dd>{event.owner}</dd>
            </div>
          </dl>
        </article>
      ))}
    </div>
  );
}

function EmailsView({ rows }) {
  return (
    <div className="email-list">
      {rows.map((email) => (
        <article className={`email-item ${email.unread ? "unread" : ""}`} key={email.id}>
          <div className="email-topline">
            <div>
              <h3>{email.subject}</h3>
              <span>
                {email.from_name} - {email.from_email}
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
  );
}

function CalendarView({ rows, clients, onCalendarItemCreated }) {
  const [showAddForm, setShowAddForm] = useState(false);
  const [form, setForm] = useState({
    title: "",
    date: "",
    start_time: "",
    end_time: "",
    related_client_id: "",
    related_to: "",
    notes: "",
  });
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const groupedRows = rows.reduce((groups, item) => {
    groups[item.date] = groups[item.date] || [];
    groups[item.date].push(item);
    return groups;
  }, {});

  function updateField(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function selectClient(value) {
    const selected = clients.find((client) => String(client.id) === value);
    setForm((current) => ({
      ...current,
      related_client_id: value,
      related_to: selected ? selected.company || selected.name : current.related_to,
    }));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (!form.title.trim() || !form.date || !form.start_time || !form.end_time || saving) {
      return;
    }

    setSaving(true);
    setMessage("");
    setError("");
    try {
      const created = await createCalendarItem({
        title: form.title.trim(),
        date: form.date,
        start_time: form.start_time,
        end_time: form.end_time,
        related_client_id: form.related_client_id ? Number(form.related_client_id) : null,
        related_to: form.related_to.trim(),
        notes: form.notes.trim(),
      });
      onCalendarItemCreated(created);
      setMessage(created.last_sync_message || "Event saved");
      setForm({
        title: "",
        date: "",
        start_time: "",
        end_time: "",
        related_client_id: "",
        related_to: "",
        notes: "",
      });
      setShowAddForm(false);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="calendar-workspace">
      <div className="view-actions">
        <span className={error ? "form-message form-error" : "form-message"}>
          {error || message}
        </span>
        <button className="secondary-action action-with-icon" onClick={() => setShowAddForm((open) => !open)} type="button">
          <CalendarPlus size={17} aria-hidden="true" />
          {showAddForm ? "Close" : "Add event"}
        </button>
      </div>

      {showAddForm ? (
        <form className="workflow-panel compact-form" onSubmit={handleSubmit}>
          <div className="field-grid calendar-field-grid">
            <label>
              <span>Title</span>
              <input
                onChange={(event) => updateField("title", event.target.value)}
                placeholder="Follow-up call"
                required
                type="text"
                value={form.title}
              />
            </label>
            <label>
              <span>Date</span>
              <input
                onChange={(event) => updateField("date", event.target.value)}
                required
                type="date"
                value={form.date}
              />
            </label>
            <label>
              <span>Start</span>
              <input
                onChange={(event) => updateField("start_time", event.target.value)}
                required
                type="time"
                value={form.start_time}
              />
            </label>
            <label>
              <span>End</span>
              <input
                onChange={(event) => updateField("end_time", event.target.value)}
                required
                type="time"
                value={form.end_time}
              />
            </label>
            <label>
              <span>Contact</span>
              <select onChange={(event) => selectClient(event.target.value)} value={form.related_client_id}>
                <option value="">No linked contact</option>
                {clients.map((client) => (
                  <option key={client.id} value={client.id}>
                    {client.name}{client.company ? ` - ${client.company}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Related to</span>
              <input
                onChange={(event) => updateField("related_to", event.target.value)}
                placeholder="Company or opportunity"
                type="text"
                value={form.related_to}
              />
            </label>
            <label className="wide-field">
              <span>Notes</span>
              <input
                onChange={(event) => updateField("notes", event.target.value)}
                placeholder="Agenda, prep, next step"
                type="text"
                value={form.notes}
              />
            </label>
          </div>
          <div className="workflow-actions form-actions">
            <button disabled={!form.title.trim() || !form.date || !form.start_time || !form.end_time || saving} type="submit">
              <CalendarPlus size={17} aria-hidden="true" />
              {saving ? "Saving..." : "Save event"}
            </button>
          </div>
        </form>
      ) : null}

      <div className="agenda">
        {Object.entries(groupedRows).map(([date, items]) => (
          <section className="agenda-day" key={date}>
            <h3>{formatDate(date)}</h3>
            {items.map((item) => (
              <article className="agenda-item" key={item.id}>
                <time>
                  {item.start_time.slice(0, 5)} - {item.end_time.slice(0, 5)}
                </time>
                <div>
                  <strong>{item.title}</strong>
                  <span>{item.related_to || "No relation"}</span>
                  <p>{item.notes}</p>
                  {item.last_sync_message ? <em>{item.last_sync_message}</em> : null}
                </div>
              </article>
            ))}
          </section>
        ))}
      </div>
    </div>
  );
}

function DiscoverView({ onLeadsChanged }) {
  const [niche, setNiche] = useState("");
  const [region, setRegion] = useState("");
  const [limit, setLimit] = useState(5);
  const [limitMenuOpen, setLimitMenuOpen] = useState(false);
  const [selectedPortals, setSelectedPortals] = useState(storedDiscoveryPortals);
  const [portalMenuOpen, setPortalMenuOpen] = useState(false);
  const limitMenuRef = useRef(null);
  const portalMenuRef = useRef(null);
  const [deadlineWindow, setDeadlineWindow] = useState("");
  const [minimumValue, setMinimumValue] = useState("");
  const [openNoticesOnly, setOpenNoticesOnly] = useState(true);
  const [dryRun, setDryRun] = useState(true);
  const [jobId, setJobId] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const running = Boolean(jobId);

  useEffect(() => {
    if (!limitMenuOpen && !portalMenuOpen) {
      return undefined;
    }

    function handlePointerDown(event) {
      if (limitMenuRef.current && !limitMenuRef.current.contains(event.target)) {
        setLimitMenuOpen(false);
      }
      if (portalMenuRef.current && !portalMenuRef.current.contains(event.target)) {
        setPortalMenuOpen(false);
      }
    }

    function handleKeyDown(event) {
      if (event.key === "Escape") {
        setLimitMenuOpen(false);
        setPortalMenuOpen(false);
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [limitMenuOpen, portalMenuOpen]);

  useEffect(() => {
    if (running) {
      setLimitMenuOpen(false);
      setPortalMenuOpen(false);
    }
  }, [running]);

  useEffect(() => {
    try {
      window.localStorage.setItem(PORTAL_SELECTION_STORAGE_KEY, JSON.stringify(selectedPortals));
    } catch {
      // Discovery can still run if local storage is unavailable.
    }
  }, [selectedPortals]);

  useEffect(() => {
    if (!jobId) {
      return undefined;
    }

    let cancelled = false;

    async function poll() {
      try {
        const payload = await fetchDiscoveryJob(jobId);
        if (cancelled) {
          return;
        }
        setResult(payload);
        if (payload.state !== "running") {
          if (payload.state === "completed") {
            onLeadsChanged();
          }
          setJobId("");
        }
      } catch (requestError) {
        if (!cancelled) {
          setError(requestError.message);
          setJobId("");
        }
      }
    }

    poll();
    const timer = window.setInterval(poll, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [jobId]);

  async function handleSubmit(event) {
    event.preventDefault();

    if (!niche.trim() || running) {
      return;
    }

    setError("");
    setResult({
      state: "running",
      phase: "queued",
      message: "Discovery queued",
      elapsed_seconds: 0,
      completed: 0,
      total: Number(limit),
      dry_run: dryRun,
      discovered: 0,
      upserted: 0,
      failed: 0,
      results: [],
    });

    try {
      const payload = await startDiscoveryJob({
        niche: niche.trim(),
        region: region.trim() || null,
        limit: Number(limit),
        dry_run: dryRun,
        portals: selectedPortals,
        deadline_window: deadlineWindow.trim(),
        minimum_value: minimumValue.trim(),
        open_notices_only: openNoticesOnly,
      });
      setJobId(payload.job_id);
    } catch (requestError) {
      setError(requestError.message);
      setResult(null);
    }
  }

  const rows = result?.results || [];
  const total = result?.total || result?.discovered || Number(limit);
  const completed = result?.completed || 0;
  const progressPercent = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  const portalSummary =
    selectedPortals.length === DISCOVERY_PORTALS.length
      ? "All portals"
      : selectedPortals.length
        ? `${selectedPortals.length} portals selected`
        : "No portals selected";
  const orderedPortalOptions = [...DISCOVERY_PORTAL_OPTIONS]
    .map((portal) => ({
      ...portal,
      matchScore: portalMatchScore(portal, niche, region),
      matchLabel: portalMatchScore(portal, niche, region) >= 110 ? "High match" : portal.label,
    }))
    .sort((left, right) => right.matchScore - left.matchScore || left.name.localeCompare(right.name));

  return (
    <div className="discover-workflow">
      <form className="workflow-panel" onSubmit={handleSubmit}>
        <div className="field-grid">
          <label>
            <span>Contract type</span>
            <input
              disabled={running}
              onChange={(event) => setNiche(event.target.value)}
              placeholder="repairs, refurbishment, M&E"
              type="text"
              value={niche}
            />
          </label>

          <label>
            <span>Region</span>
            <input
              disabled={running}
              onChange={(event) => setRegion(event.target.value)}
              placeholder="Austin, TX"
              type="text"
              value={region}
            />
          </label>

          <label>
            <span>Limit</span>
            <div
              className={`select-control custom-select ${limitMenuOpen ? "open" : ""}`}
              ref={limitMenuRef}
            >
              <button
                aria-expanded={limitMenuOpen}
                className="limit-select-button"
                disabled={running}
                onClick={() => setLimitMenuOpen((open) => !open)}
                type="button"
              >
                {limit}
              </button>
              {limitMenuOpen ? (
                <div className="select-menu">
                  {[3, 5, 10, 20].map((option) => (
                    <button
                      className={Number(limit) === option ? "selected" : ""}
                      key={option}
                      onClick={() => {
                        setLimit(option);
                        setLimitMenuOpen(false);
                      }}
                      type="button"
                    >
                      {option}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          </label>
        </div>

        <div className="discovery-options">
          <label>
            <span>Portals</span>
            <div
              className={`portal-multiselect ${portalMenuOpen ? "open" : ""}`}
              ref={portalMenuRef}
            >
              <button
                aria-expanded={portalMenuOpen}
                disabled={running}
                onClick={() => setPortalMenuOpen((open) => !open)}
                type="button"
              >
                <span>{portalSummary}</span>
              </button>
              {portalMenuOpen ? (
                <div className="portal-menu">
                  <div className="portal-menu-actions">
                    <button onClick={() => setSelectedPortals(DISCOVERY_PORTALS)} type="button">
                      Select all
                    </button>
                    <button onClick={() => setSelectedPortals([])} type="button">
                      Clear
                    </button>
                  </div>
                  {orderedPortalOptions.map((portal) => (
                    <label key={portal.name}>
                      <input
                        checked={selectedPortals.includes(portal.name)}
                        disabled={running}
                        onChange={(event) => {
                          setSelectedPortals((current) =>
                            event.target.checked
                              ? [...current, portal.name]
                              : current.filter((item) => item !== portal.name)
                          );
                        }}
                        type="checkbox"
                      />
                      <span>{portal.name}</span>
                      <em>{portal.matchLabel}</em>
                    </label>
                  ))}
                </div>
              ) : null}
            </div>
          </label>
          <label>
            <span>Deadline window</span>
            <input
              disabled={running}
              onChange={(event) => setDeadlineWindow(event.target.value)}
              placeholder="next 60 days"
              type="text"
              value={deadlineWindow}
            />
          </label>
          <label>
            <span>Minimum value</span>
            <input
              disabled={running}
              onChange={(event) => setMinimumValue(event.target.value)}
              placeholder="£25k"
              type="text"
              value={minimumValue}
            />
          </label>
        </div>

        <div className="workflow-actions">
          <label className="toggle-row">
            <input
              checked={openNoticesOnly}
              disabled={running}
              onChange={(event) => setOpenNoticesOnly(event.target.checked)}
              type="checkbox"
            />
            <span>Open notices only</span>
          </label>
          <label className="toggle-row">
            <input
              checked={!dryRun}
              disabled={running}
              onChange={(event) => setDryRun(!event.target.checked)}
              type="checkbox"
            />
            <span>Write companies to Attio</span>
          </label>

          <button disabled={!niche.trim() || running} type="submit">
            {running ? "Running..." : dryRun ? "Run dry check" : "Discover and sync"}
          </button>
        </div>
      </form>

      {error ? <div className="state state-error">{error}</div> : null}

      {result ? (
        <section className="workflow-results">
          <div className="progress-panel">
            <div className="progress-topline">
              <div>
                <StatusBadge tone={result.state === "failed" ? "red" : "blue"}>
                  {result.phase}
                </StatusBadge>
                <strong>{result.message}</strong>
              </div>
              <span>{formatElapsed(result.elapsed_seconds)}</span>
            </div>
            <div className="progress-bar" aria-label="Discovery progress">
              <span style={{ width: `${progressPercent}%` }} />
            </div>
            <div className="progress-meta">
              <span>
                {completed}/{total} completed
              </span>
              <span>{progressPercent}%</span>
            </div>
          </div>

          <div className="metric-row">
            <div>
              <strong>{result.discovered || result.total || 0}</strong>
              <span>Discovered</span>
            </div>
            <div>
              <strong>{result.upserted}</strong>
            <span>{result.dry_run ? "Dry run" : "Upserted"}</span>
            </div>
            <div>
              <strong>{result.failed}</strong>
              <span>Failed</span>
            </div>
          </div>

          <DataState loading={false} error="" isEmpty={rows.length === 0}>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Opportunity</th>
                    <th>Buyer / Portal</th>
                    <th>Status</th>
                    <th>Value / Deadline</th>
                    <th>Sources</th>
                    <th>Message</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((company) => (
                    <tr className={`discovery-row row-${company.status}`} key={company.contract_url || `${company.domain}-${company.company_name}`}>
                      <td>
                        <strong>{company.contract_title || company.company_name}</strong>
                        <span>{company.procurement_stage || "Unknown stage"}</span>
                      </td>
                      <td>
                        <strong>{company.buyer_name || company.company_name}</strong>
                        <span>{company.portal_name || company.portal_domain || company.domain}</span>
                      </td>
                      <td>
                        <StatusBadge tone={statusTone(company.status)}>
                          {company.status}
                        </StatusBadge>
                        {company.availability_status ? (
                          <span>
                            <StatusBadge tone={availabilityTone(company.availability_status)}>
                              {company.availability_status}
                            </StatusBadge>
                          </span>
                        ) : null}
                      </td>
                      <td>
                        <strong>{company.contract_value || "Unknown"}</strong>
                        <span>{company.deadline || "Unknown deadline"}</span>
                        {company.availability_reason ? <span>{company.availability_reason}</span> : null}
                      </td>
                      <td>
                        <div className="source-list">
                          {(company.contract_url
                            ? [company.contract_url, ...company.source_urls.filter((url) => url !== company.contract_url)]
                            : company.source_urls
                          )
                            .slice(0, 3)
                            .map((url) => (
                              <a href={url} key={url} rel="noreferrer" target="_blank">
                                Source
                              </a>
                            ))}
                          {company.source_urls.length > 3 ? (
                            <span>+{company.source_urls.length - 3}</span>
                          ) : null}
                        </div>
                      </td>
                      <td>{company.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </DataState>
        </section>
      ) : null}
    </div>
  );
}

function formatElapsed(value = 0) {
  const seconds = Math.max(0, Math.round(value));
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (!minutes) {
    return `${remainder}s`;
  }
  return `${minutes}m ${String(remainder).padStart(2, "0")}s`;
}

function statusTone(status) {
  if (status === "failed") {
    return "red";
  }

  if (status === "dry_run") {
    return "blue";
  }

  if (status === "upserted") {
    return "green";
  }

  if (["searching", "extracting", "parsing", "syncing"].includes(status)) {
    return "blue";
  }

  return "yellow";
}

function App() {
  const [activeTab, setActiveTab] = useState("clients");
  const [data, setData] = useState({});
  const [loading, setLoading] = useState({});
  const [errors, setErrors] = useState({});

  const activeConfig = useMemo(
    () => tabs.find((tab) => tab.id === activeTab),
    [activeTab]
  );

  useEffect(() => {
    if (activeTab === "discover" || activeTab === "leads") {
      return;
    }

    if (data[activeTab] || loading[activeTab]) {
      return;
    }

    setLoading((current) => ({ ...current, [activeTab]: true }));
    setErrors((current) => ({ ...current, [activeTab]: "" }));

    fetchResource(activeTab)
      .then((payload) => {
        setData((current) => ({ ...current, [activeTab]: payload }));
      })
      .catch((error) => {
        setErrors((current) => ({ ...current, [activeTab]: error.message }));
      })
      .finally(() => {
        setLoading((current) => ({ ...current, [activeTab]: false }));
    });
  }, [activeTab, data, loading]);

  useEffect(() => {
    if (activeTab !== "calendar" || data.clients || loading.clients) {
      return;
    }

    setLoading((current) => ({ ...current, clients: true }));
    fetchResource("clients")
      .then((payload) => {
        setData((current) => ({ ...current, clients: payload }));
      })
      .catch(() => {
        // The calendar can still save unlinked events if contacts are unavailable.
      })
      .finally(() => {
        setLoading((current) => ({ ...current, clients: false }));
      });
  }, [activeTab, data.clients, loading.clients]);

  useEffect(() => {
    if (activeTab !== "leads") {
      return undefined;
    }

    let cancelled = false;
    let hasLoaded = false;

    async function loadLeads() {
      if (!hasLoaded) {
        setLoading((current) => ({ ...current, leads: true }));
      }
      setErrors((current) => ({ ...current, leads: "" }));

      try {
        const payload = await fetchResource("leads");
        if (!cancelled) {
          setData((current) => ({ ...current, leads: payload }));
          hasLoaded = true;
        }
      } catch (error) {
        if (!cancelled) {
          setErrors((current) => ({ ...current, leads: error.message }));
        }
      } finally {
        if (!cancelled) {
          setLoading((current) => ({ ...current, leads: false }));
        }
      }
    }

    loadLeads();
    const timer = window.setInterval(loadLeads, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeTab]);

  const rows = data[activeTab] || [];

  function handleLeadsChanged() {
    setData((current) => {
      const next = { ...current };
      delete next.leads;
      return next;
    });
  }

  function handleLeadUpdated(updatedLead) {
    setData((current) => ({
      ...current,
      leads: (current.leads || []).map((lead) =>
        lead.id === updatedLead.id ? updatedLead : lead
      ),
    }));
  }

  function handleClientCreated(createdClient) {
    setData((current) => ({
      ...current,
      clients: [...(current.clients || []), createdClient].sort((left, right) =>
        left.name.localeCompare(right.name)
      ),
    }));
  }

  function handleCalendarItemCreated(createdItem) {
    setData((current) => ({
      ...current,
      calendar: [...(current.calendar || []), createdItem].sort((left, right) =>
        `${left.date} ${left.start_time}`.localeCompare(`${right.date} ${right.start_time}`)
      ),
    }));
  }

  const views = {
    clients: <ClientsView rows={rows} onClientCreated={handleClientCreated} />,
    leads: <LeadsView rows={rows} onLeadUpdated={handleLeadUpdated} />,
    events: <EventsView rows={rows} />,
    emails: <EmailsView rows={rows} />,
    calendar: (
      <CalendarView
        clients={data.clients || []}
        rows={rows}
        onCalendarItemCreated={handleCalendarItemCreated}
      />
    ),
    discover: <DiscoverView onLeadsChanged={handleLeadsChanged} />,
  };

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span>CRM</span>
          <strong>Workspace</strong>
        </div>

        <nav aria-label="CRM sections">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                className={tab.id === activeTab ? "active" : ""}
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                type="button"
              >
                <Icon size={18} aria-hidden="true" />
                <span>{tab.label}</span>
              </button>
            );
          })}
        </nav>
      </aside>

      <section className="content">
        <header className="page-header">
          <div>
            <p>CRM scaffold</p>
            <h1>{activeConfig.label}</h1>
          </div>
          <StatusBadge tone={activeTab === "discover" ? "blue" : "green"}>
            {activeTab === "discover" ? "Live workflow" : activeTab === "leads" ? "Tender leads" : "CRM API"}
          </StatusBadge>
        </header>

        <DataState
          loading={loading[activeTab]}
          error={errors[activeTab]}
          isEmpty={activeTab !== "discover" && rows.length === 0}
        >
          {views[activeTab]}
        </DataState>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
