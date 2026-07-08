import React, { useEffect, useMemo, useState } from "react";
import { CalendarDays, Mail, Megaphone, Search, Sunrise, Users, WalletCards } from "lucide-react";

import { fetchResource } from "./api";
import { DataState, StatusBadge } from "./components/common";
import { BriefingView } from "./views/BriefingView";
import { CalendarView } from "./views/CalendarView";
import { ClientsView } from "./views/ClientsView";
import { DiscoverView } from "./views/DiscoverView";
import { EmailsView } from "./views/EmailsView";
import { EventsView } from "./views/EventsView";
import { LeadsView } from "./views/LeadsView";

const tabs = [
  { id: "briefing", label: "Daybreak", icon: Sunrise },
  { id: "clients", label: "Clients", icon: Users },
  { id: "leads", label: "Leads", icon: Megaphone },
  { id: "events", label: "Events", icon: WalletCards },
  { id: "emails", label: "Emails", icon: Mail },
  { id: "calendar", label: "Calendar", icon: CalendarDays },
  { id: "discover", label: "Discover", icon: Search },
];

export function App() {
  const [activeTab, setActiveTab] = useState("clients");
  const [data, setData] = useState({});
  const [loading, setLoading] = useState({});
  const [errors, setErrors] = useState({});

  const activeConfig = useMemo(
    () => tabs.find((tab) => tab.id === activeTab),
    [activeTab]
  );

  useEffect(() => {
    if (activeTab === "discover" || activeTab === "leads" || activeTab === "briefing") {
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

  function handleClientCreated(createdClient) {
    setData((current) => ({
      ...current,
      clients: [...(current.clients || []), createdClient].sort((left, right) =>
        left.name.localeCompare(right.name)
      ),
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

  const views = {
    briefing: <BriefingView />,
    clients: <ClientsView rows={rows} onClientCreated={handleClientCreated} />,
    leads: <LeadsView rows={rows} onLeadUpdated={handleLeadUpdated} />,
    events: <EventsView rows={rows} />,
    emails: <EmailsView rows={rows} />,
    calendar: (
      <CalendarView
        clients={data.clients || []}
        rows={rows}
        onCalendarItemCreated={handleCalendarItemCreated}
      />
    ),
    discover: <DiscoverView onLeadsChanged={handleLeadsChanged} />,
  };

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
            <p>CRM workspace</p>
            <h1>{activeConfig.label}</h1>
          </div>
          <StatusBadge tone={activeTab === "briefing" ? "purple" : activeTab === "discover" ? "blue" : "green"}>
            {activeTab === "briefing" ? "Live" : activeTab === "discover" ? "Live workflow" : activeTab === "leads" ? "Tender leads" : "CRM API"}
          </StatusBadge>
        </header>

        {activeTab === "briefing" ? (
          views.briefing
        ) : (
          <DataState
            loading={loading[activeTab]}
            error={errors[activeTab]}
            isEmpty={activeTab !== "discover" && rows.length === 0}
          >
            {views[activeTab]}
          </DataState>
        )}
      </section>
    </main>
  );
}
