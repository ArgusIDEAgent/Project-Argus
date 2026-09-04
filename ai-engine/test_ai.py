import os
from dotenv import load_dotenv
from litellm import completion

# Load the API key from your .env file
load_dotenv()

print("Routing request directly to Cohere North Mini Code via OpenRouter...")

# Using an actively supported free model for coding
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