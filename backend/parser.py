import os
from tree_sitter import Language, Parser
import tree_sitter_python as tspython
import git_service

try:
    PY_LANGUAGE = Language(tspython.language(), 'python')
except TypeError:
    # Handle API differences in newer tree-sitter bindings
    PY_LANGUAGE = Language(tspython.language())

parser = Parser()
parser.set_language(PY_LANGUAGE)

TS_QUERY = """
(function_definition name: (identifier) @func.name) @func.def
(class_definition name: (identifier) @class.name) @class.def
(import_statement) @import.stmt
(import_from_statement) @import.from
"""

def extract_code_graph(project_path: str):
    """Scans the project and extracts structured nodes for Cognee."""
    code_documents = []
    
    # Phase 1: Git-aware file tracking instead of os.walk
    tracked_files = git_service.get_tracked_files(project_path)
    
    # Fallback to os.walk if git is not initialized or fails
    if not tracked_files:
        print("Git repository not detected, falling back to os.walk...")
        for root, _, files in os.walk(project_path):
            if "venv" in root or ".git" in root or "node_modules" in root:
                continue
            for file in files:
                tracked_files.append(os.path.join(root, file))
    
    try:
        query = PY_LANGUAGE.query(TS_QUERY)
    except Exception as e:
        print(f"Error compiling tree-sitter query: {e}")
        query = None

    for file_path in tracked_files:
        if not (file_path.endswith(".py") or file_path.endswith(".js") or file_path.endswith(".ts")):
            continue
            
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                code_content = f.read()
            
            commit_info = git_service.get_file_commit_info(project_path, file_path)
            file_name = os.path.basename(file_path)
            
            extracted_symbols = []
            
            # Phase 2: AST Parsing for Symbols
            if file_path.endswith(".py") and query:
                tree = parser.parse(bytes(code_content, "utf8"))
                
                # In tree_sitter python v0.22+, captures can be a dictionary or list of tuples
                # We handle both formats safely
                try:
                    captures = query.captures(tree.root_node)
                    # Support list of tuples: [(node, name)] or dict: {name: [nodes]}
                    if isinstance(captures, dict):
                        for name, nodes in captures.items():
                            for node in nodes:
                                if name.endswith(".name"):
                                    continue # Skip just names, we want the full definition blocks
                                extracted_symbols.append({
                                    "kind": name,
                                    "text": node.text.decode('utf8'),
                                    "start_line": node.start_point[0] + 1,
                                    "end_line": node.end_point[0] + 1
                                })
                    else:
                        for node, name in captures:
                            if name.endswith(".name"):
                                continue # Skip just names, we want the full definition blocks
                            extracted_symbols.append({
                                "kind": name,
                                "text": node.text.decode('utf8'),
                                "start_line": node.start_point[0] + 1,
                                "end_line": node.end_point[0] + 1
                            })
                except Exception as e:
                    print(f"Error querying AST in {file_name}: {e}")

            # Structure the document for cognee (including AST symbols)
            code_documents.append({
                "id": file_path,
                "text": code_content,
                "metadata": {
                    "file_type": file_name.split('.')[-1],
                    "file_name": file_name,
                    "last_commit": commit_info["hash"],
                    "symbols": extracted_symbols
                }
            })
        except Exception as e:
            print(f"Error processing {file_path}: {e}")
            
    return code_documents
