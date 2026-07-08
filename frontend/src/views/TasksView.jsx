import React, { useEffect, useMemo, useState } from "react";
import { CheckCircle2, ListPlus, RotateCcw, Trash2 } from "lucide-react";

import { createTask, deleteTask, fetchResource, updateTask } from "../api";
import { StatusBadge, TableView } from "../components/common";
import { formatDate } from "../utils/format";

const initialForm = {
  title: "",
  due_date: "",
  related_type: "lead",
  related_id: "",
  related_to: "",
  priority: "Medium",
  notes: "",
};

export function TasksView() {
  const [tasks, setTasks] = useState([]);
  const [statusFilter, setStatusFilter] = useState("Open");
  const [form, setForm] = useState(initialForm);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    let cancelled = false;

    fetchResource("tasks")
      .then((payload) => {
        if (!cancelled) {
          setTasks(payload);
          setError("");
        }
      })
      .catch((requestError) => {
        if (!cancelled) {
          setError(requestError.message);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const visibleTasks = useMemo(
    () => tasks.filter((task) => statusFilter === "All" || task.status === statusFilter),
    [statusFilter, tasks]
  );

  function updateField(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (!form.title.trim() || saving) {
      return;
    }

    setSaving(true);
    setError("");
    setMessage("");
    try {
      const created = await createTask({
        ...form,
        title: form.title.trim(),
        due_date: form.due_date || null,
        related_id: form.related_id ? Number(form.related_id) : null,
        related_to: form.related_to.trim(),
        notes: form.notes.trim(),
      });
      setTasks((current) => [...current, created]);
      setForm(initialForm);
      setMessage("Follow-up saved");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  async function setTaskStatus(task, status) {
    setError("");
    setMessage("");
    try {
      const updated = await updateTask(task.id, { status });
      setTasks((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setMessage(status === "Done" ? "Follow-up completed" : "Follow-up reopened");
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function removeTask(task) {
    if (!window.confirm(`Delete "${task.title}"? This cannot be undone.`)) {
      return;
    }

    setError("");
    setMessage("");
    try {
      await deleteTask(task.id);
      setTasks((current) => current.filter((item) => item.id !== task.id));
      setMessage("Follow-up deleted");
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  if (loading) {
    return <div aria-busy="true" className="state" role="status">Loading follow-ups...</div>;
  }

  return (
    <div className="tasks-workspace">
      <form className="workflow-panel compact-form" onSubmit={handleSubmit}>
        <div className="section-heading">
          <div>
            <span>Follow-ups</span>
            <h2>Create task</h2>
          </div>
          <StatusBadge tone="green">{tasks.filter((task) => task.status !== "Done").length} open</StatusBadge>
        </div>
        <div className="field-grid task-field-grid">
          <label>
            <span>Title</span>
            <input
              onChange={(event) => updateField("title", event.target.value)}
              placeholder="Call decision maker"
              required
              type="text"
              value={form.title}
            />
          </label>
          <label>
            <span>Due</span>
            <input onChange={(event) => updateField("due_date", event.target.value)} type="date" value={form.due_date} />
          </label>
          <label>
            <span>Priority</span>
            <select onChange={(event) => updateField("priority", event.target.value)} value={form.priority}>
              {["Low", "Medium", "High"].map((priority) => (
                <option key={priority} value={priority}>
                  {priority}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Related type</span>
            <select onChange={(event) => updateField("related_type", event.target.value)} value={form.related_type}>
              {["lead", "client", "company"].map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Record id</span>
            <input min="1" onChange={(event) => updateField("related_id", event.target.value)} type="number" value={form.related_id} />
          </label>
          <label>
            <span>Related to</span>
            <input
              onChange={(event) => updateField("related_to", event.target.value)}
              placeholder="Company or contact"
              type="text"
              value={form.related_to}
            />
          </label>
          <label className="wide-field">
            <span>Notes</span>
            <input
              onChange={(event) => updateField("notes", event.target.value)}
              placeholder="Context for the next action"
              type="text"
              value={form.notes}
            />
          </label>
        </div>
        <div className="workflow-actions form-actions">
          <span aria-live="polite" className={error ? "form-message form-error" : "form-message"}>{error || message}</span>
          <button disabled={!form.title.trim() || saving} type="submit">
            <ListPlus size={17} aria-hidden="true" />
            {saving ? "Saving..." : "Save follow-up"}
          </button>
        </div>
      </form>

      <div className="leads-toolbar task-toolbar">
        <select onChange={(event) => setStatusFilter(event.target.value)} value={statusFilter}>
          {["Open", "Done", "All"].map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
      </div>

      {error && !message ? <div className="state state-error compact-state" role="alert">{error}</div> : null}

      <TableView
        label="Follow-up tasks"
        columns={["Task", "Due", "Related", "Priority", "Sync", "Actions"]}
        rows={visibleTasks}
        renderRow={(task) => (
          <tr key={task.id}>
            <td>
              <strong>{task.title}</strong>
              {task.notes ? <span>{task.notes}</span> : null}
            </td>
            <td>{formatDate(task.due_date)}</td>
            <td>
              {task.related_to || "-"}
              {task.related_type ? <span>{task.related_type} {task.related_id || ""}</span> : null}
            </td>
            <td>
              <StatusBadge tone={task.priority === "High" ? "red" : task.priority === "Low" ? "blue" : "yellow"}>
                {task.priority}
              </StatusBadge>
            </td>
            <td>{task.last_sync_message || task.sync_status || "-"}</td>
            <td>
              <div className="row-actions">
                {task.status === "Done" ? (
                  <button className="secondary-action" onClick={() => setTaskStatus(task, "Open")} type="button">
                    <RotateCcw size={14} aria-hidden="true" />
                    Reopen
                  </button>
                ) : (
                  <button className="confirm-action" onClick={() => setTaskStatus(task, "Done")} type="button">
                    <CheckCircle2 size={14} aria-hidden="true" />
                    Done
                  </button>
                )}
                <button className="reject-action" onClick={() => removeTask(task)} type="button">
                  <Trash2 size={14} aria-hidden="true" />
                  Delete
                </button>
              </div>
            </td>
          </tr>
        )}
      />
    </div>
  );
}
