export class FirstChoice { run() { return true; } }
export class SecondChoice { run() { return false; } }
export function unionDispatch(value: FirstChoice | SecondChoice) { return value.run(); }
export function unresolvedDispatch(value: any) { return value.unknown(); }
