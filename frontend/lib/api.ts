export type Experiment = {
  id: string;
  name: string;
  biologic_target: string;
  polymer_target?: string;
  description?: string;
  status: "queued" | "running" | "done" | "failed";
  parameters: Record<string, unknown>;
  results?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/platform${path}`, {
    ...options,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "Something went wrong");
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
