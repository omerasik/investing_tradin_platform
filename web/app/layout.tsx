import "./styles.css";
import "./evidence-tables.css";
export const metadata = { title: "Trade Investing Panel", description: "Paper-only operator dashboard" };
export default function Layout({ children }: Readonly<{children: React.ReactNode}>) { return <html lang="en"><body><a className="skip-link" href="#main-content">Skip to operator evidence</a>{children}</body></html>; }
