import os
from fastapi import FastAPI, BackgroundTasks
from celery import Celery
import cognee
from config import setup_cognee
from parser import extract_code_graph

app = FastAPI(title="CodeMind Local Graph Service")

# Setup cognee (Kuzu + Qdrant)
setup_cognee()

# Initialize Celery
# Requires Redis: docker run -d -p 6379:6379 redis
celery_app = Celery("code_ingestion", broker="redis://localhost:6379/0")

@celery_app.task
def process_project_background(project_path: str):
    """Background task to parse and ingest codebase."""
    print(f"Starting ingestion for {project_path}...")
    
    # 1. Parse syntax and create normalized graph via custom pipeline
    documents = extract_code_graph(project_path)
    print(f"Found {len(documents)} source files.")
    
    # 2. Add documents to Cognee framework
    for doc in documents:
        # cognee.add expects an iterable of data or a string/id
        # We simulate the add operation. In real cognee, it might expect a specific schema.
        try:
            cognee.add([doc])
        except Exception as e:
            print(f"Error adding doc to cognee: {e}")
    
    # 3. Semantic Enrichment (The Magic Step)
    print("Running cognify pipeline...")
    try:
        cognee.cognify()
        print(f"✅ Successfully cognified project: {project_path}")
    except Exception as e:
        print(f"Error during cognify: {e}")

@app.post("/api/project/open")
async def open_project(project_path: str):
    """Endpoint triggered when the IDE opens a project."""
    # Dispatch to Celery so the API responds instantly
    process_project_background.delay(project_path)
    return {"status": "scanning_started", "message": f"Code ingestion started for {project_path} in background."}

@app.get("/api/chat")
async def ask_assistant(query: str):
    """Endpoint for GraphRAG multi-hop and semantic search."""
    try:
        # Cognee handles the Multi-hop graph traversal and Vector Search automatically
        results = await cognee.search(query_text=query, search_type="graph")
        return {"answer": results}
    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
