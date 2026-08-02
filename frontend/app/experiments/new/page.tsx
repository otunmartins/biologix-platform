"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { api, Experiment } from "@/lib/api";

const examples = [
  { label: "Insulin and PEG", biologic: "Human insulin", polymer: "PEG" },
  { label: "Antibody and PLGA", biologic: "Adalimumab", polymer: "PLGA" },
  { label: "Enzyme and PVA", biologic: "Lysozyme", polymer: "PVA" },
];

export default function NewExperiment() {
  const router = useRouter();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [biologic, setBiologic] = useState("");
  const [polymer, setPolymer] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    const payload = {
      name: String(data.get("name") || "").trim(),
      biologic_target: biologic.trim(),
      polymer_target: polymer.trim() || null,
      description: String(data.get("description") || "").trim() || null,
      parameters: {},
    };
    try {
      const item = await api<Experiment>("/experiments", { method: "POST", body: JSON.stringify(payload) });
      router.push(`/experiments/${item.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to create experiment");
      setBusy(false);
    }
  }

  function useExample(example: typeof examples[number]) {
    setBiologic(example.biologic);
    setPolymer(example.polymer);
  }

  return <>
    <Link href="/" className="back">← Experiment history</Link>
    <section className="form-card campaign-form">
      <p className="eyebrow">New experiment</p>
      <h1>Define a discovery campaign</h1>
      <p className="muted">Choose a biologic and candidate polymer. The worker will validate the structure, screen safety and compliance, run molecular physics, and prepare results.</p>
      <div className="example-row" aria-label="Example inputs">{examples.map(example => <button type="button" className="example-chip" key={example.label} onClick={() => useExample(example)}>{example.label}</button>)}</div>
      <form onSubmit={submit}>
        <label>Experiment name<span className="field-help">Use a name that will be easy to find in your history.</span><input name="name" required maxLength={160} autoFocus placeholder="Insulin stability screen" /></label>
        <div className="grid">
          <label>Biologic target<span className="field-help">Protein name or PDB identifier</span><input name="biologic_target" value={biologic} onChange={event => setBiologic(event.target.value)} required maxLength={200} placeholder="Human insulin" /></label>
          <label>Polymer target<span className="field-help">Common name or valid PSMILES</span><input name="polymer_target" value={polymer} onChange={event => setPolymer(event.target.value)} maxLength={500} placeholder="PEG or [*]OCC[*]" /></label>
        </div>
        <label>Purpose and notes<span className="field-help">Optional formulation goals, constraints, or context</span><textarea name="description" rows={5} maxLength={2000} placeholder="Evaluate compatibility and aggregation risk at room temperature." /></label>
        <div className="form-note"><strong>What happens next</strong><p>Your experiment enters the background queue immediately. Progress and downloadable results will appear on its experiment page.</p></div>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="actions form-actions"><Link className="quiet link-button" href="/">Cancel</Link><button disabled={busy}>{busy ? "Creating experiment" : "Create experiment"}</button></div>
      </form>
    </section>
  </>;
}
