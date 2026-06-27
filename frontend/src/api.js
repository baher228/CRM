const API_BASE_URL = "http://localhost:8000/api";

export async function fetchResource(resource) {
  const response = await fetch(`${API_BASE_URL}/${resource}`);

  if (!response.ok) {
    throw new Error(`Could not load ${resource}: ${response.status}`);
  }

  return response.json();
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
