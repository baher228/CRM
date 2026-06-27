import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  CalendarDays,
  Mail,
  Megaphone,
  Users,
  WalletCards,
} from "lucide-react";
import { fetchResource } from "./api";
import "./styles.css";

const tabs = [
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

function App() {
  const [activeTab, setActiveTab] = useState("clients");
  const [data, setData] = useState({});
  const [loading, setLoading] = useState({});
  const [errors, setErrors] = useState({});

  const activeConfig = useMemo(
    () => tabs.find((tab) => tab.id === activeTab),
    [activeTab]
  );

  useEffect(() => {
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
            <p>CRM scaffold</p>
            <h1>{activeConfig.label}</h1>
          </div>
          <StatusBadge tone="green">Dummy API</StatusBadge>
        </header>

        <DataState
          loading={loading[activeTab]}
          error={errors[activeTab]}
          isEmpty={rows.length === 0}
        >
          {views[activeTab]}
        </DataState>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

