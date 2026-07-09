import React, { useEffect, useMemo, useState } from "react";
import {
  CalendarDays,
  LayoutDashboard,
  ListTodo,
  Mail,
  Megaphone,
  Search as SearchIcon,
  Settings,
  Sunrise,
  Users,
} from "lucide-react";

import { fetchResource, fetchSearch } from "./api";
import { DataState, StatusBadge } from "./components/common";
import { BriefingView } from "./views/BriefingView";
import { CalendarView } from "./views/CalendarView";
import { ClientsView } from "./views/ClientsView";
import { DashboardView } from "./views/DashboardView";
import { DiscoverView } from "./views/DiscoverView";
import { EmailsView } from "./views/EmailsView";
import { LeadsView } from "./views/LeadsView";
import { SettingsView } from "./views/SettingsView";
import { TasksView } from "./views/TasksView";

const ENABLE_DAYBREAK = import.meta.env.VITE_ENABLE_DAYBREAK === "true";

const baseTabs = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "clients", label: "Contacts", icon: Users },
  { id: "leads", label: "Leads", icon: Megaphone },
  { id: "tasks", label: "Tasks", icon: ListTodo },
  { id: "emails", label: "Emails", icon: Mail },
  { id: "calendar", label: "Calendar", icon: CalendarDays },
  { id: "discover", label: "Discover", icon: SearchIcon },
  { id: "settings", label: "Settings", icon: Settings },
];

const selfManagedTabs = new Set(["dashboard", "tasks", "emails", "settings", "discover", "briefing"]);
const ACTIVE_TAB_STORAGE_KEY = "crm.activeTab";

function getStoredActiveTab(tabs) {
  if (typeof window === "undefined") {
    return "dashboard";
  }

  try {
    const storedTab = window.localStorage.getItem(ACTIVE_TAB_STORAGE_KEY);
    return tabs.some((tab) => tab.id === storedTab) ? storedTab : "dashboard";
  } catch {
    return "dashboard";
  }
}

export function App() {
  const tabs = useMemo(
    () => (ENABLE_DAYBREAK ? [...baseTabs, { id: "briefing", label: "Daybreak", icon: Sunrise }] : baseTabs),
    []
  );
  const [activeTab, setActiveTab] = useState(() => getStoredActiveTab(tabs));
  const [data, setData] = useState({});
  const [loading, setLoading] = useState({});
  const [errors, setErrors] = useState({});
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);

  const activeConfig = useMemo(
    () => tabs.find((tab) => tab.id === activeTab) || tabs[0],
    [activeTab, tabs]
  );

  useEffect(() => {
    try {
      window.localStorage.setItem(ACTIVE_TAB_STORAGE_KEY, activeTab);
    } catch {
      // The tab still works even if storage is blocked.
    }
  }, [activeTab]);

  useEffect(() => {
    if (selfManagedTabs.has(activeTab) || activeTab === "leads") {
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
        setData((current) => ({ ...current, [activeTab]: [] }));
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

  function handleLeadCreated(createdLead) {
    setData((current) => ({
      ...current,
      leads: [createdLead, ...(current.leads || [])],
    }));
  }

  function handleLeadDeleted(leadId) {
    setData((current) => ({
      ...current,
      leads: (current.leads || []).filter((lead) => lead.id !== leadId),
    }));
  }

  function handleLeadsBulkUpdated(updatedLeads) {
    const updatedById = new Map(updatedLeads.map((lead) => [lead.id, lead]));
    setData((current) => ({
      ...current,
      leads: (current.leads || []).map((lead) => updatedById.get(lead.id) || lead),
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

  function handleClientDeleted(clientId) {
    setData((current) => ({
      ...current,
      clients: (current.clients || []).filter((client) => client.id !== clientId),
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

  async function handleGlobalSearch(event) {
    event.preventDefault();
    if (!searchQuery.trim() || searchLoading) {
      return;
    }

    setSearchLoading(true);
    setSearchError("");
    setSearchOpen(true);
    try {
      setSearchResults(await fetchSearch(searchQuery));
    } catch (error) {
      setSearchError(error.message);
      setSearchResults([]);
    } finally {
      setSearchLoading(false);
    }
  }

  function openSearchResult(result) {
    const tabByType = {
      client: "clients",
      lead: "leads",
      task: "tasks",
      calendar: "calendar",
      note: "leads",
    };
    setActiveTab(tabByType[result.type] || "dashboard");
    setSearchOpen(false);
  }

  const views = {
    dashboard: <DashboardView />,
    briefing: <BriefingView />,
    clients: <ClientsView rows={rows} onClientCreated={handleClientCreated} onClientDeleted={handleClientDeleted} />,
    leads: (
      <LeadsView
        rows={rows}
        onLeadCreated={handleLeadCreated}
        onLeadDeleted={handleLeadDeleted}
        onLeadUpdated={handleLeadUpdated}
        onLeadsBulkUpdated={handleLeadsBulkUpdated}
      />
    ),
    tasks: <TasksView />,
    emails: <EmailsView />,
    calendar: (
      <CalendarView
        clients={data.clients || []}
        rows={rows}
        onCalendarItemCreated={handleCalendarItemCreated}
      />
    ),
    discover: <DiscoverView onLeadsChanged={handleLeadsChanged} />,
    settings: <SettingsView />,
  };

  const statusCopy = {
    dashboard: ["green", "Overview"],
    clients: ["green", "CRM records"],
    leads: ["blue", "Pipeline"],
    tasks: ["yellow", "Follow-ups"],
    emails: ["blue", "IMAP mail"],
    calendar: ["green", "Schedule"],
    discover: ["blue", "Lead discovery"],
    settings: ["yellow", "System health"],
    briefing: ["purple", "Hidden by default"],
  };
  const [statusTone, statusLabel] = statusCopy[activeTab] || ["green", "CRM API"];
  const shouldWrapDataState = !selfManagedTabs.has(activeTab);

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
                aria-current={tab.id === activeTab ? "page" : undefined}
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

      <section aria-labelledby="page-title" className="content">
        <header className="page-header">
          <div>
            <p>CRM workspace</p>
            <h1 id="page-title">{activeConfig.label}</h1>
          </div>
          <div className="header-actions">
            <form
              aria-label="Search CRM"
              className="global-search"
              onSubmit={handleGlobalSearch}
              role="search"
            >
              <SearchIcon size={16} aria-hidden="true" />
              <input
                aria-controls="global-search-results"
                aria-expanded={searchOpen}
                aria-label="Search CRM records"
                onChange={(event) => setSearchQuery(event.target.value)}
                onFocus={() => searchResults.length && setSearchOpen(true)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    setSearchOpen(false);
                  }
                }}
                placeholder="Search CRM"
                type="search"
                value={searchQuery}
              />
              <button
                aria-label={searchLoading ? "Searching CRM" : "Run CRM search"}
                disabled={!searchQuery.trim() || searchLoading}
                type="submit"
              >
                {searchLoading ? "..." : "Go"}
              </button>
              {searchOpen ? (
                <div
                  aria-live="polite"
                  className="global-search-results"
                  id="global-search-results"
                >
                  {searchLoading ? <p role="status">Searching...</p> : null}
                  {searchError ? <p className="search-error" role="alert">{searchError}</p> : null}
                  {!searchError && searchResults.length === 0 && !searchLoading ? <p>No matches.</p> : null}
                  {searchResults.map((result) => (
                    <button
                      aria-label={`Open ${result.title}`}
                      key={`${result.type}-${result.id}`}
                      onClick={() => openSearchResult(result)}
                      type="button"
                    >
                      <strong>{result.title}</strong>
                      <span>{result.type} {result.subtitle ? `- ${result.subtitle}` : ""}</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </form>
            <StatusBadge tone={statusTone}>{statusLabel}</StatusBadge>
          </div>
        </header>

        {shouldWrapDataState ? (
          <DataState
            loading={loading[activeTab]}
            error={errors[activeTab]}
            isEmpty={activeTab === "emails" && rows.length === 0}
          >
            {views[activeTab]}
          </DataState>
        ) : (
          views[activeTab]
        )}
      </section>
    </main>
  );
}
