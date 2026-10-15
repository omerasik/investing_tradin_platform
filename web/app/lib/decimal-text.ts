/** Trim a decimal string's trailing zeros for display only; the value itself is never rounded. */
export function decimalText(value: string): string {
  return value.includes(".") ? value.replace(/0+$/, "").replace(/\.$/, "") : value;
}
