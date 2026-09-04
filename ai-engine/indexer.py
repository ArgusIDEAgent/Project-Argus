import os
from langchain_text_splitters import RecursiveCharacterTextSplitter, Language
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from fastembed import TextEmbedding

COLLECTION_NAME = "codemind_codebase"

# 1. Initialize Qdrant Client & Local Embedding Model
client = QdrantClient(host="localhost", port=6333)
embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")

# 2. Ensure Collection Exists (bge-small uses 384-dimensional vectors)
if not client.collection_exists(COLLECTION_NAME):
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    )
    print(f"✅ Created Qdrant collection: '{COLLECTION_NAME}'")

# 3. Read Code & Split Using Code-Aware Separators
file_path = "sample_code/auth_service.py"
with open(file_path, "r") as f:
    raw_code = f.read()

python_splitter = RecursiveCharacterTextSplitter.from_language(
    language=Language.PYTHON, 
    chunk_size=300, 
    chunk_overlap=50
)

code_chunks = python_splitter.split_text(raw_code)
print(f"📦 Split code into {len(code_chunks)} semantic chunks.")

# 4. Generate Embeddings & Push to Qdrant
embeddings = list(embedding_model.embed(code_chunks))

points = []
for idx, (chunk, vector) in enumerate(zip(code_chunks, embeddings)):
    points.append(
        PointStruct(
            id=idx + 1,
            vector=vector.tolist(),
            payload={
                "file_path": file_path,
                "code_snippet": chunk,
                "language": "python"
            }
        )
    )

client.upsert(collection_name=COLLECTION_NAME, points=points)
print(f"🚀 Successfully indexed {len(points)} points into Qdrant!")
