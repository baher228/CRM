export const formatCurrency = (value) =>
  new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: "GBP",
    maximumFractionDigits: 0,
  }).format(value);

export const formatDate = (value, fallback = "-") => {
  if (!value) {
    return fallback;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return fallback;
  }
  return new Intl.DateTimeFormat("en-GB", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(date);
};

export const formatDateTime = (value, fallback = "-") => {
  if (!value) {
    return fallback;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return fallback;
  }
  return new Intl.DateTimeFormat("en-GB", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
};

export const formatDomain = (url) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};

export const isBadSourceUrl = (url) => {
  try {
    const parsed = new URL(url);
    return parsed.hostname.endsWith("contractsfinder.service.gov.uk")
      && parsed.pathname.toLowerCase().startsWith("/search/");
  } catch {
    return true;
  }
};

export const isKnown = (value) => Boolean(value && String(value).trim() && String(value).trim() !== "Unknown");
export const showValue = (value, fallback = "-") => (isKnown(value) ? value : fallback);
export const firstKnown = (...values) => values.find((value) => isKnown(value));

export const formatDraftEmailBody = (body) => {
  const cleaned = String(body || "").trim();
  if (!cleaned) {
    return "";
  }
  if (/\bArdivia\b/i.test(cleaned)) {
    return cleaned;
  }
  if (/Best,\s*$/i.test(cleaned)) {
    return cleaned.replace(/Best,\s*$/i, "Best,\nArdivia");
  }
  return `${cleaned}\n\nBest,\nArdivia`;
};

export const draftEmailHref = (lead, body) => {
  if (!lead.contact_email || (!lead.draft_email_subject && !body)) {
    return "";
  }
  const subject = encodeURIComponent(lead.draft_email_subject || "Following up on your tender");
  return `mailto:${lead.contact_email}?subject=${subject}&body=${encodeURIComponent(body)}`;
};

export const priorityTone = (label) =>
  ({ Hot: "red", Warm: "green", Watch: "yellow", Low: "blue" })[label] || "blue";

export const availabilityTone = (status) =>
  ({ Available: "green", Unavailable: "red", Unverified: "yellow" })[status] || "yellow";

export function parseDateish(value) {
  const timestamp = Date.parse(value || "");
  return Number.isNaN(timestamp) ? Number.MAX_SAFE_INTEGER : timestamp;
}

export function formatElapsed(value = 0) {
  const seconds = Math.max(0, Math.round(value));
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  if (!minutes) {
    return `${remainder}s`;
  }
  return `${minutes}m ${String(remainder).padStart(2, "0")}s`;
}

export function statusTone(status) {
  if (status === "failed") {
    return "red";
  }

  if (status === "dry_run") {
    return "blue";
  }

  if (status === "upserted") {
    return "green";
  }

  if (["searching", "extracting", "parsing", "syncing"].includes(status)) {
    return "blue";
  }

  return "yellow";
}

export function urgencyColor(score) {
  if (score >= 75) return "var(--urgency-high)";
  if (score >= 45) return "var(--urgency-med)";
  return "var(--urgency-low)";
}
