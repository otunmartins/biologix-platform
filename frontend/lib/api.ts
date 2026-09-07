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

export type EvidenceReaction = { reactants: string; products: string; conditions: string };
export type EvidenceSource = { name: string; reactions: EvidenceReaction[] };

export type Preflight = {
  root_product_found: boolean;
  tree_root: string;
  paper_count: number;
  warnings: string[];
  blocking_reactants: string[];
  leaf_reachability: Record<string, { purchasable: boolean; resolution_source: string; blocking: boolean }>;
};

export const emptyReaction = (): EvidenceReaction => ({ reactants: "", products: "", conditions: "" });
export const emptySource = (): EvidenceSource => ({ name: "", reactions: [emptyReaction()] });

export function usableSources(sources: EvidenceSource[]): EvidenceSource[] {
  return sources
    .map(source => ({
      name: source.name.trim(),
      reactions: source.reactions.filter(reaction => reaction.reactants.trim() && reaction.products.trim()),
    }))
    .filter(source => source.name && source.reactions.length > 0);
}

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
