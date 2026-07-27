import React, { useEffect, useState } from "react";
import { FileQuestion, ShieldAlert } from "lucide-react";
import { Link, Navigate, Route, Routes } from "react-router-dom";

import { CommandPalette } from "./components/CommandPalette";
import { EmptyState } from "./components/common";
import { QuickCreate } from "./components/QuickCreate";
import { Shell } from "./components/Shell";
import { initializeSession } from "./api";
import { RecordWorkspace } from "./views/RecordWorkspace";
import { ResourceView } from "./views/ResourceView";
import { TodayView } from "./views/TodayView";
import {
  AutomationsView,
  BillingView,
  CalendarView,
  ClientSuccessView,
  CommercialView,
  FilesView,
  InboxView,
  PipelineView,
  ProjectsView,
  ReportsView,
  SequencesView,
  SettingsView,
  TenderRadarView,
} from "./views/WorkspaceViews";

function NotFound() {
  return <EmptyState action={<Link className="button button-primary" to="/">Back to Today</Link>} icon={FileQuestion} message="The address may be old, or the record may have been merged." title="Workspace not found" />;
}

export function App() {
  const [commandOpen, setCommandOpen] = useState(false);
  const [createState, setCreateState] = useState({ open: false, type: "" });
  const [session, setSession] = useState({ error: "" });

  useEffect(() => {
    let active = true;
    initializeSession()
      .then(() => active && setSession({ error: "" }))
      .catch((error) => active && setSession({ error: error.message }));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    function handleKeyDown(event) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen(true);
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "n") {
        event.preventDefault();
        setCreateState({ open: true, type: "" });
      }
    }
    function handleQuickCreate(event) {
      setCreateState({ open: true, type: event.detail?.type || "" });
    }
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("crm:quick-create", handleQuickCreate);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("crm:quick-create", handleQuickCreate);
    };
  }, []);

  if (session.error) {
    return <main className="route-state"><EmptyState icon={ShieldAlert} title="Secure session required" message={session.error} /></main>;
  }

  return (
    <Shell onOpenCommand={() => setCommandOpen(true)} onOpenCreate={() => setCreateState({ open: true, type: "" })}>
      <Routes>
        <Route element={<TodayView />} path="/" />
        <Route element={<InboxView />} path="/inbox" />
        <Route element={<CalendarView />} path="/calendar" />
        <Route element={<ResourceView resourceKey="accounts" />} path="/accounts" />
        <Route element={<RecordWorkspace resourceKey="accounts" />} path="/accounts/:id" />
        <Route element={<ResourceView resourceKey="contacts" />} path="/contacts" />
        <Route element={<RecordWorkspace resourceKey="contacts" />} path="/contacts/:id" />
        <Route element={<PipelineView />} path="/pipeline" />
        <Route element={<ResourceView resourceKey="leads" />} path="/leads" />
        <Route element={<RecordWorkspace resourceKey="leads" />} path="/leads/:id" />
        <Route element={<ResourceView resourceKey="opportunities" />} path="/opportunities" />
        <Route element={<RecordWorkspace resourceKey="opportunities" />} path="/opportunities/:id" />
        <Route element={<TenderRadarView />} path="/tenders" />
        <Route element={<RecordWorkspace resourceKey="tenders" />} path="/tenders/:id" />
        <Route element={<SequencesView />} path="/sequences" />
        <Route element={<RecordWorkspace resourceKey="sequences" />} path="/sequences/:id" />
        <Route element={<ProjectsView />} path="/projects" />
        <Route element={<RecordWorkspace resourceKey="projects" />} path="/projects/:id" />
        <Route element={<ResourceView resourceKey="time-entries" />} path="/time" />
        <Route element={<RecordWorkspace resourceKey="time-entries" />} path="/time/:id" />
        <Route element={<CommercialView />} path="/proposals" />
        <Route element={<RecordWorkspace resourceKey="proposals" />} path="/proposals/:id" />
        <Route element={<Navigate replace to="/proposals?view=contracts" />} path="/contracts" />
        <Route element={<RecordWorkspace resourceKey="contracts" />} path="/contracts/:id" />
        <Route element={<BillingView />} path="/billing" />
        <Route element={<Navigate replace to="/billing" />} path="/invoices" />
        <Route element={<RecordWorkspace resourceKey="invoices" />} path="/invoices/:id" />
        <Route element={<Navigate replace to="/billing?view=payments" />} path="/payments" />
        <Route element={<RecordWorkspace resourceKey="payments" />} path="/payments/:id" />
        <Route element={<Navigate replace to="/billing?view=credit-notes" />} path="/credit-notes" />
        <Route element={<RecordWorkspace resourceKey="credit-notes" />} path="/credit-notes/:id" />
        <Route element={<ClientSuccessView />} path="/client-success" />
        <Route element={<RecordWorkspace resourceKey="client-success" />} path="/client-success/:id" />
        <Route element={<RecordWorkspace resourceKey="milestones" />} path="/milestones/:id" />
        <Route element={<Navigate replace to="/projects" />} path="/milestones" />
        <Route element={<RecordWorkspace resourceKey="expenses" />} path="/expenses/:id" />
        <Route element={<Navigate replace to="/projects" />} path="/expenses" />
        <Route element={<ReportsView />} path="/reports" />
        <Route element={<AutomationsView />} path="/automations" />
        <Route element={<RecordWorkspace resourceKey="automations" />} path="/automations/:id" />
        <Route element={<FilesView />} path="/files" />
        <Route element={<RecordWorkspace resourceKey="files" />} path="/files/:id" />
        <Route element={<ResourceView resourceKey="tasks" />} path="/tasks" />
        <Route element={<RecordWorkspace resourceKey="tasks" />} path="/tasks/:id" />
        <Route element={<SettingsView />} path="/settings" />
        <Route element={<NotFound />} path="*" />
      </Routes>
      <CommandPalette onClose={() => setCommandOpen(false)} onCreate={() => setCreateState({ open: true, type: "" })} open={commandOpen} />
      <QuickCreate initialType={createState.type} onClose={() => setCreateState({ open: false, type: "" })} open={createState.open} />
    </Shell>
  );
}
