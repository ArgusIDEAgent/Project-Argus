import os
import cognee

def setup_cognee():
    # Set Cognee to use Kuzu as the local development graph database
    cognee.config.set_graph_db("kuzu")
    # Set Kuzu local database path
    os.environ.setdefault("KUZU_PATH", os.path.join(os.path.expanduser("~"), ".codemind", "kuzu"))
    
    # Set Cognee to use local Qdrant for vector search
    cognee.config.set_vector_db("qdrant")
    os.environ["QDRANT_URL"] = "http://localhost:6333"
    
    # Configure LLM provider
    os.environ["LLM_PROVIDER"] = "openai" 
