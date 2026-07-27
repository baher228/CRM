export function formatMoney(minorUnits = 0, currency = "GBP") {
  const value = Number(minorUnits);
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency,
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(Number.isFinite(value) ? value / 100 : 0);
}

export function formatDate(value, options = {}) {
  if (!value) return "Not set";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not set";
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    ...(options.withTime ? { hour: "2-digit", minute: "2-digit" } : { year: "numeric" }),
    timeZone: "Europe/London",
  }).format(date);
}

export function initials(value = "") {
  return String(value)
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase())
    .join("") || "?";
}

export function titleCase(value = "") {
  return String(value)
    .replace(/[_-]/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function recordName(record = {}, fallback = "Untitled record") {
  return record.name || record.title || record.number || record.subject || record.company_name || record.email || fallback;
}

export function statusTone(status = "") {
  const value = String(status).toLowerCase();
  if (["won", "paid", "active", "healthy", "complete", "accepted", "signed", "qualified"].includes(value)) return "positive";
  if (["lost", "void", "cancelled", "rejected", "overdue", "at risk", "failed", "blocked"].includes(value)) return "danger";
  if (["watch", "part-paid", "snoozed", "pending", "draft", "negotiation"].includes(value)) return "warning";
  return "info";
}

export function compactNumber(value = 0) {
  return new Intl.NumberFormat("en-GB", { notation: "compact", maximumFractionDigits: 1 }).format(Number(value) || 0);
}
