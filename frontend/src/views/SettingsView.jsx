import React, { useEffect, useState } from "react";
import { Database, PlugZap } from "lucide-react";

import { fetchSettingsHealth } from "../api";
import { StatusBadge } from "../components/common";

export function SettingsView() {
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    fetchSettingsHealth()
      .then((payload) => {
        if (!cancelled) {
          setHealth(payload);
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
    return <div aria-busy="true" className="state" role="status">Checking workspace health...</div>;
  }

  if (error) {
    return <div className="state state-error" role="alert">{error}</div>;
  }

  return (
    <div className="settings-workspace">
      <section className="workflow-panel">
        <div className="section-heading">
          <div>
            <span>Local data</span>
            <h2>SQLite store</h2>
          </div>
          <Database size={20} aria-hidden="true" />
        </div>
        <p className="settings-path">{health.database_path}</p>
      </section>

      <section className="workflow-panel">
        <div className="section-heading">
          <div>
            <span>Integrations</span>
            <h2>Connection health</h2>
          </div>
          <PlugZap size={20} aria-hidden="true" />
        </div>
        <div className="integration-list">
          {health.integrations.map((integration) => (
            <article className="integration-row" key={integration.name}>
              <div>
                <strong>{integration.name}</strong>
                <span>{integration.detail}</span>
              </div>
              <StatusBadge tone={integration.configured ? "green" : "yellow"}>
                {integration.configured ? "Configured" : "Missing env"}
              </StatusBadge>
            </article>
          ))}
        </div>
      </section>

      <section className="workflow-panel">
        <div className="section-heading">
          <div>
            <span>Deferred module</span>
            <h2>Daybreak</h2>
          </div>
          <StatusBadge tone={health.daybreak_enabled ? "purple" : "yellow"}>
            {health.daybreak_enabled ? "Enabled" : "Hidden"}
          </StatusBadge>
        </div>
      </section>
    </div>
  );
}
