"""Presentation redaction wraps existing secret patterns; provenance is untouched."""

import re

SECRET_KEY = re.compile(r"(?i)^(?:api[_-]?key|secret(?:[_-]?key)?|token|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd|credential|authorization)$")
INLINE_SECRET = re.compile(r'''(?im)((?:["']?)(?:api[_-]?key|secret(?:[_-]?key)?|token|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd|credential|authorization)(?:["']?)\s*[:=]\s*)(["'])([^"'\n]+)\2''')
ENV_SECRET = re.compile(r"(?im)^(\s*(?:API[_-]?KEY|SECRET(?:[_-]?KEY)?|TOKEN|ACCESS[_-]?TOKEN|REFRESH[_-]?TOKEN|CLIENT[_-]?SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTHORIZATION)\s*=\s*)([^\s\"']+)\s*$")
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*")
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")


class UnsafePresentation(ValueError):
    category, status = "redaction_failed", "error"


class PresentationSafety:
    def __init__(self, answers):
        self.answers = answers

    def text(self, text):
        try:
            value = self.answers.sanitize_text(self.answers._redact_secrets(text))
            if not isinstance(value, str):
                raise TypeError("Unsafe redactor result")
        except Exception as error:
            raise UnsafePresentation("redaction_failed") from error
        value = INLINE_SECRET.sub(lambda m: m[1] + m[2] + "[REDACTED]" + m[2], value)
        value = ENV_SECRET.sub(lambda m: m[1] + "[REDACTED]", value)
        value = BEARER.sub("Bearer [REDACTED]", value)
        return JWT.sub("[REDACTED]", value)

    def tree(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.tree(item) for item in value]
        if isinstance(value, dict):
            parameter_secret = isinstance(value.get("name"), str) and bool(SECRET_KEY.fullmatch(value["name"]))
            return {self.text(key): "[REDACTED]" if SECRET_KEY.fullmatch(key) or parameter_secret and key.lower() in ("default", "default_value", "defaultvalue", "value", "initializer") else self.tree(item) for key, item in value.items()}
        return value

    def model_text(self, text):
        # Numeric markers are stripped using the existing answer utility. Other
        # model-written citation namespaces are rejected, never made trusted.
        if re.search(r"\bcm[78]:", text):
            raise ValueError("model_written_citation")
        value = self.text(text)
        cleaned = self.answers._explanation_only(value)
        if cleaned != value.strip():
            raise ValueError("model_written_citation")
        return cleaned
