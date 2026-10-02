import subprocess
import os

def run_git_command(repo_path: str, command: list[str]) -> str:
    """Run a git command in the specified repository path."""
    try:
        result = subprocess.run(
            ["git"] + command,
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Git command failed: {e.stderr}")
        return ""

def get_tracked_files(repo_path: str) -> list[str]:
    """Phase 1: Get only files tracked by Git to build file inventory."""
    output = run_git_command(repo_path, ["ls-files"])
    if not output:
        return []
    
    # Return absolute paths for files that exist
    files = []
    for f in output.split('\n'):
        if f:
            abs_path = os.path.join(repo_path, f)
            if os.path.isfile(abs_path):
                files.append(abs_path)
    return files

def get_file_commit_info(repo_path: str, file_path: str) -> dict:
    """Get the latest commit hash and author for a specific file."""
    # Convert absolute to relative path for git command
    rel_path = os.path.relpath(file_path, repo_path)
    output = run_git_command(repo_path, ["log", "-n", "1", "--pretty=format:%H|%an|%at", "--", rel_path])
    
    if not output:
        return {"hash": "", "author": "", "timestamp": ""}
        
    parts = output.split('|')
    return {
        "hash": parts[0] if len(parts) > 0 else "",
        "author": parts[1] if len(parts) > 1 else "",
        "timestamp": parts[2] if len(parts) > 2 else ""
    }

def get_current_repo_commit(repo_path: str) -> str:
    """Get the current HEAD commit hash."""
    return run_git_command(repo_path, ["rev-parse", "HEAD"])
