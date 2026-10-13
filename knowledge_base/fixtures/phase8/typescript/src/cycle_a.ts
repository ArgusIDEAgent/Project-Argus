import {cycleB} from "./cycle_b";
export function cycleA(next: boolean): boolean { return next ? cycleB(false) : true; }
