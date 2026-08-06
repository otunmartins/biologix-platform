"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AdminExperiment, AdminOverview, AdminUser, api } from "@/lib/api";

export default function AdminDashboard() {
  const router = useRouter();
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [experiments, setExperiments] = useState<AdminExperiment[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const [nextOverview, nextExperiments, nextUsers] = await Promise.all([
          api<AdminOverview>("/admin/overview"),
          api<AdminExperiment[]>("/admin/experiments"),
          api<AdminUser[]>("/admin/users"),
        ]);
        if (active) { setOverview(nextOverview); setExperiments(nextExperiments); setUsers(nextUsers); setError(""); }
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "Unable to load administration data");
      }
    };
    load();
    const timer = window.setInterval(load, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  if (error && !overview) return <section className="empty"><h1>Admin access unavailable</h1><p>{error}</p><button onClick={() => router.push("/")}>Back to workspace</button></section>;
  if (!overview) return <div className="loading" role="status"><span className="spinner" />Loading operations</div>;

  const cards: [string, number][] = [["Users", overview.users], ["Experiments", overview.experiments], ["Running", overview.running], ["Queued", overview.queued_jobs], ["Completed", overview.done], ["Failed", overview.failed]];
  return <>
    <div className="page-head"><div><p className="eyebrow">Administration</p><h1>Platform operations</h1><p className="muted">Live database records and scientific worker health.</p></div><div className="actions"><Link className="quiet button-link" href="/">Workspace</Link></div></div>
    <section className="admin-stats">{cards.map(([label, value]) => <article className="admin-stat" key={label}><span>{label}</span><strong>{value}</strong></article>)}</section>
    <section className={`worker-banner ${overview.worker_available ? "healthy" : "unhealthy"}`}><strong>{overview.worker_available ? `${overview.workers} worker${overview.workers === 1 ? "" : "s"} online` : "No scientific worker online"}</strong><span>{overview.queue_error || `${overview.queued_jobs} jobs waiting`}</span></section>
    <section className="admin-section"><div className="section-title"><h2>Recent experiments</h2><span>{experiments.length} shown</span></div><div className="table-wrap"><table><thead><tr><th>Experiment</th><th>Owner</th><th>Status</th><th>Stage</th><th>Updated</th></tr></thead><tbody>{experiments.map(item => <tr key={item.id}><td><Link href={`/experiments/${item.id}`}>{item.name}</Link><small>{item.biologic_target}{item.polymer_target ? ` · ${item.polymer_target}` : ""}</small>{item.error_message && <small className="error-text">{item.error_message}</small>}</td><td>{item.owner_email}</td><td><span className={`status ${item.status}`}>{item.status} {item.progress}%</span></td><td>{item.current_stage || "—"}</td><td>{new Date(item.updated_at).toLocaleString()}</td></tr>)}</tbody></table></div></section>
    <section className="admin-section"><div className="section-title"><h2>Users</h2><span>{users.length} shown</span></div><div className="table-wrap"><table><thead><tr><th>Email</th><th>Experiments</th><th>Joined</th></tr></thead><tbody>{users.map(user => <tr key={user.id}><td>{user.email}</td><td>{user.experiment_count}</td><td>{new Date(user.created_at).toLocaleString()}</td></tr>)}</tbody></table></div></section>
  </>;
}
