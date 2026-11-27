"""Only bounded in-memory stdout/stderr artifacts are exported in P5."""
import re


def artifact_name(name):
    if name != 'console.txt':
        raise ValueError('P5 exports only the broker-owned console.txt artifact')
    return name


def sanitize(data):
    text = data.decode('utf-8', errors='replace')
    text = re.sub(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))', '', text)
    text = ''.join(c for c in text if c in '\n\t' or (ord(c) >= 32 and ord(c) != 127 and not 0x202a <= ord(c) <= 0x202e))
    text = re.sub(r'(?i)\b(token|password|secret|api[_-]?key)\s*[:=]\s*[^\s]+', r'\1=[REDACTED]', text)
    return text
