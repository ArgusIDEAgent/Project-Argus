from qdrant_client import QdrantClient
from fastembed import TextEmbedding

COLLECTION_NAME = "codemind_codebase"

# 1. Initialize Clients
client = QdrantClient(host="localhost", port=6333)
embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")

def search_codebase(query: str):
    print(f"\n🔍 Searching for: '{query}'")
    
    # 2. Convert the natural language question into a vector
    query_vector = list(embedding_model.embed([query]))[0].tolist()
    
    # 3. Search Qdrant for the most mathematically similar code chunk
    search_results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        limit=1
    ).points
    
    # 4. Display the results
    if not search_results:
        print("❌ No relevant code found.")
        return

    top_result = search_results[0]
    payload = top_result.payload
    
    print(f"✅ Found relevant code in: {payload['file_path']} (Score: {top_result.score:.4f})")
    print("-" * 40)
    print(payload['code_snippet'])
    print("-" * 40)

# Run a test query
if __name__ == "__main__":
    search_codebase("How do we check if a token is expired?")