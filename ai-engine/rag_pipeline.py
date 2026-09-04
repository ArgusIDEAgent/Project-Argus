import os
from dotenv import load_dotenv
from litellm import completion
from qdrant_client import QdrantClient
from fastembed import TextEmbedding

# Load API keys
load_dotenv()

# Initialize Clients
qdrant = QdrantClient(host="localhost", port=6333)
embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
COLLECTION_NAME = "codemind_codebase"

def ask_codemind(question: str):
    print(f"\n👤 Developer: {question}")
    
    # 1. RETRIEVE: Get the relevant code from Qdrant
    query_vector = list(embedding_model.embed([question]))[0].tolist()
    search_results = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        limit=1
    ).points
    
    if not search_results:
        return "I couldn't find any relevant code for that question."
        
    context_snippet = search_results[0].payload['code_snippet']
    file_path = search_results[0].payload['file_path']
    
    # 2. AUGMENT: Create the prompt with the retrieved code
    system_prompt = (
        "You are CodeMind, an AI developer assistant. Answer the user's question "
        "using ONLY the provided code context. Be concise and reference the file path."
    )
    
    user_prompt = f"""
    Context from {file_path}:
    ```python
    {context_snippet}
    ```
    
    Question: {question}
    """
    
    print("🧠 CodeMind is thinking...")
    
    # 3. GENERATE: Send to the free Cohere model via OpenRouter
    response = completion(
        model="openrouter/cohere/north-mini-code:free", 
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    )
    
    print("\n🤖 CodeMind:")
    print(response.choices[0].message.content)
    print("-" * 50)

# Run the complete pipeline
if __name__ == "__main__":
    ask_codemind("What encryption algorithm are we using for our JWT tokens?")
