import {validateCredential} from "./auth";
export interface Validator {
  check(value: string): boolean;
}
export class BaseService {
  ready() { return true; }
}
/** Validate user credentials with a typed service contract. */
export class SecureService extends BaseService implements Validator {
  check(value: string): boolean;
  check(value: number): boolean;
  check(value: string | number): boolean {
    return typeof value === "string" ? validateCredential(value) : true;
  }
}
