// A random idempotency key. crypto.randomUUID exists only in secure contexts (HTTPS or localhost) and the app
// is served over plain HTTP on the LAN; crypto.getRandomValues works in both.
export function requestKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}
