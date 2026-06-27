import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  CalendarDays,
  Mail,
  Megaphone,
  Users,
  WalletCards,
  Sunrise,
  Zap,
  Check,
  X,
  Loader2,
  ExternalLink,
  Shield,
  TrendingUp,
  AlertTriangle,
} from "lucide-react";
import { fetchResource, generateBriefing, approveAction, getLatestBriefing } from "./api";
import "./styles.css";

const tabs = [
  { id: "briefing", label: "Daybreak", icon: Sunrise },
  { id: "clients", label: "Clients", icon: Users },
  { id: "leads", label: "Leads", icon: Megaphone },
  { id: "events", label: "Events", icon: WalletCards },
  { id: "emails", label: "Emails", icon: Mail },
  { id: "calendar", label: "Calendar", icon: CalendarDays },
];

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

function ClientsView({ rows }) {
  return (
    <TableView
      columns={["Name", "Company", "Owner", "Value", "Last contact"]}
      rows={rows}
      renderRow={(client) => (
        <tr key={client.id}>
          <td>
            <strong>{client.name}</strong>
            <span>{client.email}</span>
          </td>
          <td>{client.company}</td>
          <td>{client.owner}</td>
          <td>{formatCurrency(client.value)}</td>
          <td>{formatDate(client.last_contact)}</td>
        </tr>
      )}
    />
  );
}

function LeadsView({ rows }) {
  const tones = {
    New: "blue",
    Contacted: "yellow",
    Qualified: "green",
    Proposal: "purple",
  };

  return (
    <TableView
      columns={["Lead", "Status", "Source", "Estimated value", "Created"]}
      rows={rows}
      renderRow={(lead) => (
        <tr key={lead.id}>
          <td>
            <strong>{lead.name}</strong>
            <span>{lead.company}</span>
          </td>
          <td>
            <StatusBadge tone={tones[lead.status]}>{lead.status}</StatusBadge>
          </td>
          <td>{lead.source}</td>
          <td>{formatCurrency(lead.estimated_value)}</td>
          <td>{formatDate(lead.created_at)}</td>
        </tr>
      )}
    />
  );
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

function CalendarView({ rows }) {
  const groupedRows = rows.reduce((groups, item) => {
    groups[item.date] = groups[item.date] || [];
    groups[item.date].push(item);
    return groups;
  }, {});

  return (
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
                <span>{item.related_to}</span>
                <p>{item.notes}</p>
              </div>
            </article>
          ))}
        </section>
      ))}
    </div>
  );
}

/* ── Urgency bar color helper ── */
function urgencyColor(score) {
  if (score >= 75) return "var(--urgency-high)";
  if (score >= 45) return "var(--urgency-med)";
  return "var(--urgency-low)";
}

/* ── Briefing view (Daybreak) ── */
function BriefingView() {
  const [briefing, setBriefing] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [approving, setApproving] = useState({});
  const [approved, setApproved] = useState({});

  // Try to load the latest briefing on mount
  useEffect(() => {
    getLatestBriefing().then((data) => {
      if (data && data.items) setBriefing(data);
    });
  }, []);

  const handleGenerate = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await generateBriefing({ limit: 10 });
      setBriefing(result);
      setApproved({});
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleApprove = async (index) => {
    setApproving((prev) => ({ ...prev, [index]: true }));
    try {
      await approveAction(index);
      setApproved((prev) => ({ ...prev, [index]: true }));
    } catch (err) {
      setError(err.message);
    } finally {
      setApproving((prev) => ({ ...prev, [index]: false }));
    }
  };

  return (
    <div className="briefing-container">
      {/* Hero banner */}
      <div className="briefing-hero">
        <div className="briefing-hero-content">
          <div className="briefing-hero-icon">
            <Sunrise size={32} />
          </div>
          <div>
            <h2>Good morning</h2>
            <p>Your personalised Daybreak briefing — internal CRM signals and external news, ranked by what matters most.</p>
          </div>
        </div>
        <button
          className="briefing-generate-btn"
          onClick={handleGenerate}
          disabled={loading}
          id="generate-briefing-btn"
        >
          {loading ? (
            <><Loader2 size={18} className="spin" /> Generating...</>
          ) : (
            <><Zap size={18} /> Generate Briefing</>
          )}
        </button>
      </div>

      {error && <div className="briefing-error"><AlertTriangle size={16} /> {error}</div>}

      {/* Integration badges */}
      <div className="briefing-integrations">
        <span className="integration-chip attio"><Shield size={12} /> Attio</span>
        <span className="integration-chip tavily"><TrendingUp size={12} /> Tavily</span>
        <span className="integration-chip gemini"><Zap size={12} /> Gemini</span>
        <span className="integration-chip n8n">⚡ n8n</span>
        <span className="integration-chip superlinked disabled">◇ Superlinked</span>
        <span className="integration-chip slng disabled">🔊 SLNG</span>
      </div>

      {/* Loading state */}
      {loading && (
        <div className="briefing-loading">
          <div className="briefing-loading-spinner"></div>
          <p>Scanning Attio pipeline + Tavily news...</p>
        </div>
      )}

      {/* No briefing yet */}
      {!loading && !briefing && (
        <div className="briefing-empty">
          <Sunrise size={48} />
          <h3>No briefing yet</h3>
          <p>Click "Generate Briefing" to scan your CRM and external news for today's top signals.</p>
        </div>
      )}

      {/* Briefing results */}
      {!loading && briefing && (
        <div className="briefing-results">
          <div className="briefing-meta">
            <span>Generated {formatDateTime(briefing.generated_at)}</span>
            <span>{briefing.total_signals_gathered} signals scanned</span>
            <span>{briefing.items.length} items ranked</span>
            {briefing.n8n_triggered && <StatusBadge tone="blue">n8n triggered</StatusBadge>}
          </div>

          <div className="briefing-items">
            {briefing.items.map((item, index) => (
              <article
                className={`briefing-card ${approved[index] ? "approved" : ""}`}
                key={index}
              >
                <div className="briefing-card-header">
                  <div className="briefing-rank">#{item.rank}</div>
                  <div className="briefing-card-badges">
                    <StatusBadge tone={item.signal.type === "internal" ? "purple" : "blue"}>
                      {item.signal.type === "internal" ? "CRM" : "News"}
                    </StatusBadge>
                    <StatusBadge tone={item.signal.source === "attio" ? "green" : "yellow"}>
                      {item.signal.source}
                    </StatusBadge>
                  </div>
                </div>

                <div className="briefing-card-company">{item.signal.company_name}</div>
                <h3 className="briefing-card-headline">{item.signal.headline}</h3>
                <p className="briefing-card-detail">{item.signal.detail}</p>

                {item.signal.source_url && (
                  <a
                    className="briefing-card-source"
                    href={item.signal.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <ExternalLink size={14} /> Source
                  </a>
                )}

                {/* Urgency bar */}
                <div className="briefing-urgency">
                  <span className="briefing-urgency-label">Urgency</span>
                  <div className="briefing-urgency-track">
                    <div
                      className="briefing-urgency-fill"
                      style={{
                        width: `${item.urgency}%`,
                        background: urgencyColor(item.urgency),
                      }}
                    />
                  </div>
                  <span className="briefing-urgency-value">{item.urgency}</span>
                </div>

                {/* Drafted action */}
                <div className="briefing-action-box">
                  <div className="briefing-action-label"><Zap size={14} /> Suggested action</div>
                  <p>{item.drafted_action}</p>
                  <div className="briefing-action-reasoning">{item.reasoning}</div>
                </div>

                {/* Approve / dismiss */}
                <div className="briefing-card-actions">
                  {approved[index] ? (
                    <span className="briefing-approved-label"><Check size={16} /> Approved</span>
                  ) : (
                    <>
                      <button
                        className="briefing-approve-btn"
                        onClick={() => handleApprove(index)}
                        disabled={approving[index]}
                      >
                        {approving[index] ? <Loader2 size={14} className="spin" /> : <Check size={14} />}
                        {approving[index] ? "Saving..." : "Approve & Act"}
                      </button>
                      <button className="briefing-dismiss-btn">
                        <X size={14} /> Dismiss
                      </button>
                    </>
                  )}
                </div>
              </article>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function App() {
  const [activeTab, setActiveTab] = useState("briefing");
  const [data, setData] = useState({});
  const [loading, setLoading] = useState({});
  const [errors, setErrors] = useState({});

  const activeConfig = useMemo(
    () => tabs.find((tab) => tab.id === activeTab),
    [activeTab]
  );

  useEffect(() => {
    // Briefing tab has its own data fetching
    if (activeTab === "briefing") return;

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

  const rows = data[activeTab] || [];

  const views = {
    briefing: <BriefingView />,
    clients: <ClientsView rows={rows} />,
    leads: <LeadsView rows={rows} />,
    events: <EventsView rows={rows} />,
    emails: <EmailsView rows={rows} />,
    calendar: <CalendarView rows={rows} />,
  };

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span>Daybreak</span>
          <strong>CRM Intelligence</strong>
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
            <p>{activeTab === "briefing" ? "Morning intelligence" : "CRM scaffold"}</p>
            <h1>{activeConfig.label}</h1>
          </div>
          {activeTab === "briefing" ? (
            <StatusBadge tone="purple">Live</StatusBadge>
          ) : (
            <StatusBadge tone="green">Dummy API</StatusBadge>
          )}
        </header>

        {activeTab === "briefing" ? (
          views.briefing
        ) : (
          <DataState
            loading={loading[activeTab]}
            error={errors[activeTab]}
            isEmpty={rows.length === 0}
          >
            {views[activeTab]}
          </DataState>
        )}
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
