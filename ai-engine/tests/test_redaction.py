"""Regression guard for the index-time secret redaction.

`_redact_secrets` is the redaction applied to everything CodeMind persists (the
vector payload code_snippet and the SQLite metadata) and to everything it shows
back through lexical search, grounded answers and documentation. It previously
required a quote character immediately after the assignment operator, so the
unquoted config syntaxes in which real credentials are most often written were
never masked.

The negative cases below are as important as the positive ones: a redactor that
masks ordinary source code destroys the product's own output, so an assignment
whose value is a call, an attribute path or a plain identifier must survive
intact.
"""
import unittest

from indexer import _redact_secrets

SECRET = "hunter2-total-secret"


class AssignedSecretRedactionTests(unittest.TestCase):
    def assertRedacted(self, text):
        self.assertIn("[REDACTED]", _redact_secrets(text), f"not redacted: {text!r}")
        self.assertNotIn(SECRET, _redact_secrets(text), f"leaked: {text!r}")

    def assertUntouched(self, text):
        self.assertEqual(_redact_secrets(text), text, f"unexpectedly rewritten: {text!r}")

    def test_unquoted_values_are_redacted(self):
        for text in (
            f"  password: {SECRET}\n",
            f"password = {SECRET}\n",
            f"api_key: {SECRET}\n",
            f"  password: {SECRET}  # trailing comment\n",
        ):
            with self.subTest(text=text):
                self.assertRedacted(text)

    def test_environment_style_names_are_redacted(self):
        for text in (
            f"DATABASE_PASSWORD={SECRET}\n",
            f"STRIPE_SECRET_KEY={SECRET}\n",
            f"AWS_SECRET_ACCESS_KEY={SECRET}\n",
            f"DATABASE_PASSWORD = {SECRET}\n",
        ):
            with self.subTest(text=text):
                self.assertRedacted(text)

    def test_quoted_container_keys_are_redacted(self):
        for text in (
            '{"apiKey": "hunter2-total-secret"}\n',
            '{"api_key": "hunter2-total-secret", "port": 5432}\n',
            "apiKey: 'hunter2-total-secret'\n",
        ):
            with self.subTest(text=text):
                self.assertRedacted(text)

    def test_source_code_values_are_left_readable(self):
        for text in (
            "password = get_password()\n",
            "token = os.environ['CODEMIND_SESSION_TOKEN']\n",
            "password = settings.SECRET_VALUE\n",
            "token = None\n",
            "token = refresh_token\n",
            "MAX_TOKEN_SIZE = 1024\n",
            "db_url: postgres://localhost:5432/app\n",
            "username = 'service-account-reader'\n",
        ):
            with self.subTest(text=text):
                self.assertUntouched(text)

    def test_line_count_is_preserved(self):
        for text in (
            f"password: {SECRET}\nother = 1\n",
            "-----BEGIN RSA PRIVATE KEY-----\nAAAABBBBCCCC\nDDDDEEEEFFFF\n-----END RSA PRIVATE KEY-----\n",
        ):
            with self.subTest(text=text):
                self.assertEqual(_redact_secrets(text).count("\n"), text.count("\n"))

    def test_degenerate_inputs_do_not_raise(self):
        for text in ("", "\n", "password", "password:", "password: \n", "=", ":", "  password:  \n"):
            with self.subTest(text=text):
                self.assertIsInstance(_redact_secrets(text), str)


class ExistingRedactionContractTests(unittest.TestCase):
    """The pre-existing value patterns must keep working."""

    def test_known_credential_shapes_are_still_masked(self):
        for text in (
            "aws = AKIAIOSFODNN7EXAMPLE\n",
            "token = ghp_abcdefghijklmnopqrstuvwxyz0123456789ab\n",
            "key = 'sk-abcdefghijklmnopqrstuvwx'\n",
            "hook = xoxb-1234567890-abcdefghij\n",
        ):
            with self.subTest(text=text):
                self.assertIn("[REDACTED]", _redact_secrets(text))

    def test_private_key_block_is_masked_without_losing_its_lines(self):
        text = "-----BEGIN OPENSSH PRIVATE KEY-----\nsecretbody\n-----END OPENSSH PRIVATE KEY-----\n"
        masked = _redact_secrets(text)
        self.assertNotIn("secretbody", masked)
        self.assertEqual(masked.count("\n"), text.count("\n"))


if __name__ == "__main__":
    unittest.main()
