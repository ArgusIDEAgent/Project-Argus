"""Manual-only legacy OpenRouter smoke utility; never used by local chat.

External requests require explicit --allow-external-llm authorization on every
invocation. Merely importing this module does not load credentials or LiteLLM.
See ai-engine/manual/README.md before intentionally running it.
"""
import argparse
import os


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-external-llm", action="store_true",
                        help="Explicitly authorize an external OpenRouter model request (may incur charges).")
    args = parser.parse_args(argv)
    if not args.allow_external_llm:
        parser.error("External model access refused; explicit --allow-external-llm is required.")

    from dotenv import load_dotenv
    load_dotenv()
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is not set; no request sent. See ai-engine/.env.example.")
        return 1

    # Even importing LiteLLM can fetch provider metadata, so defer it until both
    # authorization and credentials have been checked.
    from litellm import completion
    print("Running the manual legacy OpenRouter smoke request...")
    try:
        response = completion(
            model="openrouter/cohere/north-mini-code:free",
            messages=[
                {"role": "system", "content": "You are CodeMind, an elite AI Developer Assistant."},
                {"role": "user", "content": "In one sentence, what is an Abstract Syntax Tree (AST)?"},
            ],
        )
    except Exception:
        # Provider exceptions can contain request details. Never print them.
        print("Legacy smoke request failed; check provider configuration privately.")
        return 1
    print("\nCodeMind says:")
    print(response.choices[0].message.content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
