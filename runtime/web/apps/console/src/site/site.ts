// Whether this console runs on a site (a server with its own hostnames), by
// the name the API reports. Null means the usual VM on the user's computer.
export async function fetchSite(fetchImpl: typeof fetch): Promise<string | null> {
  try {
    const response = await fetchImpl("/api/health");
    if (!response.ok) return null;
    const body = (await response.json()) as { site?: unknown } | null;
    const site = body?.site;
    return typeof site === "string" && site !== "" ? site : null;
  } catch {
    return null;
  }
}
