"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.verifiedSourcePath = verifiedSourcePath;
const node_crypto_1 = require("node:crypto");
const fs = __importStar(require("node:fs"));
const path = __importStar(require("node:path"));
function verifiedSourcePath(rootPath, relativePath, sourceHash) {
    if (!relativePath || path.isAbsolute(relativePath) || relativePath.includes('\0')) {
        throw new Error('Invalid indexed source path.');
    }
    const root = fs.realpathSync(rootPath);
    const file = path.resolve(root, relativePath);
    if (!file.startsWith(root + path.sep)) {
        throw new Error('Indexed source is outside the selected repository.');
    }
    const real = fs.realpathSync(file);
    if (!real.startsWith(root + path.sep))
        throw new Error('Indexed source is outside the selected repository.');
    if ((0, node_crypto_1.createHash)('sha256').update(fs.readFileSync(real)).digest('hex') !== sourceHash) {
        throw new Error('This source changed. Refresh the repository before opening it.');
    }
    return real;
}
//# sourceMappingURL=sourceNavigation.js.map