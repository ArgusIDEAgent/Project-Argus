/** Reject empty authentication credentials. */
export function validateCredential(token: string) {
  return token.length > 0;
}
/** Check resource permissions before granting protected access. */
export function authorizeRequest(role: string) {
  return role === "admin";
}
/** Reject login sessions after their expiration time. */
export function sessionIsCurrent(expiresAt: number, now: number) {
  return expiresAt > now;
}
