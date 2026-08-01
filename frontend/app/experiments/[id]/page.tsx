"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, Experiment } from "@/lib/api";

export default function Detail() {
  const { id } = useParams<{ id: string }>(); const router = useRouter(); const [item, setItem] = useState<Experiment | null>(null);
  useEffect(() => { api<Experiment>(`/experiments/${id}`).then(setItem).catch(() => router.replace("/")); }, [id, router]);
  if (!item) return <div className="loading">Loading experiment</div>;
  return <><Link href="/" className="back">← Experiment history</Link><div className="page-head"><div><p className="eyebrow">Experiment</p><h1>{item.name}</h1></div><span className={`status ${item.status}`}>{item.status}</span></div><section className="detail-grid"><div className="panel"><h2>Overview</h2><dl><dt>Biologic target</dt><dd>{item.biologic_target}</dd><dt>Polymer target</dt><dd>{item.polymer_target || "Not specified"}</dd><dt>Created</dt><dd>{new Date(item.created_at).toLocaleString()}</dd></dl></div><div className="panel"><h2>Purpose and notes</h2><p>{item.description || "No notes were added."}</p></div></section><section className="panel next"><h2>Workflow status</h2><p>This experiment is saved and ready. Scientific execution and live progress are part of the next milestone.</p></section></>;
}
