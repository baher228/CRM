import React, { useMemo, useState } from "react";
import { CalendarPlus } from "lucide-react";

import { createCalendarItem } from "../api";
import { formatDate } from "../utils/format";

export function CalendarView({ rows, clients, onCalendarItemCreated }) {
  const [showAddForm, setShowAddForm] = useState(false);
  const [form, setForm] = useState({
    title: "",
    date: "",
    start_time: "",
    end_time: "",
    related_client_id: "",
    related_to: "",
    notes: "",
  });
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const groupedRows = rows.reduce((groups, item) => {
    groups[item.date] = groups[item.date] || [];
    groups[item.date].push(item);
    return groups;
  }, {});
  const agendaEntries = Object.entries(groupedRows);

  function updateField(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function selectClient(value) {
    const selected = clients.find((client) => String(client.id) === value);
    setForm((current) => ({
      ...current,
      related_client_id: value,
      related_to: selected ? selected.company || selected.name : current.related_to,
    }));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (!form.title.trim() || !form.date || !form.start_time || !form.end_time || saving) {
      return;
    }

    setSaving(true);
    setMessage("");
    setError("");
    try {
      const created = await createCalendarItem({
        title: form.title.trim(),
        date: form.date,
        start_time: form.start_time,
        end_time: form.end_time,
        related_client_id: form.related_client_id ? Number(form.related_client_id) : null,
        related_to: form.related_to.trim(),
        notes: form.notes.trim(),
      });
      onCalendarItemCreated(created);
      setMessage(created.last_sync_message || "Event saved");
      setForm({
        title: "",
        date: "",
        start_time: "",
        end_time: "",
        related_client_id: "",
        related_to: "",
        notes: "",
      });
      setShowAddForm(false);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="calendar-workspace">
      <div className="view-actions">
        <span aria-live="polite" className={error ? "form-message form-error" : "form-message"}>
          {error || message}
        </span>
        <button className="secondary-action action-with-icon" onClick={() => setShowAddForm((open) => !open)} type="button">
          <CalendarPlus size={17} aria-hidden="true" />
          {showAddForm ? "Close" : "Add event"}
        </button>
      </div>

      {showAddForm ? (
        <form className="workflow-panel compact-form" onSubmit={handleSubmit}>
          <div className="field-grid calendar-field-grid">
            <label>
              <span>Title</span>
              <input
                onChange={(event) => updateField("title", event.target.value)}
                placeholder="Follow-up call"
                required
                type="text"
                value={form.title}
              />
            </label>
            <label>
              <span>Date</span>
              <input
                onChange={(event) => updateField("date", event.target.value)}
                required
                type="date"
                value={form.date}
              />
            </label>
            <label>
              <span>Start</span>
              <input
                onChange={(event) => updateField("start_time", event.target.value)}
                required
                type="time"
                value={form.start_time}
              />
            </label>
            <label>
              <span>End</span>
              <input
                onChange={(event) => updateField("end_time", event.target.value)}
                required
                type="time"
                value={form.end_time}
              />
            </label>
            <label>
              <span>Contact</span>
              <select onChange={(event) => selectClient(event.target.value)} value={form.related_client_id}>
                <option value="">No linked contact</option>
                {clients.map((client) => (
                  <option key={client.id} value={client.id}>
                    {client.name}{client.company ? ` - ${client.company}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Related to</span>
              <input
                onChange={(event) => updateField("related_to", event.target.value)}
                placeholder="Company or opportunity"
                type="text"
                value={form.related_to}
              />
            </label>
            <label className="wide-field">
              <span>Notes</span>
              <input
                onChange={(event) => updateField("notes", event.target.value)}
                placeholder="Agenda, prep, next step"
                type="text"
                value={form.notes}
              />
            </label>
          </div>
          <div className="workflow-actions form-actions">
            <button disabled={!form.title.trim() || !form.date || !form.start_time || !form.end_time || saving} type="submit">
              <CalendarPlus size={17} aria-hidden="true" />
              {saving ? "Saving..." : "Save event"}
            </button>
          </div>
        </form>
      ) : null}

      <div className="agenda">
        {agendaEntries.length ? (
          agendaEntries.map(([date, items]) => (
            <section className="agenda-day" key={date}>
              <h3>{formatDate(date)}</h3>
              {items.map((item) => (
                <article className="calendar-event" key={item.id}>
                  <time className="calendar-event-time">
                    {item.start_time.slice(0, 5)} - {item.end_time.slice(0, 5)}
                  </time>
                  <div className="calendar-event-body">
                    <div className="calendar-event-topline">
                      <strong>{item.title}</strong>
                      {item.last_sync_message ? <span>{item.last_sync_message}</span> : null}
                    </div>
                    <span className="calendar-event-relation">{item.related_to || "No relation"}</span>
                    {item.notes ? <p>{item.notes}</p> : null}
                  </div>
                </article>
              ))}
            </section>
          ))
        ) : (
          <div className="state state-empty" role="status">
            <strong>No events scheduled</strong>
            <span>Add an event to start building the agenda.</span>
          </div>
        )}
      </div>
    </div>
  );
}
