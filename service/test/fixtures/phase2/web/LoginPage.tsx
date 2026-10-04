export function LoginPage() {
  fetch('/api/login', { method: 'POST' });
  return <button>Log in</button>;
}
