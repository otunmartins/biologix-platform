"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { api } from "@/lib/api";

export default function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    try {
      await api(`/auth/${mode}`, { method: "POST", body: JSON.stringify({ email: data.get("email"), password: data.get("password") }) });
      router.push("/");
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to continue");
    } finally { setBusy(false); }
  }
  const signup = mode === "signup";
  return <section className="auth-card">
    <p className="eyebrow">Secure workspace</p>
    <h1>{signup ? "Create your account" : "Welcome back"}</h1>
    <p className="muted">{signup ? "Start organizing your scientific discovery work." : "Sign in to continue to your experiments."}</p>
    <form onSubmit={submit}>
      <label>Email<input name="email" type="email" required autoComplete="email" placeholder="researcher@company.com" /></label>
      <label>Password<input name="password" type="password" required minLength={signup ? 8 : undefined} autoComplete={signup ? "new-password" : "current-password"} /></label>
      {error && <p className="error">{error}</p>}
      <button disabled={busy}>{busy ? "Please wait" : signup ? "Create account" : "Sign in"}</button>
    </form>
    <p className="switch">{signup ? "Already have an account?" : "New to Biologix?"} <Link href={signup ? "/login" : "/signup"}>{signup ? "Sign in" : "Create an account"}</Link></p>
  </section>;
}
