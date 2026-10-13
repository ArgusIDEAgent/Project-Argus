import {validateCredential} from "./auth";
export function loadLiteral() { return import("./auth"); }
export function loadDynamic(location: string) { return import(location); }
export function invokeDynamic(object: any, key: string) { return object[key](); }
export function applyValidator(callback: (value: string) => boolean, token: string) {
  return callback(token);
}
export function mappedCheck(token: string) {
  return applyValidator(value => validateCredential(value), token);
}
export function nestedCheck(token: string) {
  function inner() { return validateCredential(token); }
  return inner();
}
