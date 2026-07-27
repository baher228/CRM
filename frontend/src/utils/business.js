export function buildCreatePayload(form) {
  const name = form.name.trim();
  const payloads = {
    accounts: { name, domain: (form.company || "").trim(), status: "Prospect" },
    contacts: { display_name: name, email: (form.email || "").trim() || undefined, account_id: form.account_id ? Number(form.account_id) : undefined },
    leads: { title: name, company: (form.company || "").trim(), email: (form.email || "").trim() || undefined, account_id: form.account_id ? Number(form.account_id) : undefined, status: "New" },
    opportunities: { title: name, account_id: form.account_id ? Number(form.account_id) : undefined, value_minor: poundsToMinor(form.value), next_action: form.next_action?.trim() || "" },
    tasks: { title: name, due_at: form.due_at || undefined, status: "Open" },
  };
  return payloads[form.type];
}

export function poundsToMinor(value) {
  if (value === "" || value === null || value === undefined) return 0;
  const amount = Number(value);
  return Number.isFinite(amount) ? Math.round(amount * 100) : 0;
}

const transitions = {
  proposal: { Draft: ["Sent", "Void"], Sent: ["Accepted", "Rejected", "Expired", "Void"] },
  contract: { Draft: ["Sent"], Sent: ["Signed"], Signed: ["Active"], Active: ["Expired", "Terminated"] },
  invoice: { Draft: ["Sent", "Void"], Sent: ["Part-paid", "Paid", "Overdue", "Void"], "Part-paid": ["Paid", "Overdue"] },
};

export function canTransition(type, from, to) {
  return transitions[type]?.[from]?.includes(to) || false;
}

export function requiresConfirmation(action) {
  return ["email.send", "sequence.activate", "invoice.issue", "invoice.void", "payment.refund"].includes(action);
}
