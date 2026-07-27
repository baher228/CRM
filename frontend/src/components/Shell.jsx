import React, { useState } from "react";
import { Bell, ChevronDown, Command, Menu, MoreHorizontal, Plus, Search, Zap } from "lucide-react";
import { Link, NavLink, useLocation } from "react-router-dom";

import { api } from "../api";
import { useResource } from "../hooks";
import { allNavigationItems, mobileNavigation, navigation } from "../workspace";
import { AppDialog, Avatar, EmptyState, LoadingState, SignalLine, UnavailableState } from "./common";

function activePath(pathname, path) {
  const aliases = {
    "/pipeline": ["/leads", "/opportunities"],
    "/proposals": ["/contracts"],
    "/billing": ["/invoices", "/payments", "/credit-notes"],
  };
  if (aliases[path]?.some((alias) => pathname === alias || pathname.startsWith(`${alias}/`))) return true;
  return path === "/" ? pathname === "/" : pathname === path || pathname.startsWith(`${path}/`);
}

export function Shell({ children, onOpenCommand, onOpenCreate }) {
  const location = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const notifications = useResource("notifications", { query: { unread_only: true } });
  const inbox = useResource("email/threads", { query: { unread: true, limit: 100 } });
  const current = [...allNavigationItems].sort((left, right) => right.path.length - left.path.length).find((item) => activePath(location.pathname, item.path));

  async function markNotificationRead(item) {
    await api.patch(`notifications/${item.id}`, { version: item.version, read: true });
    await notifications.reload();
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <aside className="command-rail">
        <NavLink aria-label="CRM Workspace home" className="brand-mark" to="/">
          <span aria-hidden="true">CW</span>
          <strong>CRM<br />Workspace</strong>
        </NavLink>
        <div className="operator-chip"><Avatar name="Local operator" size="sm" /><span><strong>Operator</strong><small>Local workspace</small></span><ChevronDown aria-hidden="true" size={14} /></div>
        <nav aria-label="Primary navigation">
          {navigation.map((group) => (
            <div className="nav-group" key={group.label}>
              <span className="nav-group-label">{group.label}</span>
              {group.items.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink aria-label={item.label} className={() => activePath(location.pathname, item.path) ? "active" : ""} end={item.path === "/"} key={item.path} title={item.label} to={item.path}>
                    <Icon aria-hidden="true" size={17} /><span>{item.label}</span>{item.path === "/inbox" && inbox.data.length ? <i aria-label={`${inbox.data.length} unread messages`}>{inbox.data.length > 99 ? "99+" : inbox.data.length}</i> : null}
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="rail-status"><SignalLine label="Local service" /><small>Secure on this device</small></div>
      </aside>

      <div className="workspace">
        <header className="workspace-header">
          <div className="mobile-brand"><span>CW</span><strong>{current?.label || "Workspace"}</strong></div>
          <div className="workspace-context"><span>Workspace</span><strong>{current?.label || "Record"}</strong></div>
          <div className="global-actions">
            <button className="command-trigger" onClick={onOpenCommand} type="button"><Search aria-hidden="true" size={16} /><span>Search or jump to…</span><kbd><Command aria-hidden="true" size={12} /> K</kbd></button>
            <button aria-expanded={notificationsOpen} aria-label={`Notifications, ${notifications.data.length} unread`} className="icon-button notification-button" onClick={() => { setNotificationsOpen(true); notifications.reload(); }} type="button"><Bell aria-hidden="true" size={18} />{notifications.data.length ? <i aria-hidden="true" /> : null}</button>
            <button className="button button-primary header-new" onClick={onOpenCreate} type="button"><Plus aria-hidden="true" size={16} /> New <kbd>Ctrl N</kbd></button>
          </div>
        </header>
        <main className="workspace-content" id="main-content" tabIndex="-1">{children}</main>
      </div>

      <nav aria-label="Mobile navigation" className="mobile-nav">
        {mobileNavigation.map((item) => {
          const Icon = item.icon;
          return <NavLink aria-label={item.label} className={() => activePath(location.pathname, item.path) ? "active" : ""} end={item.path === "/"} key={item.path} to={item.path}><Icon aria-hidden="true" size={20} /><span>{item.label}</span></NavLink>;
        })}
        <button aria-expanded={moreOpen} aria-haspopup="dialog" onClick={() => setMoreOpen(true)} type="button"><MoreHorizontal aria-hidden="true" size={20} /><span>More</span></button>
      </nav>

      <AppDialog className="more-dialog" description="Every workspace remains available on mobile." onClose={() => setMoreOpen(false)} open={moreOpen} title="More">
        <nav aria-label="More CRM sections" className="more-grid">{allNavigationItems.filter((item) => !mobileNavigation.includes(item)).map((item) => { const Icon = item.icon; return <NavLink key={item.path} onClick={() => setMoreOpen(false)} to={item.path}><Icon aria-hidden="true" size={19} /><span>{item.label}</span></NavLink>; })}</nav>
        <button className="button button-quiet more-create" onClick={() => { setMoreOpen(false); setNotificationsOpen(true); notifications.reload(); }} type="button"><Bell aria-hidden="true" size={16} /> Notifications{notifications.data.length ? ` (${notifications.data.length})` : ""}</button>
        <button className="button button-primary more-create" onClick={() => { setMoreOpen(false); onOpenCreate(); }} type="button"><Zap aria-hidden="true" size={16} /> Quick create</button>
      </AppDialog>

      <AppDialog className="workflow-dialog" description="Durable alerts from billing, integrations, renewals and automations." onClose={() => setNotificationsOpen(false)} open={notificationsOpen} title="Notifications">
        <div aria-busy={notifications.loading} className="workflow-fields">
          {notifications.loading ? <LoadingState label="Loading notifications" /> : null}
          {!notifications.loading && notifications.error ? <UnavailableState compact error={notifications.error} onRetry={notifications.reload} /> : null}
          {!notifications.loading && !notifications.error && !notifications.data.length ? <EmptyState message="New operational alerts will appear here." title="You are all caught up" /> : null}
          {!notifications.loading && !notifications.error ? notifications.data.map((item) => (
            <article className="record-section field-wide" key={item.id}>
              <header><h2>{item.title}</h2><span>{item.severity}</span></header>
              <div className="focus-card">
                <span><Bell aria-hidden="true" size={17} /></span>
                <div><strong>{item.title}</strong><p>{item.body || "Open the linked workspace for details."}</p></div>
                <div className="record-workflow-actions">
                  {item.action_url ? <Link className="button button-quiet" onClick={() => setNotificationsOpen(false)} to={item.action_url}>Open</Link> : null}
                  <button className="button button-quiet" onClick={() => markNotificationRead(item)} type="button">Mark read</button>
                </div>
              </div>
            </article>
          )) : null}
        </div>
      </AppDialog>
    </div>
  );
}
