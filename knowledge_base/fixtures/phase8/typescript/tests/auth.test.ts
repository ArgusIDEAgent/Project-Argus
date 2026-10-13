import {authorizeRequest, validateCredential} from "../src/auth";
import {SecureService} from "../src/service";
export function permissionsTest() { return authorizeRequest("admin"); }
export const credentialReference = validateCredential;
export function methodTest() { return new SecureService().check("ok"); }
