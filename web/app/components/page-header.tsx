import { utc } from "../lib/data-access";

export function PageHeader({
  eyebrow = "PRIVATE READ-ONLY OPERATOR WORKSPACE",
  title,
  asOfTime,
  children,
}: {
  eyebrow?: string;
  title: string;
  asOfTime?: string;
  children?: React.ReactNode;
}) {
  const renderedTime = asOfTime ?? new Date().toISOString();

  return (
    <header className="page-header">
      <div>
        <p className="page-header-eyebrow eyebrow">{eyebrow}</p>
        <h1 className="page-header-title">{title}</h1>
        <p className="page-header-meta">
          Rendered evidence time:{" "}
          <time dateTime={renderedTime}>{utc(renderedTime)}</time> UTC
        </p>
      </div>
      {children ? <div className="page-header-actions">{children}</div> : null}
    </header>
  );
}
