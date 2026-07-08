import React from "react";

import { StatusBadge } from "../components/common";
import { formatDateTime } from "../utils/format";

export function EmailsView({ rows }) {
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
