import React from "react";

import { StatusBadge } from "../components/common";
import { formatDateTime } from "../utils/format";

export function EventsView({ rows }) {
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
