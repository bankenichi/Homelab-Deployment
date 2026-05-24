# mcp_server.py
"""MCP server for coding assistant."""
import asyncio
import io
import os
import re
import subprocess
import shutil
from pathlib import Path

import pdfplumber
import psutil
import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

from mcp.server.fastmcp import FastMCP

server = FastMCP("coding-assistant")

# ==================== WEB =====================

@server.tool()
async def web_search(query: str, max_results: int = 5) -> str:
    """
    CRITICAL: Use this tool to SEARCH the internet for keywords, errors, or general topics.
    Use this when you need to find information but do not have an exact, working URL.
    IF a URL fails or returns a 404, DO NOT retry fetching it. Instead, use THIS tool to search for the topic.
    Argument format: pass the query as a standard string.
    """
    try:
        url = "http://localhost:8080/search"
        params = {
            "q": query,
            "format": "json"
        }
        
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        if "results" not in data or not data["results"]:
            return "No results found."
            
        output = []
        for i, item in enumerate(data["results"]):
            title = item.get('title', 'No Title')
            link = item.get('url', 'No URL')
            snippet = item.get('content', '')
            
            output.append(f"## Result {i+1}\n**{title}**\nURL: {link}\nSnippet: {snippet}")
            
            if i + 1 >= max_results:
                break
                
        return "\n\n".join(output)
    except Exception as e:
        return f"SearxNG Error: {e}"

@server.tool()
async def fetch_url(url: str) -> str:
    """
    Use this tool ONLY to read the content of a specific, exactly known URL.
    WARNING: If this returns an error or a 404 Not Found, DO NOT call this tool again with the same URL.
    If it fails, instantly switch to using the 'web_search' tool instead.
    """
    try:
        jina_url = f"https://r.jina.ai/{url}"
        
        resp = requests.get(
            jina_url, 
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        resp.raise_for_status()
        
        text = resp.text
        if len(text) > 50000:
            return text[:50000] + "\n\n...[SYSTEM WARNING: CONTENT TRUNCATED FOR LENGTH]..."
        return text
        
    except Exception as e:
        return f"Error fetching URL: {e}"

# ==================== MARKDOWN =====================

@server.tool()
async def read_markdown(path: str, convert_to_html: bool = False) -> str:
    """Read a markdown file. Pass convert_to_html=True to get HTML output instead of raw markdown."""
    try:
        content = Path(path).read_text(encoding="utf-8")
        if convert_to_html:
            from markdown import markdown as md
            return md(content)
        return content
    except Exception as e:
        return f"Error: {e}"

@server.tool()
async def parse_markdown(path: str) -> str:
    """Parse a markdown file to extract its heading structure to understand the document layout."""
    try:
        from markdown_it import MarkdownIt
        md = MarkdownIt()
        content = Path(path).read_text(encoding="utf-8")
        tokens = md.parse(content)
        structure = []
        for tok in tokens:
            if tok.type == "heading_open":
                level = int(tok.tag[1]) if len(tok.tag) > 1 else 1
                structure.append(f"{'#' * level} Heading")
        return "\n".join(structure) if structure else "No headings found."
    except Exception as e:
        return f"Error: {e}"

# ==================== HTML =====================

@server.tool()
async def read_html(path: str) -> str:
    """Read the raw text of an HTML file."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception as e:
        return f"Error: {e}"

@server.tool()
async def parse_html(html: str) -> str:
    """Extract tags, attributes, and inner text from an HTML string to understand its DOM structure."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        elements = []
        for tag in soup.find_all(True):
            attrs = " ".join(f'{k}="{v}"' for k, v in tag.attrs.items())
            text = tag.get_text(strip=True)[:100]
            elements.append(f"<{tag.name} {attrs}>...{text}")
        return "\n".join(elements[:100])
    except Exception as e:
        return f"Error: {e}"

@server.tool()
async def validate_html(html: str) -> str:
    """Check an HTML string for common syntax errors like missing closing script or style tags."""
    try:
        errors = []
        if "<script>" in html and "</script>" not in html:
            errors.append("Missing closing </script>")
        if "<style>" in html and "</style>" not in html:
            errors.append("Missing closing </style>")
        return "\n".join(errors) if errors else "No issues found."
    except Exception as e:
        return f"Error: {e}"

# ==================== CSS =====================

@server.tool()
async def read_css(path: str) -> str:
    """Read the raw text of a CSS file."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception as e:
        return f"Error: {e}"

@server.tool()
async def extract_css_rules(css: str, selector: str = "") -> str:
    """Parse a CSS string and extract rules or counts."""
    try:
        from tinycss2 import parse_stylesheet
        rules = parse_stylesheet(css, skip_comments=True, skip_whitespace=True)
        if not selector:
            return f"Found {len(rules)} rules"
        return "Rule extraction requires manual node traversal in tinycss2."
    except Exception as e:
        return f"Error: {e}"

# ==================== JAVASCRIPT ===================================

@server.tool()
async def read_js(path: str) -> str:
    """Read the raw text of a JavaScript file."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception as e:
        return f"Error: {e}"

@server.tool()
async def parse_js(code: str) -> str:
    """Extract function, class, and variable declarations from a JavaScript string via regex."""
    try:
        items = []
        for match in re.finditer(r'function\s+(\w+)\s*\(', code):
            items.append(f"function {match.group(1)}()")
        for match in re.finditer(r'class\s+(\w+)', code):
            items.append(f"class {match.group(1)}")
        for match in re.finditer(r'(?:var|let|const)\s+(\w+)', code):
            items.append(f"var/let/const {match.group(1)}")
            
        return "\n".join(items[:50]) if items else "No clear declarations found."
    except Exception as e:
        return f"Error: {e}"

@server.tool()
def run_eslint(path: str) -> str:
    """Run ESLint on a specific file path to check for syntax and style issues."""
    try:
        result = subprocess.run(["eslint", path], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode != 0 else "No lint issues."
    except FileNotFoundError:
        return "eslint not installed"
    except Exception as e:
        return f"Error: {e}"

# ==================== PRETTIER =====================================

@server.tool()
def run_prettier(path: str, options: str = "") -> str:
    """Run Prettier to format a file at the given path. Overwrites the file with formatted code."""
    try:
        cmd = ["prettier", "--write", path]
        if options:
            cmd.extend(options.split())
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return f"Formatted: {result.stdout}"
    except FileNotFoundError:
        return "prettier not installed"
    except Exception as e:
        return f"Error: {e}"

@server.tool()
def check_prettier(path: str) -> str:
    """Check if a file is formatted according to Prettier without modifying it."""
    try:
        result = subprocess.run(["prettier", "--check", path], capture_output=True, text=True)
        if result.returncode != 0:
            return f"Not formatted.\n{result.stdout}"
        return "File is properly formatted."
    except FileNotFoundError:
        return "prettier not installed"
    except Exception as e:
        return f"Error: {e}"

# ==================== PDF ========================================

@server.tool()
async def read_pdf(path: str, pages: int | None = None) -> str:
    """Extract raw text from a PDF file."""
    try:
        with pdfplumber.open(path) as pdf:
            pdf_pages = pdf.pages[:pages] if pages else pdf.pages
            text = "\n\n---\n\n".join(page.extract_text() or "" for page in pdf_pages)
            
        if len(text) > 50000:
            return text[:50000] + "\n\n...[SYSTEM WARNING: CONTENT TRUNCATED FOR LENGTH]..."
        return text
    except Exception as e:
        return f"Error: {e}"

# ==================== FILE OPS ===================================

@server.tool()
async def read_file(path: str) -> str:
    """Read the exact contents of a file at the given path."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception as e:
        return f"Error: {e}"

@server.tool()
async def write_file(path: str, content: str) -> str:
    """
    Write content to a file. 
    WARNING: This will overwrite existing files. Ensure content is complete and correct.
    """
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"

@server.tool()
async def list_directory(path: str = ".") -> str:
    """List all files and directories in the given path."""
    try:
        entries = sorted(Path(path).glob("*"))
        return "\n".join(f"{'📁' if p.is_dir() else '📄'} {p.name}" for p in entries)
    except Exception as e:
        return f"Error: {e}"

@server.tool()
async def create_directory(path: str) -> str:
    """Create a new directory and any necessary parent directories."""
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        return f"Created {os.path.abspath(path)}"
    except Exception as e:
        return f"Error: {e}"

@server.tool()
async def delete_file(path: str) -> str:
    """Permanently delete a single file."""
    try:
        Path(path).unlink()
        return f"Deleted {os.path.abspath(path)}"
    except Exception as e:
        return f"Error: {e}"

@server.tool()
async def delete_directory(path: str) -> str:
    """Permanently delete a directory and ALL its contents."""
    try:
        shutil.rmtree(path)
        return f"Deleted {os.path.abspath(path)}"
    except Exception as e:
        return f"Error: {e}"

# ==================== SEARCH =====================================

@server.tool()
def search_text(pattern: str, directory: str = ".") -> str:
    """Search for a specific text pattern across all files in a directory using ripgrep or grep."""
    try:
        result = subprocess.run(
            ["rg", "--files-with-matches", "--no-heading", pattern, directory],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return "No matches found."
        return result.stdout.strip()
    except FileNotFoundError:
        pass
    try:
        result = subprocess.run(
            ["grep", "-r", "-l", pattern, directory],
            capture_output=True, text=True
        )
        return result.stdout.strip() if result.returncode == 0 else "No matches found."
    except Exception as e:
        return f"Error: {e}"

@server.tool()
async def find_files(pattern: str, directory: str = ".") -> str:
    """Find files matching a specific glob pattern (e.g., '*.py') in a directory."""
    try:
        matches = sorted(Path(directory).rglob(pattern))
        return "\n".join(str(m) for m in matches)
    except Exception as e:
        return f"Error: {e}"

# ==================== SHELL =====================================

@server.tool()
async def execute_command(command: str, timeout: int = 30, cwd: str = ".") -> str:
    """
    Execute a raw shell command asynchronously. 
    Use this to run builds, scripts, system utilities, or inspect environments.
    WARNING: Do not run interactive commands that require user input.
    """
    try:
        # Launch process asynchronously so the MCP server stays responsive
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd
        )
        
        try:
            # Wait for output with timeout
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            # Safely kill process if it hangs (e.g., waiting for input)
            try:
                process.kill()
                await process.wait()
            except ProcessLookupError:
                pass
            return f"Command timed out after {timeout} seconds. Did it wait for user input?"

        # Safely decode byte streams
        out_str = stdout.decode('utf-8', errors='replace').strip() or '(none)'
        err_str = stderr.decode('utf-8', errors='replace').strip() or '(none)'
        
        return f"Exit code: {process.returncode}\nstdout: {out_str}\nstderr: {err_str}"
        
    except Exception as e:
        return f"Error: {e}"

# ==================== PYTHON ====================================

@server.tool()
async def run_python(code: str) -> str:
    """
    Execute a raw string of Python code in an isolated environment and capture output.
    WARNING: This environment is STATELESS. Variables do not persist between tool calls. 
    If you need to run multi-step logic, write a complete script to a file and run it via execute_command.
    """
    out = io.StringIO()
    try:
        exec(
            code,
            {"__builtins__": __builtins__,
             "print": lambda *a, **k: out.write(" ".join(map(str, a)) + "\n")},
            {}
        )
        return out.getvalue() or "No output."
    except Exception as e:
        return f"Error: {e}"

# ==================== PROCESS ====================================

@server.tool()
async def list_processes(limit: int = 20, sort_by_cpu: bool = True) -> str:
    """List running system processes, optionally sorted by CPU usage."""
    try:
        procs = []
        for p in psutil.process_iter(["pid", "name", "status", "cpu_percent"]):
            try:
                procs.append(p.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
                
        if sort_by_cpu:
            procs.sort(key=lambda x: x["cpu_percent"] or 0, reverse=True)
            
        lines = [f"{p['pid']:>6} {str(p['name'])[:20]:>20} {p['status']:>10} {p['cpu_percent'] or 0:.1f}%"
                 for p in procs[:limit]]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"

# ==================== ENVIRONMENT =================================

@server.tool()
async def get_env() -> str:
    """Get non-secret environment variables from the system."""
    secrets = {"PASSWORD", "SECRET", "TOKEN", "KEY", "API_KEY"}
    env = {
        k: v for k, v in os.environ.items()
        if not any(s in k.upper() for s in secrets)
    }
    return "\n".join(f"{k}={v}" for k, v in sorted(env.items()))

# ==================== GIT =======================================

@server.tool()
def git_status(path: str = ".") -> str:
    """Get the current working tree status of a git repository."""
    try:
        result = subprocess.run(["git", "status", "--short"], capture_output=True,
                               text=True, cwd=path)
        return result.stdout.strip() if result.returncode == 0 else f"Error: {result.stderr}"
    except Exception as e:
        return f"Error: {e}"

@server.tool()
def git_diff(path: str = ".") -> str:
    """Get the git diff of uncommitted changes."""
    try:
        result = subprocess.run(["git", "diff"], capture_output=True,
                               text=True, cwd=path)
        return result.stdout.strip() if result.returncode == 0 else f"Error: {result.stderr}"
    except Exception as e:
        return f"Error: {e}"

@server.tool()
def git_log(path: str = ".", lines: int = 10) -> str:
    """Get the recent commit history of a git repository."""
    try:
        result = subprocess.run(["git", "log", "-n", str(lines), "--oneline"],
                               capture_output=True, text=True, cwd=path)
        return result.stdout.strip() if result.returncode == 0 else f"Error: {result.stderr}"
    except Exception as e:
        return f"Error: {e}"

# ==================== CODE QUALITY =================================

@server.tool()
def format_code(path: str) -> str:
    """Run 'black' to check Python code formatting without modifying it."""
    try:
        result = subprocess.run(["black", "--check", "--diff", path],
                               capture_output=True, text=True)
        if result.returncode != 0:
             return f"Formatting required:\n{result.stdout}"
        return "Code already formatted."
    except FileNotFoundError:
        return "black not installed"
    except Exception as e:
        return f"Error: {e}"

@server.tool()
def lint_code(path: str) -> str:
    """Run 'flake8' to lint Python code for errors and style violations."""
    try:
        result = subprocess.run(["flake8", path], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode != 0 else "No lint issues."
    except FileNotFoundError:
        return "flake8 not installed"
    except Exception as e:
        return f"Error: {e}"

# ==================== PACKAGE MGMT =================================

@server.tool()
def install_package(name: str) -> str:
    """Install a Python package using pip."""
    try:
        result = subprocess.run(["pip", "install", name], capture_output=True, text=True)
        return result.stdout + result.stderr
    except Exception as e:
        return f"Error: {e}"

# ==================== TESTING ====================================

@server.tool()
def run_tests(path: str = ".", pattern: str = "test_*", verbose: bool = True) -> str:
    """Run Python tests using pytest."""
    try:
        cmd = ["pytest", path, "-k", pattern, "-v"] if verbose else ["pytest", path, "-k", pattern]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return result.stdout + (result.stderr if result.returncode != 0 else "")
    except subprocess.TimeoutExpired:
        return "Tests timed out"
    except Exception as e:
        return f"Error: {e}"

# ==================== BUILD ======================================

@server.tool()
def run_build(path: str = ".") -> str:
    """Attempt to run common build commands (npm, yarn, or poetry)."""
    try:
        result = subprocess.run(["npm", "run", "build"], capture_output=True,
                               text=True, cwd=path, timeout=60)
        if result.returncode != 0:
            result = subprocess.run(["yarn", "build"], capture_output=True,
                                   text=True, cwd=path, timeout=60,
                                   env={**os.environ, "PATH": os.environ.get("PATH", "")})
            if result.returncode != 0:
                result = subprocess.run(["poetry", "build"], capture_output=True,
                                       text=True, cwd=path, timeout=60)
        return result.stdout + result.stderr
    except Exception as e:
        return f"Error: {e}"

# ==================== DATABASE ===================================

@server.tool()
def run_sql(query: str, db_url: str = "") -> str:
    """Execute a raw SQL query against a SQLite database URL."""
    try:
        import sqlite3
        if "sqlite" in db_url:
            conn = sqlite3.connect(db_url.replace("sqlite:///", ""))
            cursor = conn.execute(query)
            rows = cursor.fetchall()
            cols = [d[0] for d in cursor.description] if cursor.description else []
            conn.close()
            header = "\t".join(cols)
            data = "\n".join("\t".join(str(v) for v in row) for row in rows)
            return f"Columns: {header}\n{data}"
        return "SQLite only"
    except Exception as e:
        return f"Error: {e}"

# ==================== DOCKER =====================================

@server.tool()
def run_docker(command: str) -> str:
    """Execute a docker CLI command."""
    try:
        result = subprocess.run(["docker", *command.split()], capture_output=True,
                               text=True, timeout=300)
        return result.stdout + result.stderr if result.returncode == 0 else f"Error: {result.stderr}"
    except Exception as e:
        return f"Error: {e}"

# ==================== SLACK =====================================

@server.tool()
def send_slack(message: str, channel: str = "#general") -> str:
    """Send a message to Slack via webhook (requires SLACK_WEBHOOK env var)."""
    try:
        webhook = os.environ.get("SLACK_WEBHOOK")
        if not webhook:
            return "SLACK_WEBHOOK not set"
        resp = requests.post(webhook, json={"text": f"{channel}: {message}"}, timeout=10)
        return f"Sent: {resp.status_code}"
    except Exception as e:
        return f"Error: {e}"

# ==================== JSON =======================================

@server.tool()
async def read_json(path: str) -> str:
    """Read and format the contents of a JSON file."""
    try:
        import json
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        output = json.dumps(data, indent=2)
        if len(output) > 50000:
            return output[:50000] + "\n\n...[SYSTEM WARNING: CONTENT TRUNCATED FOR LENGTH]..."
        return output
    except Exception as e:
        return f"Error: {e}"

# ==================== YAML =======================================

@server.tool()
async def read_yaml(path: str) -> str:
    """Read and format the contents of a YAML file."""
    try:
        import yaml
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return yaml.dump(data, default_flow_style=False)
    except Exception as e:
        return f"Error: {e}"

# ==================== Textract ===================================

@server.tool()
def extract_text(path: str) -> str:
    """Extract raw text from various document formats using the textract library."""
    try:
        import textract
        content = textract.process(path).decode("utf-8", errors="replace")
        if len(content) > 50000:
            return content[:50000] + "\n\n...[SYSTEM WARNING: CONTENT TRUNCATED FOR LENGTH]..."
        return content
    except Exception as e:
        return f"Error: {e}"

# ==================== RUN ========================================

if __name__ == "__main__":
    server.run()