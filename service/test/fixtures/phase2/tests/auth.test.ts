import { authenticate } from '../core/auth';

test('auth works', () => authenticate({ id: 1 }));
