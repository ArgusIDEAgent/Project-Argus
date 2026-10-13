import {deduplicateEvent} from "../src/events.js";
export function duplicateTest() { return deduplicateEvent("event", {}); }
