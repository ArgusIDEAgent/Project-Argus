import {requestLogin} from "./controller";
export function applicationEntry() {
  return requestLogin("admin", "credential");
}
