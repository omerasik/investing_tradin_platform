export function ContradictionPanel({ items }: { items: string[] }) {
  return (
    <div className="contradiction-panel" aria-label="Contradicting Evidence">
      <span className="inspector-section-title">Contradicting Evidence</span>
      {items.length > 0 ? (
        <ul>
          {items.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="empty-notice">None recorded</p>
      )}
    </div>
  );
}
