import React, { useEffect, useMemo, useState } from "react";
import { ArrowRight, Command, CornerDownLeft, Plus, Search } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { api, routeForResult, unwrapList } from "../api";
import { quickPages } from "../workspace";
import { AppDialog } from "./common";

export function CommandPalette({ open, onClose, onCreate }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [remote, setRemote] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setRemote([]);
    }
  }, [open]);

  useEffect(() => {
    if (!open || query.trim().length < 2) {
      setRemote([]);
      return undefined;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true);
      try {
        const payload = await api.get("search", { query: { q: query.trim() }, signal: controller.signal });
        setRemote(unwrapList(payload));
      } catch (error) {
        if (error.name !== "AbortError") setRemote([]);
      } finally {
        setLoading(false);
      }
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [open, query]);

  const pages = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return quickPages.filter((item) => !needle || item.title.toLowerCase().includes(needle)).slice(0, 7);
  }, [query]);

  function go(path) {
    navigate(path);
    onClose();
  }

  return (
    <AppDialog className="command-dialog" description="Search records or jump to any workspace." onClose={onClose} open={open} title="Command workspace">
      <label className="command-input">
        <Search aria-hidden="true" size={19} />
        <span className="sr-only">Search CRM records and pages</span>
        <input autoComplete="off" onChange={(event) => setQuery(event.target.value)} placeholder="Search people, deals, invoices…" type="search" value={query} />
        <kbd>Esc</kbd>
      </label>
      <div aria-busy={loading} aria-live="polite" className="command-results">
        <span className="command-section-label">{query ? "Matches" : "Jump to"}</span>
        {remote.map((result) => (
          <button key={`${result.type || result.resource_type}-${result.id}`} onClick={() => go(routeForResult(result))} type="button">
            <span className="command-result-icon"><Search aria-hidden="true" size={16} /></span>
            <span><strong>{result.title || result.name}</strong><small>{result.subtitle || result.detail || result.type}</small></span>
            <CornerDownLeft aria-hidden="true" size={14} />
          </button>
        ))}
        {pages.map((page) => {
          const Icon = page.icon;
          return <button key={page.id} onClick={() => go(page.path)} type="button"><span className="command-result-icon"><Icon aria-hidden="true" size={16} /></span><span><strong>{page.title}</strong><small>{page.subtitle}</small></span><ArrowRight aria-hidden="true" size={14} /></button>;
        })}
        {!loading && query && !remote.length && !pages.length ? <p className="command-empty">No matching records or pages.</p> : null}
      </div>
      <footer className="command-footer"><span><Command aria-hidden="true" size={14} /> Navigate with Tab</span><button onClick={() => { onClose(); onCreate(); }} type="button"><Plus aria-hidden="true" size={14} /> New record <kbd>Ctrl N</kbd></button></footer>
    </AppDialog>
  );
}
