export type Experiment = {
  id: string;
  name: string;
  biologic_target: string;
  polymer_target?: string;
  description?: string;
  status: "queued" | "running" | "done" | "failed";
  parameters: Record<string, unknown>;
  results?: Record<string, unknown>;
  progress: number;
  current_stage?: string;
  progress_log: { timestamp: string; stage: string; detail: string; progress: number }[];
  error_message?: string;
  started_at?: string;
  completed_at?: string;
  job_id?: string;
  created_at: string;
  updated_at: string;
};

export type Artifact = {
  id: string;
  kind: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  created_at: string;
};

export type User = { id: string; email: string; created_at: string; is_admin: boolean };

export type AdminOverview = {
  users: number; experiments: number; queued: number; running: number; done: number; failed: number;
  workers: number; queued_jobs: number; worker_available: boolean; queue_error?: string;
};

export type AdminUser = User & { experiment_count: number };

export type AdminExperiment = Pick<Experiment, "id" | "name" | "biologic_target" | "polymer_target" | "status" | "progress" | "current_stage" | "error_message" | "created_at" | "updated_at"> & { owner_email: string };

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/platform${path}`, {
    ...options,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...options.headers }
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "Something went wrong");
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
