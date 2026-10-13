import {deduplicateEvent as accept, notifySubscriber} from "./events.js";
export function consumeEvent(id, seen) { return accept(id, seen); }
export function sendUpdate(recipient, message) { return notifySubscriber(recipient, message); }
export function unknownCallback(callback) { return callback(); }
