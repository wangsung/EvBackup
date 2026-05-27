import os
import sys
import re
import datetime
from pathlib import Path
from flask import Blueprint, jsonify, request, send_from_directory, render_template

# Define Blueprint pointing to its own nested folders relative to the module package
browser_bp = Blueprint(
    'browser', 
    __name__,
    template_folder='templates',
    static_folder='static'
)

# Reference parent backup module
import backup

def get_markdown_dir():
    """Dynamically resolve the markdown directory from config at request time to prevent caching/desync issues."""
    return Path(backup.load_config()) / "markdown"

def get_resources_dir():
    """Dynamically resolve the resources directory from config at request time."""
    return get_markdown_dir() / "_resources"

def get_file_meta_info(filepath, keep_version=None):
    """Retrieve formatted file size and modification date metadata."""
    try:
        stat = filepath.stat()
        size_bytes = stat.st_size
        if size_bytes < 1024:
            size_str = f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            size_str = f"{size_bytes / 1024:.1f} KB"
        else:
            size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
            
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
        date_str = mtime.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        size_str = "Unknown"
        date_str = "Unknown"
        
    info = {
        "filename": filepath.name,
        "size": size_str,
        "date": date_str
    }
    if keep_version:
        info["keep_version"] = keep_version
    return info

def format_evernote_date(date_str):
    """Format Evernote XML dates (YYYYMMDDTHHMMSSZ) into readable dates."""
    if not date_str:
        return ""
    try:
        m = re.match(r'^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$', str(date_str))
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}:{m.group(6)}"
        return str(date_str)
    except Exception:
        return str(date_str)

def parse_note_file(filepath):
    """Parse front matter and extract note metadata and snippet."""
    content = ""
    title = filepath.stem
    tags = []
    created = ""
    updated = ""
    
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
            
        # Parse YAML Front Matter
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', text, re.DOTALL)
        if match:
            front_matter_text = match.group(1)
            content = match.group(2)
            try:
                import yaml
                metadata = yaml.safe_load(front_matter_text)
                if metadata:
                    title = metadata.get("title", title)
                    tags = metadata.get("tags", [])
                    if isinstance(tags, str):
                        tags = [tags]
                    elif not isinstance(tags, list):
                        tags = []
                    created = metadata.get("created", "")
                    updated = metadata.get("updated", "")
            except Exception as e:
                print(f"[-] YAML parsing error in {filepath.name}: {e}")
        else:
            content = text
            
    except Exception as e:
        print(f"[-] Failed to read {filepath.name}: {e}")
        content = ""
        
    # Generate clean plaintext snippet
    snippet = content[:300]
    snippet = re.sub(r'#+\s+', '', snippet) # Strip headers
    snippet = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', snippet) # Strip links
    snippet = re.sub(r'[*_`~]', '', snippet) # Strip formatting
    snippet = re.sub(r'<[^<]+?>', '', snippet) # Strip HTML tags
    snippet = re.sub(r'\s+', ' ', snippet).strip() # Collapse spaces
    
    if len(snippet) > 130:
        snippet = snippet[:130] + "..."
    
    return {
        "filename": filepath.name,
        "title": title,
        "tags": tags,
        "created": format_evernote_date(created),
        "updated": format_evernote_date(updated),
        "snippet": snippet
    }

@browser_bp.route('/')
def index():
    """Serve the main browser index/viewer page."""
    return render_template('browser.html')

@browser_bp.route('/cleaner')
def cleaner():
    """Serve the duplicate cleaner UI."""
    return render_template('cleaner.html')

@browser_bp.route('/api/notebooks')
def get_notebooks():
    """Get list of notebooks (folders) with their respective note count."""
    markdown_dir = get_markdown_dir()
    if not markdown_dir.exists():
        return jsonify([])
        
    notebooks = []
    for entry in markdown_dir.iterdir():
        if entry.is_dir() and not entry.name.startswith("_"):
            md_files = list(entry.glob("*.md"))
            notebooks.append({
                "name": entry.name,
                "count": len(md_files)
            })
            
    notebooks.sort(key=lambda x: x["name"])
    return jsonify(notebooks)

@browser_bp.route('/api/notes/<notebook_name>')
def get_notes_in_notebook(notebook_name):
    """Get lists of note metadata for a given notebook."""
    notebook_path = get_markdown_dir() / notebook_name
    if not notebook_path.exists() or not notebook_path.is_dir():
        return jsonify([]), 404
        
    notes = []
    for filepath in notebook_path.glob("*.md"):
        notes.append(parse_note_file(filepath))
        
    notes.sort(key=lambda x: x["created"], reverse=True)
    return jsonify(notes)

@browser_bp.route('/api/notes/<notebook_name>/<filename>/raw')
def get_raw_note_content(notebook_name, filename):
    """Retrieve the raw markdown content of a specific note."""
    note_path = get_markdown_dir() / notebook_name / filename
    if not note_path.exists():
        return jsonify({"error": "Note not found"}), 404
        
    try:
        with open(note_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        return text, 200, {'Content-Type': 'text/plain; charset=utf-8'}
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@browser_bp.route('/api/resources/<path:filename>')
def get_resource(filename):
    """Serve attachment files (images, PDFs) from the resources directory."""
    resources_dir = get_resources_dir()
    if not resources_dir.exists():
        return jsonify({"error": "Resources directory does not exist"}), 404
    return send_from_directory(resources_dir, filename)

@browser_bp.route('/api/tags')
def get_all_tags():
    """Aggregate all tags across all notes in the backup database."""
    markdown_dir = get_markdown_dir()
    if not markdown_dir.exists():
        return jsonify({})
        
    tag_counts = {}
    for notebook_dir in markdown_dir.iterdir():
        if notebook_dir.is_dir() and not notebook_dir.name.startswith("_"):
            for filepath in notebook_dir.glob("*.md"):
                try:
                    note_info = parse_note_file(filepath)
                    for tag in note_info.get("tags", []):
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1
                except Exception:
                    pass
                    
    sorted_tags = dict(sorted(tag_counts.items()))
    return jsonify(sorted_tags)

@browser_bp.route('/api/search')
def search_notes():
    """Perform a global full-text search across all notes."""
    query = request.args.get('q', '').strip().lower()
    if not query:
        return jsonify([])
        
    results = []
    markdown_dir = get_markdown_dir()
    if not markdown_dir.exists():
        return jsonify([])
        
    for notebook_dir in markdown_dir.iterdir():
        if notebook_dir.is_dir() and not notebook_dir.name.startswith("_"):
            for filepath in notebook_dir.glob("*.md"):
                try:
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                        
                    note_info = parse_note_file(filepath)
                    
                    title_match = query in note_info["title"].lower()
                    tag_match = any(query in tag.lower() for tag in note_info["tags"])
                    content_match = query in text.lower()
                    
                    if title_match or tag_match or content_match:
                        results.append({
                            "notebook": notebook_dir.name,
                            "filename": filepath.name,
                            "title": note_info["title"],
                            "created": note_info["created"],
                            "tags": note_info["tags"],
                            "snippet": note_info["snippet"]
                        })
                except Exception as e:
                    print(f"[-] Search error in {filepath.name}: {e}")
                    
    results.sort(key=lambda x: x["created"], reverse=True)
    return jsonify(results)

@browser_bp.route('/api/cleaner/scan/<notebook_name>')
def scan_duplicates(notebook_name):
    """Scan notebook for duplicate notes and group them into keep vs delete lists."""
    notebook_path = get_markdown_dir() / notebook_name
    if not notebook_path.exists() or not notebook_path.is_dir():
        return jsonify({"keep": [], "delete": []}), 404

    all_files = list(notebook_path.glob("*.md"))
    
    groups = {}
    for fp in all_files:
        filename = fp.name
        match = re.match(r'^(.*?)(_\d+)$', fp.stem)
        if match:
            base_name = match.group(1)
            suffix = match.group(2)
        else:
            base_name = fp.stem
            suffix = ""
            
        if base_name not in groups:
            groups[base_name] = []
        groups[base_name].append({
            "filepath": fp,
            "filename": filename,
            "base_name": base_name,
            "suffix": suffix
        })
        
    keep_list = []
    delete_list = []
    
    for base_name, file_entries in groups.items():
        if len(file_entries) == 1:
            keep_list.append(get_file_meta_info(file_entries[0]["filepath"]))
        else:
            def sort_key(entry):
                s = entry["suffix"]
                if not s:
                    return (0, 0)
                try:
                    num = int(s[1:])
                    return (1, num)
                except ValueError:
                    return (2, s)
            
            sorted_entries = sorted(file_entries, key=sort_key)
            keep_entry = sorted_entries[0]
            keep_list.append(get_file_meta_info(keep_entry["filepath"]))
            
            for del_entry in sorted_entries[1:]:
                delete_list.append(get_file_meta_info(del_entry["filepath"], keep_version=keep_entry["filename"]))
                
    keep_list.sort(key=lambda x: x["filename"].lower())
    delete_list.sort(key=lambda x: x["filename"].lower())
    
    return jsonify({
        "keep": keep_list,
        "delete": delete_list
    })

@browser_bp.route('/api/cleaner/delete', methods=['POST'])
def delete_duplicates():
    """Securely delete specified duplicate markdown files."""
    data = request.get_json() or {}
    notebook = data.get("notebook", "").strip()
    files_to_delete = data.get("files", [])
    
    if not notebook or not files_to_delete:
        return jsonify({"status": "error", "errors": ["Invalid payload"]}), 400
        
    if ".." in notebook or "/" in notebook or "\\" in notebook:
        return jsonify({"status": "error", "errors": ["Invalid notebook path"]}), 400
        
    notebook_path = get_markdown_dir() / notebook
    if not notebook_path.exists() or not notebook_path.is_dir():
        return jsonify({"status": "error", "errors": ["Notebook directory not found"]}), 400
        
    deleted_count = 0
    errors = []
    
    for filename in files_to_delete:
        filename = filename.strip()
        if ".." in filename or "/" in filename or "\\" in filename:
            errors.append(f"Security blocked invalid filename: {filename}")
            continue
        if not filename.endswith(".md"):
            errors.append(f"Security blocked non-markdown file: {filename}")
            continue
            
        file_path = notebook_path / filename
        if not file_path.exists():
            errors.append(f"File not found: {filename}")
            continue
            
        try:
            file_path.unlink()
            deleted_count += 1
        except Exception as e:
            errors.append(f"Failed to delete {filename}: {str(e)}")
            
    if errors:
        return jsonify({
            "status": "partial" if deleted_count > 0 else "error",
            "deleted": deleted_count,
            "errors": errors
        })
        
    return jsonify({
        "status": "success",
        "deleted": deleted_count,
        "errors": []
    })

@browser_bp.route('/api/notes/delete', methods=['POST'])
def delete_single_note():
    """Securely delete an individual markdown note file."""
    data = request.get_json() or {}
    notebook = data.get("notebook", "").strip()
    filename = data.get("filename", "").strip()
    
    if not notebook or not filename:
        return jsonify({"status": "error", "error": "Invalid payload"}), 400
        
    if ".." in notebook or "/" in notebook or "\\" in notebook:
        return jsonify({"status": "error", "error": "Security block: invalid notebook path"}), 400
    if ".." in filename or "/" in filename or "\\" in filename:
        return jsonify({"status": "error", "error": "Security block: invalid filename"}), 400
    if not filename.endswith(".md"):
        return jsonify({"status": "error", "error": "Security block: only markdown files are permitted"}), 400
        
    notebook_path = get_markdown_dir() / notebook
    if not notebook_path.exists() or not notebook_path.is_dir():
        return jsonify({"status": "error", "error": "Notebook directory not found"}), 400
        
    file_path = notebook_path / filename
    if not file_path.exists():
        return jsonify({"status": "error", "error": "Note file not found"}), 404
        
    try:
        file_path.unlink()
        return jsonify({"status": "success", "message": f"Successfully deleted {filename}"})
    except Exception as e:
        return jsonify({"status": "error", "error": f"Deletion failed: {str(e)}"}), 500
