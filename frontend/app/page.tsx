"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, Experiment } from "@/lib/api";

export default function Dashboard() {
  const router = useRouter();
  const [items, setItems] = useState<Experiment[] | null>(null);
  useEffect(() => { api<Experiment[]>("/experiments").then(setItems).catch(() => router.replace("/login")); }, [router]);
  async function logout() { await api("/auth/logout", { method: "POST" }); router.push("/login"); }
  if (!items) return <div className="loading">Loading workspace</div>;
  return <>
    <div className="page-head"><div><p className="eyebrow">Experiment history</p><h1>Your discovery work</h1><p className="muted">Create, review and revisit every scientific experiment.</p></div><div className="actions"><button className="quiet" onClick={logout}>Sign out</button><Link className="button" href="/experiments/new">New experiment</Link></div></div>
    {items.length === 0 ? <section className="empty"><div className="flask">⌬</div><h2>No experiments yet</h2><p>Set up your first discovery campaign and it will appear here.</p><Link className="button" href="/experiments/new">Create experiment</Link></section> :
      <section className="list">{items.map(item => <Link href={`/experiments/${item.id}`} className="experiment" key={item.id}><div><h2>{item.name}</h2><p>{item.biologic_target}{item.polymer_target ? ` · ${item.polymer_target}` : ""}</p></div><div className="meta"><span className={`status ${item.status}`}>{item.status}</span><time>{new Date(item.created_at).toLocaleDateString()}</time></div></Link>)}</section>}
  </>;
}
