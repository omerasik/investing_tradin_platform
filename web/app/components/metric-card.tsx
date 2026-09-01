import Link from "next/link";

export function MetricCard({
  category,
  status,
  description,
  href,
  linkText,
}: {
  category: string;
  status: string;
  description: string;
  href: string;
  linkText: string;
}) {
  return (
    <article className="card">
      <p>{category}</p>
      <strong>{status}</strong>
      <small>{description}</small>
      <Link href={href}>{linkText} &rarr;</Link>
    </article>
  );
}
