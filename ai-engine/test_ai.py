import os
from dotenv import load_dotenv
from litellm import completion

# Load the API key from your .env file
load_dotenv()


def main():
    print("Routing request directly to Cohere North Mini Code via OpenRouter...")

    if not os.environ.get("OPENROUTER_API_KEY"):
        msg = "OPENROUTER_API_KEY is not set. Add it to ai-engine/.env (see .env.example)."
        print(f"❌ {msg}")
        return

    response = completion(
        model="openrouter/cohere/north-mini-code:free",
        messages=[
            {"role": "system", "content": "You are CodeMind, an elite AI Developer Assistant."},
            {"role": "user", "content": "In one sentence, what is an Abstract Syntax Tree (AST)?"}
        ]
    )

    # Print the AI's response
    print("\n🤖 CodeMind says:")
    print(response.choices[0].message.content)


if __name__ == "__main__":
    main()
