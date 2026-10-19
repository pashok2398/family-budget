// Decimal wire values stay exact; no binary floating point financial arithmetic.
export function cents(value: string): bigint {
  if (!/^-?\d+(\.\d{1,2})?$/.test(value)) throw new Error('Invalid decimal amount');
  const negative = value.startsWith('-');
  const [whole, fraction = ''] = value.replace('-', '').split('.');
  return (BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'))) * (negative ? -1n : 1n);
}
export function amount(value: string, currency='ILS'): string {
  const n = cents(value), absolute = n < 0n ? -n : n;
  const whole = (absolute / 100n).toLocaleString('he-IL');
  const fraction = (absolute % 100n).toString().padStart(2,'0');
  return `${n < 0n ? '−' : ''}${whole}${fraction === '00' ? '' : '.'+fraction} ${currency === 'ILS' ? '₪' : currency}`;
}
export function percent(actual: string, planned: string): number {
  const a = cents(actual), b = cents(planned);
  if (b <= 0n) return a > 0n ? 100 : 0;
  return Number(a <= 0n ? 0n : a >= b ? 100n : a * 100n / b);
}
