import React, { useEffect, useState } from "react";
import { AlertTriangle, Check, ExternalLink, Loader2, Shield, Sunrise, TrendingUp, X, Zap } from "lucide-react";

import { approveAction, generateBriefing, getLatestBriefing } from "../api";
import { urgencyColor } from "../utils/format";

export function BriefingView() {
  const [briefing, setBriefing] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [approving, setApproving] = useState({});
  const [approved, setApproved] = useState({});

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
      <div className="briefing-hero">
        <div className="briefing-hero-content">
          <div className="briefing-hero-icon"><Sunrise size={32} /></div>
          <div>
            <h2>Good morning</h2>
            <p>Your personalised Daybreak briefing - internal CRM signals and external news, ranked by what matters most.</p>
          </div>
        </div>
        <button className="briefing-generate-btn" onClick={handleGenerate} disabled={loading}>
          {loading ? <><Loader2 size={18} className="spin" /> Generating...</> : <><Zap size={18} /> Generate Briefing</>}
        </button>
      </div>

      {error && <div className="briefing-error"><AlertTriangle size={16} /> {error}</div>}

      <div className="briefing-integrations">
        <span className="integration-chip attio"><Shield size={12} /> Attio</span>
        <span className="integration-chip tavily"><TrendingUp size={12} /> Tavily</span>
        <span className="integration-chip gemini"><Zap size={12} /> Gemini</span>
        <span className="integration-chip n8n"><Zap size={12} /> n8n</span>
        <span className="integration-chip superlinked disabled">Superlinked</span>
        <span className="integration-chip slng disabled">SLNG</span>
      </div>

      {loading && (
        <div className="briefing-loading">
          <div className="briefing-loading-spinner"></div>
          <p>Scanning Attio pipeline + Tavily news...</p>
        </div>
      )}

      {!loading && !briefing && (
        <div className="briefing-empty">
          <Sunrise size={48} />
          <h3>No briefing yet</h3>
          <p>Click "Generate Briefing" to scan your CRM and external news for today's top signals.</p>
        </div>
      )}

      {!loading && briefing && (
        <div className="briefing-results">
          <div className="briefing-meta">
            <span>Generated {new Intl.DateTimeFormat("en-GB", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(briefing.generated_at))}</span>
            <span>{briefing.total_signals_gathered} signals scanned</span>
            <span>{briefing.items.length} items ranked</span>
            {briefing.n8n_triggered && <span className="badge badge-blue">n8n triggered</span>}
          </div>

          <div className="briefing-items">
            {briefing.items.map((item, index) => (
              <article className={`briefing-card ${approved[index] ? "approved" : ""}`} key={index}>
                <div className="briefing-card-header">
                  <div className="briefing-rank">#{item.rank}</div>
                  <div className="briefing-card-badges">
                    <span className={`badge badge-${item.signal.type === "internal" ? "purple" : "blue"}`}>
                      {item.signal.type === "internal" ? "CRM" : "News"}
                    </span>
                    <span className={`badge badge-${item.signal.source === "attio" ? "green" : "yellow"}`}>
                      {item.signal.source}
                    </span>
                  </div>
                </div>

                <div className="briefing-card-company">{item.signal.company_name}</div>
                <h3 className="briefing-card-headline">{item.signal.headline}</h3>
                <p className="briefing-card-detail">{item.signal.detail}</p>

                {item.signal.source_url && (
                  <a className="briefing-card-source" href={item.signal.source_url} target="_blank" rel="noopener noreferrer">
                    <ExternalLink size={14} /> Source
                  </a>
                )}

                <div className="briefing-urgency">
                  <span className="briefing-urgency-label">Urgency</span>
                  <div className="briefing-urgency-track">
                    <div className="briefing-urgency-fill" style={{ width: `${item.urgency}%`, background: urgencyColor(item.urgency) }} />
                  </div>
                  <span className="briefing-urgency-value">{item.urgency}</span>
                </div>

                <div className="briefing-action-box">
                  <div className="briefing-action-label"><Zap size={14} /> Suggested action</div>
                  <p>{item.drafted_action}</p>
                  <div className="briefing-action-reasoning">{item.reasoning}</div>
                </div>

                <div className="briefing-card-actions">
                  {approved[index] ? (
                    <span className="briefing-approved-label"><Check size={16} /> Approved</span>
                  ) : (
                    <>
                      <button className="briefing-approve-btn" onClick={() => handleApprove(index)} disabled={approving[index]}>
                        {approving[index] ? <Loader2 size={14} className="spin" /> : <Check size={14} />}
                        {approving[index] ? "Saving..." : "Approve & Act"}
                      </button>
                      <button className="briefing-dismiss-btn" onClick={() => setApproved((prev) => ({ ...prev, [index]: true }))}>
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
