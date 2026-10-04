import { findUser } from '../data/users';

export function authenticate(user: User) {
  return findUser(user.id);
}
