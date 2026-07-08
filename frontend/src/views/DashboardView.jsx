import React, { useEffect, useState } from "react";
import { Activity, CalendarClock, Flame, ListTodo, Megaphone } from "lucide-react";

import { fetchDashboard } from "../api";
import { StatusBadge } from "../components/common";
import { formatDateTime } from "../utils/format";

const metrics = [
  { key: "open_leads", label: "Open leads", icon: Megaphone, tone: "blue" },
  { key: "hot_leads", label: "Hot opportunities", icon: Flame, tone: "red" },
  { key: "overdue_tasks", label: "Overdue follow-ups", icon: ListTodo, tone: "yellow" },
  { key: "upcoming_calendar", label: "Upcoming calendar", icon: CalendarClock, tone: "green" },
];

export function DashboardView() {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    fetchDashboard()
      .then((payload) => {
        if (!cancelled) {
          setSummary(payload);
          setError("");
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setError(requestError.message);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return <div aria-busy="true" className="state" role="status">Loading dashboard...</div>;
  }

  if (error) {
    return <div className="state state-error" role="alert">{error}</div>;
  }

  return (
    <div className="dashboard-workspace">
      <div className="dashboard-metrics">
        {metrics.map(({ key, label, icon: Icon, tone }) => (
          <article className="metric-card" key={key}>
            <span className={`metric-icon metric-${tone}`}>
              <Icon size={18} aria-hidden="true" />
            </span>
            <div>
              <strong>{summary?.[key] ?? 0}</strong>
              <span>{label}</span>
            </div>
          </article>
        ))}
      </div>

      <section className="workflow-panel">
        <div className="section-heading">
          <div>
            <span>Workspace activity</span>
            <h2>Recent updates</h2>
          </div>
          <StatusBadge tone="blue">{summary?.recent_activity?.length || 0} items</StatusBadge>
        </div>
        {summary?.recent_activity?.length ? (
          <div className="activity-list">
            {summary.recent_activity.map((item) => (
              <article className="activity-item" key={item.id}>
                <Activity size={16} aria-hidden="true" />
                <div>
                  <strong>{item.title}</strong>
                  <p>{item.detail || `${item.related_type} #${item.related_id}`}</p>
                  <span>{formatDateTime(item.occurred_at)}</span>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <p className="empty-copy">No activity yet.</p>
        )}
      </section>
    </div>
  );
}
