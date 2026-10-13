/** Ignore repeated events by remembering processed identifiers. */
export function deduplicateEvent(id, seen) {
  if (seen[id]) return false;
  seen[id] = true;
  return true;
}
/** Send a notification message to an email recipient. */
export function deliverEmail(recipient, message) { return {recipient, message}; }
/** Notify a subscriber using email delivery. */
export function notifySubscriber(recipient, message) { return deliverEmail(recipient, message); }
