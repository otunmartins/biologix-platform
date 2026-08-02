"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { api, Experiment } from "@/lib/api";

export default function NewExperiment() {
  const router = useRouter(); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); const data = new FormData(event.currentTarget);
    try { const item = await api<Experiment>("/experiments", { method: "POST", body: JSON.stringify(Object.fromEntries(data)) }); router.push(`/experiments/${item.id}`); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to create experiment"); setBusy(false); }
  }
  return <section className="form-card"><Link href="/" className="back">← Experiment history</Link><p className="eyebrow">New experiment</p><h1>Define a discovery campaign</h1><p className="muted">Create an experiment to run the scientific qualification workflow.</p><form onSubmit={submit}><label>Experiment name<input name="name" required placeholder="Insulin stability screen" /></label><div className="grid"><label>Biologic target<input name="biologic_target" required placeholder="Insulin" /></label><label>Polymer target, optional<input name="polymer_target" placeholder="PEG or PSMILES" /></label></div><label>Purpose and notes<textarea name="description" rows={5} placeholder="Describe the goal and key constraints" /></label>{error && <p className="error">{error}</p>}<div className="actions"><Link className="quiet link-button" href="/">Cancel</Link><button disabled={busy}>{busy ? "Creating" : "Create experiment"}</button></div></form></section>;
}
