/** Save computed values to avoid repeating expensive calculations. */
export function saveComputedResult(key, value, cache) { cache[key] = value; return value; }
/** Remove an expired cached entry. */
export function evictExpiredEntry(key, expiresAt, now, cache) {
  if (expiresAt < now) delete cache[key];
}
