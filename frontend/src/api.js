const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api";

export async function fetchResource(resource) {
  const response = await fetch(`${API_BASE_URL}/${resource}`);

  if (!response.ok) {
    throw new Error(await getErrorMessage(response, `Could not load ${resource}`));
  }

  return response.json();
}

export async function postResource(resource, payload) {
  const response = await fetch(`${API_BASE_URL}/${resource}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(await getErrorMessage(response, `Could not run ${resource}`));
  }

  return response.json();
}

export async function startDiscoveryJob(payload) {
  return postResource("discovery/jobs", payload);
}

export async function createClient(payload) {
  return postResource("clients", payload);
}

export async function createCalendarItem(payload) {
  return postResource("calendar", payload);
}

export async function fetchDiscoveryJob(jobId) {
  return fetchResource(`discovery/jobs/${jobId}`);
}

export async function fetchDiscoveryPortals(niche = "", region = "") {
  const search = new URLSearchParams();
  if (niche.trim()) {
    search.set("niche", niche.trim());
  }
  if (region.trim()) {
    search.set("region", region.trim());
  }
  const suffix = search.toString() ? `?${search}` : "";
  return fetchResource(`discovery/portals${suffix}`);
}

export async function updateLead(leadId, payload) {
  const response = await fetch(`${API_BASE_URL}/leads/${leadId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(await getErrorMessage(response, "Could not update lead"));
  }

  return response.json();
}

export async function confirmLead(leadId) {
  return postResource(`leads/${leadId}/confirm`, {});
}

export async function rejectLead(leadId) {
  return postResource(`leads/${leadId}/reject`, {});
}

export async function generateBriefing(payload = {}) {
  const response = await fetch(`${API_BASE_URL}/briefing/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `Briefing failed: ${response.status}`);
  }

  return response.json();
}

export async function getLatestBriefing() {
  const response = await fetch(`${API_BASE_URL}/briefing/latest`);
  if (!response.ok) return null;
  return response.json();
}

export async function approveAction(itemIndex, actionText = null) {
  const response = await fetch(`${API_BASE_URL}/briefing/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_index: itemIndex, action_text: actionText }),
  });

  if (!response.ok) {
    throw new Error(`Approve failed: ${response.status}`);
  }

  return response.json();
}

async function getErrorMessage(response, fallback) {
  try {
    const payload = await response.json();
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
    if (Array.isArray(payload.detail)) {
      return payload.detail.map((item) => item.msg).join("; ");
    }
    if (payload.message) {
      return payload.message;
    }
  } catch {
    // Fall through to the HTTP status message.
  }

  return `${fallback}: ${response.status}`;
}
