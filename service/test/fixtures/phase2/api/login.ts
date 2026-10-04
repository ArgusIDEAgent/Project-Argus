import { authenticate } from '../core/auth';

router.post('/api/login', login);

export function login(req: Request) {
  return authenticate(req.user);
}
