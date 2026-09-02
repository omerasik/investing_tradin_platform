export function ParameterTable({ schema }: { schema: Record<string, unknown> }) {
  const entries = Object.entries(schema);
  if (entries.length === 0) {
    return <p className="empty-notice">No parameters recorded.</p>;
  }
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th scope="col">Parameter</th>
          <th scope="col">Value</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([key, value]) => (
          <tr key={key}>
            <td><code>{key}</code></td>
            <td>{typeof value === "object" ? JSON.stringify(value) : String(value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
