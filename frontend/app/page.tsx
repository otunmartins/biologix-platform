"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { api, Experiment, User } from "@/lib/api";

type StatusFilter = "all" | Experiment["status"];

export default function Dashboard() {
  const router = useRouter();
  const [items, setItems] = useState<Experiment[] | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [me, setMe] = useState<User | null>(null);

  useEffect(() => {
    let active = true;
    const load = () => api<Experiment[]>("/experiments")
      .then(value => active && setItems(value))
      .catch(() => router.replace("/login"));
    load();
    api<User>("/auth/me").then(value => active && setMe(value)).catch(() => undefined);
    const timer = window.setInterval(load, 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [router]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return (items || []).filter(item => {
      const matchesStatus = status === "all" || item.status === status;
      const text = `${item.name} ${item.biologic_target} ${item.polymer_target || ""}`.toLowerCase();
      return matchesStatus && (!normalized || text.includes(normalized));
    });
  }, [items, query, status]);

  async function logout() {
    await api("/auth/logout", { method: "POST" });
    router.push("/login");
  }

  if (!items) return <div className="loading" role="status"><span className="spinner" />Loading workspace</div>;

  return <>
    <div className="page-head">
      <div><p className="eyebrow">Experiment history</p><h1>Your discovery work</h1><p className="muted">Create, review and revisit every scientific experiment.</p></div>
      <div className="actions">{me?.is_admin && <Link className="quiet button-link" href="/admin">Admin</Link>}<button className="quiet" onClick={logout}>Sign out</button><Link className="button" href="/experiments/new">New experiment</Link></div>
    </div>
    {items.length === 0 ? <section className="empty"><div className="flask" aria-hidden="true">⌬</div><h2>No experiments yet</h2><p>Set up your first discovery campaign and it will appear here.</p><Link className="button" href="/experiments/new">Create experiment</Link></section> : <>
      <section className="workspace-tools" aria-label="Experiment filters">
        <label className="search-field"><span className="sr-only">Search experiments</span><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search experiments or targets" /></label>
        <div className="filter-tabs" role="group" aria-label="Filter by status">
          {(["all", "queued", "running", "done", "failed"] as StatusFilter[]).map(value => <button key={value} className={status === value ? "active" : ""} onClick={() => setStatus(value)} aria-pressed={status === value}>{value}</button>)}
        </div>
        <span className="result-count">{filtered.length} of {items.length}</span>
      </section>
      {filtered.length ? <section className="list" aria-label="Experiments">{filtered.map(item => <Link href={`/experiments/${item.id}`} className="experiment" key={item.id}>
        <div className="experiment-main"><div className="experiment-icon" aria-hidden="true">{item.biologic_target.charAt(0).toUpperCase()}</div><div><h2>{item.name}</h2><p>{item.biologic_target}{item.polymer_target ? ` · ${item.polymer_target}` : ""}</p>{item.status === "running" && <div className="mini-progress"><span style={{ width: `${item.progress}%` }} /></div>}</div></div>
        <div className="meta"><span className={`status ${item.status}`}>{item.status}</span><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}</time><span className="row-arrow" aria-hidden="true">→</span></div>
      </Link>)}</section> : <section className="no-results"><h2>No matching experiments</h2><p>Try a different search or status filter.</p><button className="quiet" onClick={() => { setQuery(""); setStatus("all"); }}>Clear filters</button></section>}
    </>}
  </>;
}
