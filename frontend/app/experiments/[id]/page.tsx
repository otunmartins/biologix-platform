"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, Artifact, Experiment } from "@/lib/api";

type Results = {
  summary?: Record<string, unknown>;
  validation?: Record<string, unknown>;
  safety?: { safe?: boolean; warnings?: string[] };
  compliance?: { overall_status?: string; approved_name?: string; jurisdictions_matched?: string[] };
  retrosynthesis?: { status?: string; reason?: string; result?: { routes?: unknown[] } };
  physics?: { status?: string; reason?: string; result?: Record<string, unknown> };
  capabilities?: Record<string, unknown>;
};

export default function Detail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [item, setItem] = useState<Experiment | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);

  useEffect(() => {
    let active = true;
    const load = () => api<Experiment>(`/experiments/${id}`).then(value => {
      if (active) setItem(value);
      if (value.status === "done") api<Artifact[]>(`/experiments/${id}/artifacts`).then(files => active && setArtifacts(files));
    }).catch(() => router.replace("/"));
    load();
    const timer = window.setInterval(load, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [id, router]);

  async function retry() { setItem(await api<Experiment>(`/experiments/${id}/retry`, { method: "POST" })); }
  if (!item) return <div className="loading">Loading experiment</div>;

  const results = item.results as Results | undefined;
  const routeCount = results?.retrosynthesis?.result?.routes?.length;
  const capabilities = results?.capabilities
    ? Object.entries(results.capabilities).filter(([, value]) => value === true).map(([name]) => name.replaceAll("_", " "))
    : [];

  return <>
    <Link href="/" className="back">← Experiment history</Link>
    <div className="page-head"><div><p className="eyebrow">Experiment</p><h1>{item.name}</h1></div><span className={`status ${item.status}`}>{item.status}</span></div>
    <section className="detail-grid">
      <div className="panel"><h2>Overview</h2><dl><dt>Biologic target</dt><dd>{item.biologic_target}</dd><dt>Polymer target</dt><dd>{item.polymer_target || "Not specified"}</dd><dt>Created</dt><dd>{new Date(item.created_at).toLocaleString()}</dd></dl></div>
      <div className="panel"><h2>Purpose and notes</h2><p>{item.description || "No notes were added."}</p></div>
    </section>
    <section className="panel next">
      <div className="workflow-heading"><h2>Workflow progress</h2><strong>{item.progress}%</strong></div>
      <div className="progress-track"><span style={{ width: `${item.progress}%` }} /></div>
      <p>{item.current_stage ? item.current_stage.replaceAll("_", " ") : "Waiting for a worker"}</p>
      {item.error_message && <div className="failure"><p>{item.error_message}</p><button onClick={retry}>Retry workflow</button></div>}
      <ol className="timeline">{item.progress_log.map((event, index) => <li key={`${event.timestamp}-${index}`}><span>{event.progress}%</span><div><strong>{event.stage.replaceAll("_", " ")}</strong><p>{event.detail}</p></div></li>)}</ol>
    </section>
    {results && <section className="results">
      <p className="eyebrow">Scientific results</p><h2>Candidate assessment</h2>
      <div className="result-grid"><article><span>Disposition</span><strong>{String(results.summary?.disposition || "Review")}</strong></article><article><span>Structure</span><strong>{results.validation?.valid ? "Valid" : "Review"}</strong></article><article><span>Safety screen</span><strong>{results.safety?.safe ? "Passed" : "Alerts found"}</strong></article><article><span>Compliance</span><strong>{results.compliance?.overall_status || "Unknown"}</strong></article></div>
      <div className="science-grid">
        <div className="panel result-detail"><h2>Regulatory context</h2><p>{results.compliance?.approved_name || "No approved excipient match found."}</p><p>{results.compliance?.jurisdictions_matched?.join(", ") || "No jurisdiction match"}</p>{results.safety?.warnings?.map(note => <p className="warning" key={note}>{note}</p>)}</div>
        <div className="panel result-detail"><h2>Retrosynthesis</h2><p className="result-status">{results.retrosynthesis?.status || "Not run"}</p><p>{routeCount !== undefined ? `${routeCount} candidate routes returned` : results.retrosynthesis?.reason || "No route summary available"}</p></div>
        <div className="panel result-detail"><h2>Molecular physics</h2><p className="result-status">{results.physics?.status || "Not run"}</p><p>{results.physics?.reason || (results.physics?.result ? "Simulation results are included in the report" : "No simulation summary available")}</p></div>
        <div className="panel result-detail"><h2>Scientific runtime</h2><p>{capabilities.length ? capabilities.join(", ") : "Core validation runtime"}</p></div>
      </div>
      {artifacts.length > 0 && <div className="panel artifacts"><h2>Downloads</h2><div className="artifact-list">{artifacts.map(file => <a className="artifact" href={`/api/platform/experiments/${id}/artifacts/${file.id}`} key={file.id}><span><strong>{file.filename}</strong><small>{file.kind} · {Math.max(1, Math.round(file.size_bytes / 1024))} KB</small></span><span>Download</span></a>)}</div></div>}
      <details className="technical"><summary>Technical result data</summary><pre>{JSON.stringify(results, null, 2)}</pre></details>
    </section>}
  </>;
}
