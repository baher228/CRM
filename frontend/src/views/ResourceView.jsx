import React, { useDeferredValue, useEffect, useMemo, useState } from "react";
import { Archive, Filter, Plus, Search } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { api } from "../api";
import { EmptyState, LoadingState, PageControls, SignalLine, UnavailableState } from "../components/common";
import { archiveResources, EditableRecordCard, SavedViewControls, SelectableRecordGrid } from "../components/DataControls";
import { useDocumentTitle, useResource } from "../hooks";
import { createTypes, resourceConfigs, statusOptions } from "../workspace";
import { recordName } from "../utils/format";

export function PageIntro({ eyebrow, title, description, actions, signal }) {
  return (
    <div className="page-intro">
      <div>
        <span className="eyebrow">{eyebrow || "Workspace"}</span>
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      <div className="page-intro-actions">
        {signal ? <SignalLine label={signal} /> : null}
        {actions}
      </div>
    </div>
  );
}

function requestQuickCreate(type) {
  window.dispatchEvent(new CustomEvent("crm:quick-create", { detail: { type } }));
}

export function ResourceView({ resourceKey, endpoint, embedded = false, title, description, actions }) {
  const config = resourceConfigs[resourceKey];
  const path = endpoint || config.endpoint;
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [columns, setColumns] = useState(() => config.fields.map(([label]) => label));
  const [includeArchived, setIncludeArchived] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const deferredQuery = useDeferredValue(query);
  const supportsArchivedView = ["accounts", "contacts"].includes(resourceKey);
  const activeSavedView = searchParams.get("saved_view") || "";
  const listQuery = {
    q: deferredQuery.trim() || undefined,
    status: status === "all" ? undefined : status,
    include_archived: supportsArchivedView && includeArchived ? true : undefined,
    limit: 25,
  };
  const {
    data,
    loading,
    error,
    reload,
    mutate,
    page,
    hasNext,
    hasPrevious,
    nextPage,
    previousPage,
  } = useResource(path, { query: listQuery, pageSize: 25 });
  const savedViewsState = useResource("saved-views", { query: { entity_type: resourceKey } });
  const supportsQuickCreate = createTypes.some((type) => type.value === resourceKey);
  useDocumentTitle(embedded ? "" : title || config.title);

  useEffect(() => {
    if (!activeSavedView) return;
    const saved = savedViewsState.data.find((view) => String(view.id) === activeSavedView);
    if (!saved) return;
    const view = saved.config || {};
    setQuery(String(view.query || ""));
    setStatus(String(view.status || "all"));
    setColumns(Array.isArray(view.columns) ? view.columns.filter((label) => config.fields.some(([field]) => field === label)) : config.fields.map(([label]) => label));
    setIncludeArchived(supportsArchivedView && Boolean(view.include_archived));
  }, [activeSavedView, config.fields, savedViewsState.data, supportsArchivedView]);

  useEffect(() => {
    setSelected((current) => new Set([...current].filter((id) => data.some((record) => record.id === id && !record.archived_at))));
  }, [data]);

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return data.filter((record) => {
      const recordStatus = String(record.status || record.triage_status || record.state || record.lifecycle_status || record.computed_health || record.health || "").toLowerCase();
      const matchesStatus = status === "all" || recordStatus === status.toLowerCase();
      const matchesQuery = !needle || [recordName(record), record.account_name, record.company_name, record.email, record.number]
        .some((value) => String(value || "").toLowerCase().includes(needle));
      return matchesStatus && matchesQuery;
    });
  }, [data, query, status]);

  function setSavedView(id) {
    const next = new URLSearchParams(searchParams);
    if (id) next.set("saved_view", id);
    else {
      next.delete("saved_view");
      setQuery("");
      setStatus("all");
      setColumns(config.fields.map(([label]) => label));
      setIncludeArchived(false);
    }
    setSearchParams(next);
  }

  async function saveView(name) {
    const saved = await api.post("saved-views", {
      entity_type: resourceKey,
      name,
      config: { query, status, columns, include_archived: includeArchived },
    });
    await savedViewsState.reload();
    setSavedView(String(saved.id));
  }

  function toggleColumn(label) {
    setColumns((current) => current.includes(label) ? current.filter((item) => item !== label) : [...current, label]);
  }

  function selectRecord(id, checked) {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function selectAll(checked) {
    setSelected(checked ? new Set(rows.filter((record) => !record.archived_at).map((record) => record.id)) : new Set());
  }

  async function archiveSelected() {
    const records = rows.filter((record) => selected.has(record.id));
    if (!records.length || !window.confirm(`Archive ${records.length} selected ${records.length === 1 ? config.singular : config.title.toLowerCase()}? Linked history is preserved.`)) return;
    setBulkBusy(true);
    setFeedback(null);
    let completed = 0;
    try {
      for (const record of records) {
        await api.post(`${archiveResources[resourceKey]}/${record.id}/archive`, { version: record.version }, { headers: { "X-CRM-Confirmed": "true" } });
        completed += 1;
      }
      setSelected(new Set());
      setFeedback({ tone: "positive", message: `${completed} record${completed === 1 ? "" : "s"} archived.` });
      await reload();
    } catch (caught) {
      setFeedback({ tone: "error", message: `${completed} archived before the operation stopped. ${caught.message}` });
      await reload();
    } finally {
      setBulkBusy(false);
    }
  }

  async function restoreRecord(record) {
    if (!window.confirm(`Restore ${recordName(record)} to active ${config.title.toLowerCase()}?`)) return;
    setBulkBusy(true);
    setFeedback(null);
    try {
      await api.post(`${resourceKey}/${record.id}/restore`, { version: record.version }, { headers: { "X-CRM-Confirmed": "true" } });
      setFeedback({ tone: "positive", message: `${recordName(record)} restored.` });
      await reload();
    } catch (caught) {
      setFeedback({ tone: "error", message: caught.message });
    } finally {
      setBulkBusy(false);
    }
  }

  function saveInlineRecord(saved) {
    mutate((current) => current.map((record) => record.id === saved.id ? { ...record, ...saved } : record));
    setFeedback({ tone: "positive", message: `${recordName(saved, config.singular)} updated.` });
  }

  return (
    <div className={`resource-view ${embedded ? "resource-embedded" : ""}`}>
      {!embedded ? (
        <PageIntro
          actions={actions !== undefined ? actions : supportsQuickCreate ? (
            <button className="button button-primary" onClick={() => requestQuickCreate(resourceKey)} type="button">
              <Plus aria-hidden="true" size={16} /> New {config.singular}
            </button>
          ) : null}
          description={description || config.description}
          eyebrow="Workspace"
        signal={`Page ${page} Â· ${data.length} local ${data.length === 1 ? "record" : "records"}`}
          title={title || config.title}
        />
      ) : null}

      <section aria-label={`${config.title} controls`} className="list-toolbar">
        <label className="search-field">
          <Search aria-hidden="true" size={16} />
          <span className="sr-only">Search {config.title}</span>
          <input onChange={(event) => setQuery(event.target.value)} placeholder={`Search ${config.title.toLowerCase()}`} type="search" value={query} />
        </label>
        {statusOptions[resourceKey] ? (
          <label className="select-field">
            <Filter aria-hidden="true" size={15} />
            <span className="sr-only">Filter by status</span>
            <select onChange={(event) => setStatus(event.target.value)} value={status}>
              <option value="all">All statuses</option>
              {statusOptions[resourceKey].map((option) => <option key={option}>{option}</option>)}
            </select>
          </label>
        ) : null}
        <SavedViewControls activeId={activeSavedView} columns={columns} config={config} onApply={setSavedView} onSave={saveView} onToggleColumn={toggleColumn} savedViews={savedViewsState.data} />
        {supportsArchivedView ? <label className="checkbox-field archive-filter"><input checked={includeArchived} onChange={(event) => setIncludeArchived(event.target.checked)} type="checkbox" /><span>Include archived</span></label> : null}
        <span aria-live="polite" className="result-count">{rows.length} shown</span>
      </section>

      {archiveResources[resourceKey] && rows.length ? <section aria-label={`${config.title} selection`} className="bulk-toolbar"><label className="checkbox-field"><input checked={rows.some((record) => !record.archived_at) && rows.filter((record) => !record.archived_at).every((record) => selected.has(record.id))} onChange={(event) => selectAll(event.target.checked)} type="checkbox" /><span>Select all shown</span></label><span aria-live="polite">{selected.size ? `${selected.size} selected` : "Select records for bulk actions"}</span><button className="button button-quiet" disabled={!selected.size || bulkBusy} onClick={archiveSelected} type="button"><Archive aria-hidden="true" size={14} /> {bulkBusy ? "Archiving…" : "Archive selected"}</button></section> : null}
      {feedback ? <p aria-live="polite" className={`action-feedback ${feedback.tone}`}>{feedback.message}</p> : null}

      {loading ? <LoadingState label={`Loading ${config.title.toLowerCase()}`} /> : null}
      {!loading && error ? <UnavailableState error={error} onRetry={reload} /> : null}
      {!loading && !error && !rows.length ? (
        <EmptyState
          icon={config.icon}
          title={query || status !== "all" ? "No records match this view" : `No ${config.title.toLowerCase()} yet`}
          message={query || status !== "all" ? "Clear the search or status filter to see more records." : `Create the first ${config.singular}, or import existing data in Settings.`}
          action={!query && status === "all" && supportsQuickCreate ? (
            <button className="button button-primary" onClick={() => requestQuickCreate(resourceKey)} type="button">
              <Plus aria-hidden="true" size={16} /> New {config.singular}
            </button>
          ) : null}
        />
      ) : null}
      {!loading && !error && rows.length ? (
        archiveResources[resourceKey]
          ? <SelectableRecordGrid columns={columns} config={config} onReload={reload} onRestore={supportsArchivedView ? restoreRecord : null} onSaved={saveInlineRecord} onSelect={selectRecord} records={rows} selected={selected} />
          : <div className="record-grid">{rows.map((record) => <EditableRecordCard config={{ ...config, fields: config.fields.filter(([label]) => columns.includes(label)) }} key={record.id} onReload={reload} onSaved={saveInlineRecord} record={record} />)}</div>
      ) : null}
      {!loading && !error ? <PageControls hasNext={hasNext} hasPrevious={hasPrevious} label={config.title} nextPage={nextPage} page={page} previousPage={previousPage} /> : null}
    </div>
  );
}
