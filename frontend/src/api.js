const API_BASE_URL = "http://localhost:8000/api";

export async function fetchResource(resource) {
  const response = await fetch(`${API_BASE_URL}/${resource}`);

  if (!response.ok) {
    throw new Error(`Could not load ${resource}: ${response.status}`);
  }

  return response.json();
}

