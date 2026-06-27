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

export async function fetchDiscoveryJob(jobId) {
  return fetchResource(`discovery/jobs/${jobId}`);
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
