import {authorizeRequest as allow} from "@auth";
import {SecureService} from "./service";
/** Validate a login and check permission to enter a protected resource. */
export function requestLogin(role: string, token: string) {
  const service = new SecureService();
  return allow(role) && service.check(token);
}
