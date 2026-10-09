// API client for the JalNyay backend (AWS API Gateway HTTP API -> Lambda).
// Base URL comes from the build-time env var VITE_API_URL.

export const API_URL = ((import.meta.env.VITE_API_URL as string | undefined) ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.status = status;
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (!API_URL) throw new ApiError("The site was built without VITE_API_URL, so it cannot reach the API.");
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 25000);
  try {
    const res = await fetch(API_URL + path, {
      method,
      headers: body ? { "content-type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
    });
    const text = await res.text();
    const data = text ? JSON.parse(text) : null;
    if (!res.ok) {
      const msg = data?.error ?? `HTTP ${res.status}`;
      if (res.status === 429) throw new ApiError("Too many requests right now (the demo API is rate-limited). Wait a few seconds and retry.", 429);
      throw new ApiError(msg, res.status);
    }
    return data as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if ((e as Error).name === "AbortError") throw new ApiError("The API did not answer within 25 s.");
    throw new ApiError("Network error: could not reach the API. Check your connection and retry.");
  } finally {
    clearTimeout(timer);
  }
}

/** GET with 2 automatic retries on network errors, 429 and 5xx (backoff 1 s, 2 s). */
export async function getJson<T>(path: string): Promise<T> {
  let last: ApiError | undefined;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      return await request<T>("GET", path);
    } catch (e) {
      last = e as ApiError;
      const retryable = last.status === undefined || last.status === 429 || last.status >= 500;
      if (!retryable) break;
      await sleep(1000 * (attempt + 1));
    }
  }
  throw last ?? new ApiError("Request failed");
}

export type JobPhase = "submitting" | "queued" | "running";
export interface JobProgress {
  phase: JobPhase;
  elapsedS: number;
}

export const POLL_MS = 4000;
export const MAX_WAIT_S: Record<string, number> = {
  city_evaluate: 120,
  village_plan: 150,
  village_fleet: 600,
  village_fraud: 60,
};

/** Submit an async job and poll GET /jobs/{id} every 4 s until done, failed, or the max wait. */
export async function runJob<T>(
  type: string,
  params: Record<string, unknown>,
  onProgress?: (p: JobProgress) => void,
): Promise<{ result: T; runtimeS?: number; coldStart?: boolean; elapsedS: number }> {
  const t0 = Date.now();
  const elapsed = () => Math.round((Date.now() - t0) / 1000);
  onProgress?.({ phase: "submitting", elapsedS: 0 });
  const job = await request<{ job_id: string }>("POST", "/jobs", { type, params });
  const maxWait = MAX_WAIT_S[type] ?? 120;
  for (;;) {
    await sleep(POLL_MS);
    let st: { status: string; result?: T; error?: string; runtime_s?: number; cold_start?: boolean };
    try {
      st = await request("GET", `/jobs/${job.job_id}`);
    } catch (e) {
      if (elapsed() > maxWait) throw e;
      continue; // transient polling error: keep waiting
    }
    if (st.status === "done") return { result: st.result as T, runtimeS: st.runtime_s, coldStart: st.cold_start, elapsedS: elapsed() };
    if (st.status === "failed") throw new ApiError(`The job failed on AWS: ${st.error ?? "unknown error"}`);
    onProgress?.({ phase: st.status === "running" ? "running" : "queued", elapsedS: elapsed() });
    if (elapsed() > maxWait)
      throw new ApiError(`No result after ${maxWait} s. The job may still finish in the background; please try again in a minute.`);
  }
}

/** Fire-and-forget warm-up of the city simulator (cold start ~30 s). Once per browser tab. */
export function warmUpCity(): void {
  try {
    if (sessionStorage.getItem("jalnyay-city-warm")) return;
    sessionStorage.setItem("jalnyay-city-warm", "1");
  } catch {
    /* storage blocked: still warm up */
  }
  request("POST", "/jobs", { type: "city_evaluate", params: { warmup: true } }).catch(() => undefined);
}
