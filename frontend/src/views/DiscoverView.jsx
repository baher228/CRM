import React, { useEffect, useRef, useState } from "react";

import { fetchDiscoveryPortals, fetchDiscoveryJob, startDiscoveryJob } from "../api";
import { DataState, StatusBadge } from "../components/common";
import { availabilityTone, formatElapsed, statusTone } from "../utils/format";

const PORTAL_SELECTION_STORAGE_KEY = "crm.discovery.selectedPortals";

const storedDiscoveryPortals = () => {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const parsed = JSON.parse(window.localStorage.getItem(PORTAL_SELECTION_STORAGE_KEY) || "null");
    return Array.isArray(parsed) ? parsed.filter((portal) => typeof portal === "string") : null;
  } catch {
    return null;
  }
};

export function DiscoverView({ onLeadsChanged }) {
  const [niche, setNiche] = useState("");
  const [region, setRegion] = useState("");
  const [limit, setLimit] = useState(5);
  const [limitMenuOpen, setLimitMenuOpen] = useState(false);
  const [selectedPortals, setSelectedPortals] = useState(() => storedDiscoveryPortals() || []);
  const [portalOptions, setPortalOptions] = useState([]);
  const initializedPortalsRef = useRef(false);
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
    let cancelled = false;

    fetchDiscoveryPortals(niche, region)
      .then((payload) => {
        if (cancelled) {
          return;
        }
        const validNames = new Set(payload.map((portal) => portal.name));
        setPortalOptions(payload);
        setSelectedPortals((current) => {
          const validSelection = current.filter((portal) => validNames.has(portal));
          if (initializedPortalsRef.current) {
            return validSelection;
          }
          initializedPortalsRef.current = true;
          const stored = storedDiscoveryPortals();
          if (stored) {
            return stored.filter((portal) => validNames.has(portal));
          }
          return payload.filter((portal) => portal.default_selected).map((portal) => portal.name);
        });
      })
      .catch((requestError) => {
        if (!cancelled) {
          setError(requestError.message);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [niche, region]);

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
  const portalNames = portalOptions.map((portal) => portal.name);
  const portalSummary =
    portalNames.length && selectedPortals.length === portalNames.length
      ? "All portals"
      : selectedPortals.length
        ? `${selectedPortals.length} portals selected`
        : portalOptions.length
          ? "No portals selected"
          : "Loading portals";
  const orderedPortalOptions = [...portalOptions]
    .sort((left, right) => (right.priority || 0) - (left.priority || 0) || left.name.localeCompare(right.name));

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
                    <button onClick={() => setSelectedPortals(portalNames)} type="button">
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
                      <em>{portal.label}</em>
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
              placeholder="GBP 25k"
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

      {error ? <div className="state state-error" role="alert">{error}</div> : null}

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
            <div aria-label="Discovery results" className="table-wrap" role="region" tabIndex="0">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Opportunity</th>
                    <th scope="col">Buyer / Portal</th>
                    <th scope="col">Status</th>
                    <th scope="col">Value / Deadline</th>
                    <th scope="col">Sources</th>
                    <th scope="col">Message</th>
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
