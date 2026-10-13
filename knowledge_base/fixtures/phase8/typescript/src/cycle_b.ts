import {cycleA} from "./cycle_a";
export function cycleB(next: boolean): boolean { return next ? cycleA(false) : true; }
