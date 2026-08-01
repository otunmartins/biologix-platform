"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, Experiment } from "@/lib/api";

export default function Detail() {
  const { id } = useParams<{ id: string }>(); const router = useRouter(); const [item, setItem] = useState<Experiment | null>(null);
  useEffect(() => {
    let active = true;
    const load = () => api<Experiment>(`/experiments/${id}`).then(value => active && setItem(value)).catch(() => router.replace("/"));
    load();
    const timer = window.setInterval(load, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [id, router]);
  async function retry() { setItem(await api<Experiment>(`/experiments/${id}/retry`, { method: "POST" })); }
  if (!item) return <div className="loading">Loading experiment</div>;
  const results = item.results as { summary?: Record<string, unknown>; validation?: Record<string, unknown>; safety?: { safe?: boolean; warnings?: string[] }; compliance?: { overall_status?: string; approved_name?: string; jurisdictions_matched?: string[]; notes?: string[] } } | undefined;
  return <><Link href="/" className="back">← Experiment history</Link><div className="page-head"><div><p className="eyebrow">Experiment</p><h1>{item.name}</h1></div><span className={`status ${item.status}`}>{item.status}</span></div><section className="detail-grid"><div className="panel"><h2>Overview</h2><dl><dt>Biologic target</dt><dd>{item.biologic_target}</dd><dt>Polymer target</dt><dd>{item.polymer_target || "Not specified"}</dd><dt>Created</dt><dd>{new Date(item.created_at).toLocaleString()}</dd></dl></div><div className="panel"><h2>Purpose and notes</h2><p>{item.description || "No notes were added."}</p></div></section><section className="panel next"><div className="workflow-heading"><h2>Workflow progress</h2><strong>{item.progress}%</strong></div><div className="progress-track"><span style={{ width: `${item.progress}%` }} /></div><p>{item.current_stage ? item.current_stage.replaceAll("_", " ") : "Waiting for a worker"}</p>{item.error_message && <div className="failure"><p>{item.error_message}</p><button onClick={retry}>Retry workflow</button></div>}<ol className="timeline">{item.progress_log.map((event, index) => <li key={`${event.timestamp}-${index}`}><span>{event.progress}%</span><div><strong>{event.stage.replaceAll("_", " ")}</strong><p>{event.detail}</p></div></li>)}</ol></section>{results && <section className="results"><p className="eyebrow">Scientific results</p><h2>Candidate assessment</h2><div className="result-grid"><article><span>Disposition</span><strong>{String(results.summary?.disposition || "Review")}</strong></article><article><span>Structure</span><strong>{results.validation?.valid ? "Valid" : "Review"}</strong></article><article><span>Safety screen</span><strong>{results.safety?.safe ? "Passed" : "Alerts found"}</strong></article><article><span>Compliance</span><strong>{results.compliance?.overall_status || "Unknown"}</strong></article></div><div className="panel result-detail"><h2>Regulatory context</h2><p>{results.compliance?.approved_name || "No approved excipient match found."}</p><p>{results.compliance?.jurisdictions_matched?.join(", ") || "No jurisdiction match"}</p>{results.safety?.warnings?.map(note => <p className="warning" key={note}>{note}</p>)}</div><details><summary>View structured results</summary><pre>{JSON.stringify(results, null, 2)}</pre></details></section>}</>;
}
