import React, { useEffect, useId, useMemo, useState } from "react";
import { ArrowRight, CheckCircle2 } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { api } from "../api";
import { useResource } from "../hooks";
import { buildCreatePayload } from "../utils/business";
import { createTypes, resourceConfigs } from "../workspace";
import { AppDialog } from "./common";

const emptyForm = { type: "accounts", name: "", email: "", company: "", account_id: "", value: "", next_action: "", due_at: "" };

export function QuickCreate({ open, initialType, onClose }) {
  const navigate = useNavigate();
  const [form, setForm] = useState(emptyForm);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const nameErrorId = useId();
  const formErrorId = useId();
  const { data: accounts } = useResource("accounts");
  const selected = useMemo(() => createTypes.find((item) => item.value === form.type) || createTypes[0], [form.type]);

  useEffect(() => {
    if (open) setForm({ ...emptyForm, type: createTypes.some((item) => item.value === initialType) ? initialType : "accounts" });
    setError(null);
  }, [open, initialType]);

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.post(form.type, buildCreatePayload(form));
      const config = resourceConfigs[form.type];
      onClose();
      navigate(created?.id && config ? `${config.path}/${created.id}` : config?.path || "/");
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  const nameError = error?.fieldErrors?.name || error?.fieldErrors?.title;
  const nameErrorText = nameError ? (Array.isArray(nameError) ? nameError.join(" ") : String(nameError)) : "";

  return (
    <AppDialog className="quick-create-dialog" description="Create the minimum record now; add context from its workspace." onClose={onClose} open={open} title="Quick create">
      <form aria-busy={busy} aria-describedby={error ? formErrorId : undefined} className="quick-create-form" onSubmit={submit}>
        <label><span>Record type</span><select onChange={(event) => update("type", event.target.value)} value={form.type}>{createTypes.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}</select></label>
        <label><span>{selected.nameLabel}</span><input aria-describedby={nameErrorText ? nameErrorId : undefined} aria-invalid={nameErrorText ? "true" : undefined} autoFocus data-dialog-initial-focus onChange={(event) => update("name", event.target.value)} required value={form.name} />{nameErrorText ? <small className="field-error-text" id={nameErrorId}>{nameErrorText}</small> : null}</label>
        {["contacts", "leads"].includes(form.type) ? <label><span>Email</span><input autoComplete="email" onChange={(event) => update("email", event.target.value)} type="email" value={form.email} /></label> : null}
        {["accounts", "leads"].includes(form.type) ? <label><span>{form.type === "accounts" ? "Website or domain" : "Company"}</span><input onChange={(event) => update("company", event.target.value)} value={form.company} /></label> : null}
        {["contacts", "leads", "opportunities"].includes(form.type) ? <label><span>Account {form.type === "opportunities" ? "(required)" : "(optional)"}</span><select onChange={(event) => update("account_id", event.target.value)} required={form.type === "opportunities"} value={form.account_id}><option value="">{accounts.length ? "Select account" : "Create an account first"}</option>{accounts.map((account) => <option key={account.id} value={account.id}>{account.name}</option>)}</select></label> : null}
        {form.type === "opportunities" ? <label><span>Expected value (£)</span><input min="0" onChange={(event) => update("value", event.target.value)} step="0.01" type="number" value={form.value} /></label> : null}
        {form.type === "opportunities" ? <label><span>Next action</span><input onChange={(event) => update("next_action", event.target.value)} placeholder="Book discovery call" value={form.next_action} /></label> : null}
        {form.type === "tasks" ? <label><span>Due</span><input onChange={(event) => update("due_at", event.target.value)} type="datetime-local" value={form.due_at} /></label> : null}
        {error ? <div className="form-error" id={formErrorId} role="alert"><strong>Could not create this record.</strong><span>{error.message}</span></div> : null}
        <span aria-live="polite" className="sr-only" role="status">{busy ? `Creating ${selected.label.toLowerCase()}` : ""}</span>
        <footer><span><CheckCircle2 aria-hidden="true" size={15} /> Saved locally first</span><button className="button button-primary" disabled={busy} type="submit">{busy ? "Creating…" : `Create ${selected.label.toLowerCase()}`} <ArrowRight aria-hidden="true" size={15} /></button></footer>
      </form>
    </AppDialog>
  );
}
