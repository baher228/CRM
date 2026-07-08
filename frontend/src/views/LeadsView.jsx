import React, { useState } from "react";
import { Send } from "lucide-react";

import { confirmLead, rejectLead, updateLead } from "../api";
import { StatusBadge, TableView } from "../components/common";
import {
  availabilityTone,
  draftEmailHref,
  firstKnown,
  formatCurrency,
  formatDate,
  formatDomain,
  formatDraftEmailBody,
  isBadSourceUrl,
  isKnown,
  parseDateish,
  priorityTone,
  showValue,
} from "../utils/format";

export function LeadsView({ rows, onLeadUpdated }) {
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
    const draftBody = formatDraftEmailBody(selectedLead.draft_email_body);
    const draftHref = draftEmailHref(selectedLead, draftBody);

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
          {(selectedLead.status === "Confirmed" || selectedLead.confirmed_at) && selectedLead.contact_email ? (
            <button className="secondary-action" disabled={busyLeadId === selectedLead.id} onClick={() => runAction(selectedLead, "confirm")} type="button">
              Sync contact
            </button>
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
          <section className="draft-email-section">
            <div className="draft-email-header">
              <h3>Draft email</h3>
              {selectedLead.draft_email_subject || draftBody ? (
                draftHref ? (
                  <a className="confirm-action action-with-icon" href={draftHref}>
                    <Send size={15} aria-hidden="true" />
                    Send draft
                  </a>
                ) : (
                  <button className="confirm-action action-with-icon" disabled type="button">
                    <Send size={15} aria-hidden="true" />
                    Send draft
                  </button>
                )
              ) : null}
            </div>
            {selectedLead.draft_email_subject || selectedLead.draft_email_body ? (
              <div className="draft-email">
                <strong>{selectedLead.draft_email_subject || "No subject generated"}</strong>
                <p>{draftBody || "No body generated"}</p>
              </div>
            ) : (
              <p>Confirm this lead with a contact to generate a draft email.</p>
            )}
          </section>
          <section>
            <h3>Attio sync</h3>
            <p>{selectedLead.last_sync_message || "Not synced yet"}</p>
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
