import "./styles.css";
export const metadata = { title: "Trade Investing Panel", description: "Paper-only operator dashboard" };
export default function Layout({ children }: Readonly<{children: React.ReactNode}>) { return <html lang="en"><body>{children}</body></html>; }
