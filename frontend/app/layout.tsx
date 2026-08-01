import Link from "next/link";
import "./styles.css";

export const metadata = { title: "Biologix", description: "Scientific experiment workspace" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header><Link href="/" className="brand"><span className="mark">B</span>Biologix</Link><span className="tagline">Discovery workspace</span></header>
        <main>{children}</main>
      </body>
    </html>
  );
}
