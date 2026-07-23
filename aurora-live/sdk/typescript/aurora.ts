export type AuroraClientOptions = {
  token?: string;
  apiKey?: string;
  fetchImpl?: typeof fetch;
};

export class AuroraAPIError extends Error {
  constructor(public status: number, public payload: unknown) {
    super(`AURORA API request failed (${status})`);
  }
}

export class AuroraClient {
  private fetchImpl: typeof fetch;

  constructor(private baseUrl: string, private options: AuroraClientOptions) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    if (!options.token && !options.apiKey) {
      throw new Error("token or apiKey required");
    }
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
    extraHeaders: Record<string, string> = {},
  ): Promise<T> {
    const headers: Record<string, string> = {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...extraHeaders,
    };
    if (this.options.apiKey) {
      headers["X-AURORA-API-KEY"] = this.options.apiKey;
    } else {
      headers.Authorization = `Bearer ${this.options.token}`;
    }
    const response = await this.fetchImpl(this.baseUrl + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) throw new AuroraAPIError(response.status, payload);
    return payload as T;
  }

  async *pages<T>(
    path: string,
    params: Record<string, string | number> = {},
    limit = 100,
  ): AsyncGenerator<T> {
    let cursor = "";
    do {
      const query = new URLSearchParams({
        ...Object.fromEntries(
          Object.entries(params).map(([key, value]) => [key, String(value)]),
        ),
        limit: String(Math.max(1, Math.min(200, limit))),
        ...(cursor ? { cursor } : {}),
      });
      const page = await this.request<{
        data: T[];
        meta: { next_cursor: string | null };
      }>("GET", `${path}?${query}`);
      for (const item of page.data) yield item;
      cursor = page.meta.next_cursor ?? "";
    } while (cursor);
  }

  detections(filters: Record<string, string | number> = {}) {
    return this.pages<Record<string, unknown>>("/api/v1/detections", filters);
  }

  routes(filters: Record<string, string | number> = {}) {
    return this.pages<Record<string, unknown>>("/api/v1/routes", filters);
  }

  forecastCandidates(filters: Record<string, string | number> = {}) {
    return this.pages<Record<string, unknown>>(
      "/api/v1/forecast-candidates",
      filters,
    );
  }

  async search(query: string, limit = 50) {
    const params = new URLSearchParams({ query, limit: String(limit) });
    const response = await this.request<{ data: unknown[] }>(
      "GET",
      `/api/v1/search?${params}`,
    );
    return response.data;
  }

  approveForecast(candidateId: string, rationale: string, key = crypto.randomUUID()) {
    return this.request(
      "POST",
      `/api/v1/forecast-candidates/${encodeURIComponent(candidateId)}/approve`,
      { rationale },
      { "Idempotency-Key": key },
    );
  }

  async mcpCall(tool: string, args: Record<string, unknown> = {}) {
    const response = await this.request<{
      result: { structuredContent: unknown };
      error?: unknown;
    }>("POST", "/mcp", {
      jsonrpc: "2.0",
      id: crypto.randomUUID(),
      method: "tools/call",
      params: { name: tool, arguments: args },
    });
    if (response.error) throw new AuroraAPIError(400, response.error);
    return response.result.structuredContent;
  }
}
