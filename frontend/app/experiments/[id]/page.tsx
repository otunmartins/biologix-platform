"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import EvidenceEditor from "@/components/EvidenceEditor";
import {
  api,
  Artifact,
  emptySource,
  EvidenceSource,
  Experiment,
  Preflight,
  usableSources,
} from "@/lib/api";

type StageVerdict = { stage: string; status: "complete" | "degraded" | "skipped" | "failed"; reasons: string[] };
type Completeness = { verdict: "complete" | "incomplete"; stages: StageVerdict[]; blocking: string[]; degraded: string[] };

const STAGE_LABEL: Record<StageVerdict["status"], string> = {
  complete: "Complete",
  degraded: "Degraded",
  skipped: "Not run",
  failed: "Failed",
};

const PROVENANCE_LABEL: Record<string, string> = {
  llm_verified: "resolved and checked",
  user_supplied: "as you entered it",
  offline_cache: "offline table (not checked)",
  rcsb_text_search: "text search hit (unconfirmed)",
};

const EVIDENCE_LABEL: Record<string, string> = {
  llm_literature_evidence: "route planned from literature, graph-checked",
  user_supplied: "your synthesis evidence",
  offline_curated_route: "offline curated route",
};

const VERDICT_HEADLINE: Record<Completeness["verdict"], string> = {
  complete: "Every scientific stage produced a result.",
  incomplete: "This run did not produce a complete scientific result.",
};

type Results = {
  completeness?: Completeness;
  summary?: Record<string, unknown>;
  validation?: Record<string, unknown>;
  safety?: { safe?: boolean; warnings?: string[] };
  compliance?: { overall_status?: string; approved_name?: string; jurisdictions_matched?: string[] };
  monomer_safety?: Array<{ name?: string; smiles: string; safe?: boolean; warnings?: string[] }>;
  structure?: { psmiles?: string; material_name?: string; provenance?: string; confidence?: string; model?: string; notes?: string; fallback_reason?: string };
  biologic?: { pdb_id?: string; provenance?: string; canonical_name?: string; rationale?: string; fallback_reason?: string; entry?: { title?: string; method?: string; resolution_a?: number } };
  retrosynthesis?: { status?: string; reason?: string; evidence_source?: string; planning?: { model?: string; error?: string; sources?: Array<{ name: string }> }; result?: {
    polymer_routes?: Array<{
      target_polymer: string;
      polymerization_type?: string;
      steps?: Array<{ reactant_names?: string[]; product_name?: string; reaction_type?: string; conditions?: string }>;
      monomers?: Array<{ name?: string; smiles: string; source?: string }>;
    }>;
    warnings?: string[];
    metadata?: { route_provenance?: string; reporting_honesty?: string };
  } };
  physics?: { status?: string; reason?: string; sampling?: { npt_enabled?: boolean; duration_ps?: number; frames_averaged?: number; expected_frames?: number; truncated_by_wall_clock?: boolean; wall_clock_limit_s?: number }; result?: { results?: {
    md_results_raw?: Array<{ interaction_energy_kj_mol?: number; interaction_energy_kj_mol_std?: number; n_frames_averaged?: number; method?: string } | null>;
    evaluation_progress?: Array<{ status?: string; reason?: string }>;
  } | Array<{ interaction_energy_kj_mol?: number; interaction_energy_kj_mol_std?: number; n_frames_averaged?: number; method?: string }> } };
  capabilities?: Record<string, unknown>;
};

export default function Detail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [item, setItem] = useState<Experiment | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [retrying, setRetrying] = useState(false);
  const [editing, setEditing] = useState(false);
  const [sources, setSources] = useState<EvidenceSource[]>([emptySource()]);
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [retryError, setRetryError] = useState("");

  useEffect(() => {
    let active = true;
    const load = () => api<Experiment>(`/experiments/${id}`).then(value => {
      if (active) setItem(value);
      // A failed run still produces results, audit and report - that report is
      // exactly what explains the failure, so it must stay reachable.
      if (value.status === "done" || value.status === "failed") {
        api<Artifact[]>(`/experiments/${id}/artifacts`).then(files => active && setArtifacts(files)).catch(() => undefined);
      }
    }).catch(() => router.replace("/"));
    load();
    const timer = window.setInterval(load, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [id, router]);

  async function retry(parameters?: Record<string, unknown>) {
    setRetrying(true);
    setRetryError("");
    try {
      const body = parameters ? JSON.stringify({ parameters }) : undefined;
      setItem(await api<Experiment>(`/experiments/${id}/retry`, { method: "POST", body }));
      setEditing(false);
    } catch (reason) {
      setRetryError(reason instanceof Error ? reason.message : "Unable to re-run the experiment");
    } finally { setRetrying(false); }
  }

  function openEvidenceEditor(existing: Experiment) {
    const saved = existing.parameters?.retrosynthesis_sources as EvidenceSource[] | undefined;
    setSources(saved?.length ? saved : [emptySource()]);
    setPreflight(null);
    setRetryError("");
    setEditing(true);
  }
  if (!item) return <div className="loading" role="status"><span className="spinner" />Loading experiment</div>;

  const results = item.results as Results | undefined;
  const routeCount = results?.retrosynthesis?.result?.polymer_routes?.length;
  const routes = results?.retrosynthesis?.result?.polymer_routes || [];
  const retrosynthesisNote = results?.retrosynthesis?.reason || results?.retrosynthesis?.result?.warnings?.[0];
  const rawPhysics = results?.physics?.result?.results;
  const physicsResult = Array.isArray(rawPhysics)
    ? rawPhysics[0]
    : rawPhysics?.md_results_raw?.find(value => value != null) || undefined;
  const physicsFailure = !Array.isArray(rawPhysics)
    ? rawPhysics?.evaluation_progress?.find(value => value.status === "failed")
    : undefined;
  const energy = physicsResult?.interaction_energy_kj_mol;
  const sampling = results?.physics?.sampling;
  const completeness = results?.completeness;
  const stageOf = (name: string) => completeness?.stages.find(entry => entry.stage === name);
  const retroVerdict = stageOf("retrosynthesis");
  const physicsVerdict = stageOf("physics");
  const retroStatus = results?.retrosynthesis?.status;
  const needsEvidence = ["requires_input", "provisional", "no_routes", "failed"].includes(retroStatus || "");
  const settled = item.status === "done" || item.status === "failed";
  const materialName = String(item.parameters?.retrosynthesis_material_name || item.polymer_target || "");
  const readySources = usableSources(sources);
  const evidenceBlocked = readySources.length === 0 || (preflight !== null && !preflight.root_product_found);
  const capabilities = results?.capabilities
    ? Object.entries(results.capabilities).filter(([, value]) => value === true).map(([name]) => name.replaceAll("_", " "))
    : [];

  return <>
    <Link href="/" className="back">← Experiment history</Link>
    <div className="page-head experiment-head"><div><p className="eyebrow">Experiment</p><h1>{item.name}</h1><p className="muted experiment-id">Experiment {item.id.slice(0, 8)}</p></div><span className={`status status-large ${item.status}`}>{item.status}</span></div>
    <section className="detail-grid">
      <div className="panel"><h2>Overview</h2><dl><dt>Biologic target</dt><dd>{item.biologic_target}</dd><dt>Polymer target</dt><dd>{item.polymer_target || "Not specified"}</dd><dt>Created</dt><dd><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time></dd>{item.completed_at && <><dt>Completed</dt><dd><time dateTime={item.completed_at}>{new Date(item.completed_at).toLocaleString()}</time></dd></>}</dl></div>
      <div className="panel"><h2>Purpose and notes</h2><p>{item.description || "No notes were added."}</p></div>
    </section>
    <section className="panel next">
      <div className="workflow-heading"><h2>Workflow progress</h2><strong>{item.progress}%</strong></div>
      <div className="progress-track" role="progressbar" aria-label="Workflow progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={item.progress}><span style={{ width: `${item.progress}%` }} /></div>
      <p className="current-stage">{item.current_stage ? item.current_stage.replaceAll("_", " ") : "Waiting for a worker"}{item.status === "running" && <span className="live-dot">Live</span>}</p>
      {item.error_message && <div className="failure" role="alert"><p>{item.error_message}</p><button onClick={() => retry()} disabled={retrying}>{retrying ? "Retrying" : "Retry workflow"}</button></div>}
      <ol className="timeline">{item.progress_log.map((event, index) => <li key={`${event.timestamp}-${index}`}><span>{event.progress}%</span><div><strong>{event.stage.replaceAll("_", " ")}</strong><p>{event.detail}</p></div></li>)}</ol>
    </section>
    {results && <section className="results">
      <p className="eyebrow">Scientific results</p><h2>Candidate assessment</h2>
      {completeness && <div className={`completeness ${completeness.verdict}`}>
        <div className="completeness-head">
          <div><p className="eyebrow">Run completeness</p><h2>{VERDICT_HEADLINE[completeness.verdict]}</h2></div>
          <span className={`status status-large ${completeness.verdict === "complete" ? "done" : "failed"}`}>{completeness.verdict}</span>
        </div>
        <ul className="stage-list">
          {completeness.stages.map(stage => <li key={stage.stage} className={stage.status}>
            <div className="stage-row">
              <strong>{stage.stage.replaceAll("_", " ")}</strong>
              <span className={`stage-tag ${stage.status}`}>{STAGE_LABEL[stage.status]}</span>
            </div>
            {stage.reasons.length > 0 && <ul className="stage-reasons">{stage.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>}
          </li>)}
        </ul>
      </div>}
      {(results.structure?.provenance || results.biologic?.provenance) && <p className="provenance">
        {results.structure?.provenance && <span className={`provenance-chip${results.structure.provenance === "offline_cache" ? " fallback" : ""}`}>Repeat unit: {PROVENANCE_LABEL[results.structure.provenance] || results.structure.provenance.replaceAll("_", " ")}</span>}
        {results.biologic?.provenance && <span className={`provenance-chip${results.biologic.provenance === "llm_verified" ? "" : " fallback"}`}>Structure {results.biologic.pdb_id}: {PROVENANCE_LABEL[results.biologic.provenance] || results.biologic.provenance.replaceAll("_", " ")}</span>}
      </p>}
      {results.biologic?.entry?.title && <p className="muted">{results.biologic.entry.title}</p>}
      <div className="result-grid"><article><span>Disposition</span><strong>{String(results.summary?.disposition || "Review")}</strong></article><article><span>Structure</span><strong>{results.validation?.valid ? "Valid" : "Review"}</strong></article><article><span>Safety screen</span><strong>{results.safety?.safe ? "Passed" : "Alerts found"}</strong></article><article><span>Compliance</span><strong>{results.compliance?.overall_status || "Unknown"}</strong></article></div>
      <div className="science-grid">
        <div className="panel result-detail"><h2>Regulatory context</h2><p>{results.compliance?.approved_name || "No approved excipient match found."}</p><p>{results.compliance?.jurisdictions_matched?.join(", ") || "No jurisdiction match"}</p>{results.safety?.warnings?.map(note => <p className="warning" key={note}>{note}</p>)}</div>
        <div className="panel result-detail"><div className="result-title"><h2>Retrosynthesis</h2><span className="science-icon">R</span></div><p className="result-status">{retroVerdict ? STAGE_LABEL[retroVerdict.status] : results.retrosynthesis?.status?.replaceAll("_", " ") || "Not run"}</p>{retroVerdict && retroVerdict.status !== "complete" ? retroVerdict.reasons.map((reason, index) => <p className="warning" key={index}>{reason}</p>) : <p>{routeCount ? `${routeCount} candidate route${routeCount === 1 ? "" : "s"} returned` : retrosynthesisNote || "No route summary available"}</p>}{results.retrosynthesis?.status === "provisional" && <p className="warning">Offline curated route only. Nothing was planned or checked for this run, so it is not counted as verified.</p>}{results.retrosynthesis?.evidence_source && <p className="provenance"><span className={`provenance-chip${results.retrosynthesis.evidence_source === "offline_curated_route" ? " fallback" : ""}`}>{EVIDENCE_LABEL[results.retrosynthesis.evidence_source] || results.retrosynthesis.evidence_source.replaceAll("_", " ")}</span>{results.retrosynthesis.planning?.model && <span className="provenance-chip">{results.retrosynthesis.planning.model}</span>}</p>}{results.retrosynthesis?.planning?.error && <p className="warning">{results.retrosynthesis.planning.error}</p>}{(results.retrosynthesis?.planning?.sources?.length || 0) > 0 && <ul className="citation-list">{results.retrosynthesis!.planning!.sources!.map((source, index) => <li key={index}>{source.name}</li>)}</ul>}{results.retrosynthesis?.status === "requires_input" && <p className="input-note">No route could be established that terminates in purchasable chemicals. Add a reviewed reaction source and retry.</p>}</div>
        <div className="panel result-detail"><div className="result-title"><h2>Molecular physics</h2><span className="science-icon">M</span></div><p className="result-status">{physicsVerdict ? STAGE_LABEL[physicsVerdict.status] : results.physics?.status || "Not run"}</p>{physicsVerdict?.status && physicsVerdict.status !== "complete" && physicsVerdict.reasons.map((reason, index) => <p className="warning" key={index}>{reason}</p>)}{typeof energy === "number" ? <p><strong className="metric">{energy.toFixed(1)}</strong>{typeof physicsResult?.interaction_energy_kj_mol_std === "number" ? ` ± ${physicsResult.interaction_energy_kj_mol_std.toFixed(1)}` : ""} kJ/mol interaction energy</p> : <p>{physicsFailure?.reason || results.physics?.reason || (results.physics?.result ? "No successful simulation result" : "No simulation summary available")}</p>}{sampling && <p className="method">{sampling.npt_enabled ? `${sampling.duration_ps} ps constant-pressure trajectory, ${sampling.frames_averaged ?? 0} of ${sampling.expected_frames ?? 0} frames averaged` : "Single minimised pose, no sampling"}</p>}{physicsResult?.method && <p className="method">{physicsResult.method}</p>}</div>
        <div className="panel result-detail"><h2>Scientific runtime</h2><p>{capabilities.length ? capabilities.join(", ") : "Core validation runtime"}</p></div>
      </div>
      {settled && (needsEvidence || editing) && <div className="panel evidence-panel">
        <div className="result-title"><div><p className="eyebrow">Retrosynthesis input</p><h2>{editing ? "Add synthesis evidence" : "This route is not verified"}</h2></div></div>
        {!editing && <>
          <p>{retroStatus === "provisional"
            ? "The engine fell back to a curated template rather than a route derived from literature. Supply reactions from sources you trust to produce a verified route."
            : "The engine had no literature reactions to build a route from. Supply reactions from sources you trust and re-run this experiment."}</p>
          <button onClick={() => openEvidenceEditor(item)}>Add synthesis evidence</button>
        </>}
        {editing && <>
          <EvidenceEditor materialName={materialName} sources={sources} onChange={setSources} onPreflight={setPreflight} />
          {retryError && <p className="error" role="alert">{retryError}</p>}
          <div className="actions form-actions">
            <button className="quiet" onClick={() => setEditing(false)} disabled={retrying}>Cancel</button>
            <button
              onClick={() => retry({ retrosynthesis_sources: readySources })}
              disabled={retrying || evidenceBlocked}
            >{retrying ? "Re-running" : "Save evidence and re-run"}</button>
          </div>
        </>}
      </div>}
      {routes.length > 0 && <div className="panel route-panel"><div className="result-title"><div><p className="eyebrow">Synthesis plan</p><h2>Polymer route details</h2></div><span className="result-status">{results.retrosynthesis?.result?.metadata?.route_provenance?.replaceAll("_", " ")}</span></div>
        {results.retrosynthesis?.result?.metadata?.reporting_honesty && <p className="muted">{results.retrosynthesis.result.metadata.reporting_honesty}</p>}
        {routes.map((route, routeIndex) => <article className="route" key={`${route.target_polymer}-${routeIndex}`}><h3>Route {routeIndex + 1}: {route.target_polymer}</h3><p className="method">{route.polymerization_type?.replaceAll("_", " ")}</p>
          {!route.steps?.length && <p className="warning">This route contains no reaction steps, so it is not a usable synthesis plan.</p>}<ol className="route-steps">{route.steps?.map((step, stepIndex) => <li key={`${step.product_name}-${stepIndex}`}><strong>{step.reactant_names?.join(" + ")} → {step.product_name}</strong><p>{step.reaction_type || "Polymer synthesis step"}</p>{step.conditions && <p className="muted">Conditions: {step.conditions}</p>}</li>)}</ol>
          <div className="monomer-list">{route.monomers?.map(monomer => <span key={`${monomer.name}-${monomer.smiles}`}><strong>{monomer.name || monomer.smiles}</strong><small>{monomer.smiles} · {monomer.source?.replaceAll("_", " ")}</small></span>)}</div>
        </article>)}
        {!!results.monomer_safety?.length && <div className="monomer-safety"><h3>Residual monomer safety screen</h3>{results.monomer_safety.map((monomer, index) => <p key={`${monomer.smiles}-${index}`} className={monomer.safe ? "" : "warning"}><strong>{monomer.name || monomer.smiles}:</strong> {monomer.safe ? "No structural or predicted ADMET alerts" : monomer.warnings?.join(", ") || "Review required"}</p>)}</div>}
      </div>}
      {artifacts.length > 0 && <div className="panel artifacts"><div><h2>Downloads</h2><p>Reports, structured data, and the experiment audit trail.</p></div><div className="artifact-list">{artifacts.map(file => <a className="artifact" href={`/api/platform/experiments/${id}/artifacts/${file.id}`} key={file.id}><span><strong>{file.filename}</strong><small>{file.kind} · {Math.max(1, Math.round(file.size_bytes / 1024))} KB</small></span><span className="download-label">Download ↓</span></a>)}</div></div>}
      <details className="technical"><summary>Technical result data</summary><pre>{JSON.stringify(results, null, 2)}</pre></details>
    </section>}
  </>;
}
