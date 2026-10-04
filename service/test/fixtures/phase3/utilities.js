// Normalize an email address by trimming spaces and converting to lowercase.
export function normalizeEmail(value) { return value.trim().toLowerCase(); }

// Restrict a number to the inclusive minimum and maximum bounds.
export function clamp(value, min, max) { return Math.min(max, Math.max(min, value)); }

// Add two numbers.
export function add(left, right) { return left + right; }

// Parse JSON safely and return a fallback for invalid input.
export function parseJson(text, fallback) {
  try { return JSON.parse(text); } catch { return fallback; }
}
