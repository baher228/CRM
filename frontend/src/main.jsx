import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  CalendarDays,
  Mail,
  Megaphone,
  Users,
  WalletCards,
  Search,
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
import {
  approveAction,
  fetchDiscoveryJob,
  fetchResource,
  generateBriefing,
  getLatestBriefing,
  startDiscoveryJob,
} from "./api";
import "./styles.css";

const tabs = [
  { id: "briefing", label: "Daybreak", icon: Sunrise },
  { id: "clients", label: "Clients", icon: Users },
  { id: "leads", label: "Leads", icon: Megaphone },
  { id: "events", label: "Events", icon: WalletCards },
  { id: "emails", label: "Emails", icon: Mail },
  { id: "calendar", label: "Calendar", icon: CalendarDays },
  { id: "discover", label: "Discover", icon: Search },
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

function DiscoverView({ onLeadsChanged }) {
  const [niche, setNiche] = useState("");
  const [region, setRegion] = useState("UK");
  const [limit, setLimit] = useState(5);
  const [dryRun, setDryRun] = useState(true);
  const [jobId, setJobId] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const running = Boolean(jobId);

  useEffect(() => {
    if (!jobId) return undefined;

    let cancelled = false;

    async function poll() {
      try {
        const payload = await fetchDiscoveryJob(jobId);
        if (cancelled) return;

        setResult(payload);
        if (payload.state !== "running") {
          if (payload.state === "completed") onLeadsChanged();
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
  }, [jobId, onLeadsChanged]);

  async function handleSubmit(event) {
    event.preventDefault();
    if (!niche.trim() || running) return;

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
              placeholder="UK"
              type="text"
              value={region}
            />
          </label>
          <label>
            <span>Limit</span>
            <select disabled={running} onChange={(event) => setLimit(event.target.value)} value={limit}>
              {[3, 5, 10, 20].map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="workflow-actions">
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
                    <tr
                      className={`discovery-row row-${company.status}`}
                      key={company.contract_url || `${company.domain}-${company.company_name}`}
                    >
                      <td>
                        <strong>{company.contract_title || company.company_name}</strong>
                        <span>{company.procurement_stage || "Unknown stage"}</span>
                      </td>
                      <td>
                        <strong>{company.buyer_name || company.company_name}</strong>
                        <span>{company.portal_name || company.portal_domain || company.domain}</span>
                      </td>
                      <td>
                        <StatusBadge tone={statusTone(company.status)}>{company.status}</StatusBadge>
                      </td>
                      <td>
                        <strong>{company.contract_value || "Unknown"}</strong>
                        <span>{company.deadline || "Unknown deadline"}</span>
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
                          {company.source_urls.length > 3 ? <span>+{company.source_urls.length - 3}</span> : null}
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
  if (!minutes) return `${remainder}s`;
  return `${minutes}m ${String(remainder).padStart(2, "0")}s`;
}

function statusTone(status) {
  if (status === "failed") return "red";
  if (status === "dry_run") return "blue";
  if (status === "upserted") return "green";
  if (["searching", "extracting", "parsing", "syncing"].includes(status)) return "blue";
  return "yellow";
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
    // Briefing and Discover tabs have their own data fetching.
    if (activeTab === "briefing" || activeTab === "discover" || activeTab === "leads") return;

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
    if (activeTab !== "leads") return undefined;

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

  const views = {
    briefing: <BriefingView />,
    clients: <ClientsView rows={rows} />,
    leads: <LeadsView rows={rows} />,
    events: <EventsView rows={rows} />,
    emails: <EmailsView rows={rows} />,
    calendar: <CalendarView rows={rows} />,
    discover: <DiscoverView onLeadsChanged={handleLeadsChanged} />,
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
          {activeTab === "briefing" || activeTab === "discover" ? (
            <StatusBadge tone="purple">Live</StatusBadge>
          ) : (
            <StatusBadge tone="green">Dummy API</StatusBadge>
          )}
        </header>

        {activeTab === "briefing" || activeTab === "discover" ? (
          views[activeTab]
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
