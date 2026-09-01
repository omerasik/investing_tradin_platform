import React from "react";

export type KeyValuePair = {
  key: string;
  label: string;
  value: React.ReactNode;
};

export function KeyValueGrid({
  items,
  columns = 2,
}: {
  items: KeyValuePair[];
  columns?: 1 | 2 | 3 | 4;
}) {
  return (
    <dl className={`key-value-grid key-value-grid-${columns}`}>
      {items.map((item) => (
        <div key={item.key} className="key-value-item">
          <dt className="key-value-label">{item.label}</dt>
          <dd className="key-value-value">{item.value ?? "UNAVAILABLE"}</dd>
        </div>
      ))}
    </dl>
  );
}
