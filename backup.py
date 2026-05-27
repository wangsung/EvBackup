import os
import sys
import subprocess
import re
import hashlib
import base64
import mimetypes
from pathlib import Path
import xml.etree.ElementTree as ET
import sqlite3
import json

CONFIG_PATH = Path(__file__).parent / "config.json"

def load_config():
    import getpass
    username = getpass.getuser()
    default_dir = f"c:/{username}/ever_md"
    if not CONFIG_PATH.exists():
        config = {"base_backup_dir": default_dir}
        try:
            CONFIG_PATH.write_text(json.dumps(config, indent=4), encoding="utf-8")
        except Exception:
            pass
        return default_dir
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return config.get("base_backup_dir", default_dir)
    except Exception:
        return default_dir

# Define global paths dynamically
BASE_BACKUP_DIR = Path(load_config())
DB_DIR = BASE_BACKUP_DIR / "db"
ENEX_DIR = BASE_BACKUP_DIR / "enex"
MARKDOWN_DIR = BASE_BACKUP_DIR / "markdown"
RESOURCES_DIR = MARKDOWN_DIR / "_resources"
DB_PATH = DB_DIR / "token_bk.db"
NOTE_DB_PATH = DB_DIR / "note.db"

def ensure_directories():
    """Ensure all required directories exist."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    ENEX_DIR.mkdir(parents=True, exist_ok=True)
    MARKDOWN_DIR.mkdir(parents=True, exist_ok=True)
    RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[+] Directories verified/created at: {BASE_BACKUP_DIR}")

def ensure_note_db_has_token():
    """Ensure note.db is initialized and has the latest auth token from token_bk.db."""
    import shutil
    if not NOTE_DB_PATH.exists():
        if DB_PATH.exists():
            shutil.copy(DB_PATH, NOTE_DB_PATH)
            print("[+] Initialized note.db from token_bk.db")
        else:
            print("[-] Error: token_bk.db does not exist. Please run 'init' first.")
            sys.exit(1)
    else:
        # Sync the config table from token_bk.db to note.db to ensure credentials are fresh
        if DB_PATH.exists():
            try:
                conn = sqlite3.connect(NOTE_DB_PATH)
                cursor = conn.cursor()
                db_str = str(DB_PATH.resolve()).replace("\\", "/")
                cursor.execute(f"ATTACH DATABASE '{db_str}' AS token_db;")
                cursor.execute("INSERT OR REPLACE INTO main.config SELECT * FROM token_db.config;")
                conn.commit()
                conn.close()
                print("[+] Synced latest auth token from token_bk.db to note.db")
            except Exception as e:
                print(f"[!] Warning: Failed to sync config table to note.db: {e}")

def run_command(command, check=True):
    """Run a system command and stream the output."""
    print(f"[*] Running: {' '.join(command)}")
    try:
        is_windows = os.name == 'nt'
        result = subprocess.run(command, check=check, text=True, shell=is_windows)
        return result.returncode == 0
    except FileNotFoundError:
        print("[-] Error: Python command or executable not found.")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"[-] Command failed with error: {e}")
        return False

def init_db():
    """Initialize the Evernote local database via OAuth using direct, interactive terminal execution."""
    ensure_directories()
    print("[*] Starting Evernote local database initialization...")
    print("==================================================================")
    print("[!] OAUTH LOGIN INSTRUCTIONS:")
    print("    1. Your default web browser should open automatically.")
    print("    2. IF THE BROWSER DOES NOT OPEN:")
    print("       A long link starting with 'https://www.evernote.com/...'")
    print("       will be printed directly on the screen below.")
    print("       Simply copy that entire link and paste it into your browser.")
    print("==================================================================")
    cmd = [sys.executable, "-m", "evernote_backup", "init-db", "-d", str(DB_PATH)]
    run_command(cmd)
    print("[+] Initialization completed successfully!")

def sync_db():
    """Sync notes from Evernote to the local SQLite database."""
    ensure_directories()
    check_and_sanitize_notebooks()
    ensure_note_db_has_token()
    print("[*] Syncing notes from Evernote (this may take a while depending on account size)...")
    cmd = [sys.executable, "-m", "evernote_backup", "sync", "-d", str(NOTE_DB_PATH)]
    run_command(cmd)
    print("[+] Sync completed successfully!")

def export_enex():
    """Export SQLite database notes to .enex files."""
    ensure_directories()
    check_and_sanitize_notebooks()
    if not NOTE_DB_PATH.exists():
        print("[-] Local database note.db not found. Please run 'sync' first.")
        sys.exit(1)
    print(f"[*] Exporting notes to ENEX format inside: {ENEX_DIR}")
    # Clear existing enex files to avoid duplicate sync confusion
    for file in ENEX_DIR.glob("*.enex"):
        try:
            file.unlink()
        except Exception:
            pass
    cmd = [sys.executable, "-m", "evernote_backup", "export", str(ENEX_DIR), "-d", str(NOTE_DB_PATH)]
    run_command(cmd)
    print(f"[+] Export completed! .enex files are saved in {ENEX_DIR}")

def check_and_sanitize_notebooks():
    """Scan Evernote database notebooks or exported ENEX files, warning the user if invalid characters or quotes exist."""
    print("==================================================================")
    print("[*] [Backup Integrity Check] Scanning notebook names and filenames for special characters...")
    
    # Check if BASE_BACKUP_DIR contains quotes
    if "'" in str(BASE_BACKUP_DIR) or '"' in str(BASE_BACKUP_DIR):
        print("[!] Warning: Local backup directory path contains quotes (' or \").")
        print("    --> It is highly recommended to strip quotes from the path to prevent browser loading errors.")
        
    has_issues = False
    
    # 1. Check note.db notebooks names
    if NOTE_DB_PATH.exists():
        try:
            conn = sqlite3.connect(NOTE_DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='notebooks';")
            if cursor.fetchone():
                cursor.execute("SELECT name FROM notebooks;")
                notebooks = [row[0] for row in cursor.fetchall()]
                for name in notebooks:
                    cleaned = re.sub(r'[\'"]', "", name)
                    cleaned = re.sub(r'[\\/*?:"<>|]', "_", cleaned)
                    cleaned = cleaned.strip()
                    if cleaned != name:
                        print(f"[!] Warning: Evernote notebook name '{name}' contains quotes (') or Windows invalid characters.")
                        print(f"    --> Saved as '{cleaned}' instead to prevent filesystem errors and browser template string conflicts.")
                        has_issues = True
            conn.close()
        except Exception as e:
            pass
            
    # 2. Check existing .enex filenames
    if ENEX_DIR.exists():
        for file in ENEX_DIR.glob("*.enex"):
            name = file.stem
            cleaned = re.sub(r'[\'"]', "", name)
            cleaned = re.sub(r'[\\/*?:"<>|]', "_", cleaned)
            cleaned = cleaned.strip()
            if cleaned != name:
                print(f"[!] Warning: ENEX filename '{name}' contains quotes (') or Windows invalid characters.")
                print(f"    --> Converted notes will be saved under the '{cleaned}' directory instead.")
                has_issues = True
                
    if has_issues:
        print("[*] Notebook name security checks and auto-correction guidance completed.")
    else:
        print("[+] Integrity check completed: No filename safety issues detected.")
    print("==================================================================")

def clean_filename(filename):
    """Remove characters that are invalid in Windows filenames, plus single/double quotes."""
    # First, strip single and double quotes to prevent browser template string/link errors
    filename = re.sub(r'[\'"]', "", filename)
    # Replace Windows-invalid chars
    filename = re.sub(r'[\\/*?:"<>|]', "_", filename)
    filename = filename.strip()
    return filename if filename else "Untitled_Note"

def parse_resources(note_element):
    """Parse resources (attachments) from an ENEX note element."""
    resources = {}
    for res in note_element.findall("resource"):
        data_elem = res.find("data")
        if data_elem is None or data_elem.text is None:
            continue
        
        # Extract and decode base64 data
        b64_data = data_elem.text.strip().replace("\n", "").replace("\r", "")
        try:
            binary_data = base64.b64decode(b64_data)
        except Exception as e:
            print(f"[-] Failed to decode resource base64: {e}")
            continue
        
        # Calculate MD5 hash to match with <en-media> hash
        md5_hash = hashlib.md5(binary_data).hexdigest()
        
        # Determine file extension and name
        mime_elem = res.find("mime")
        mime_type = mime_elem.text.strip() if mime_elem is not None else "application/octet-stream"
        
        filename = None
        attr_elem = res.find("resource-attributes")
        if attr_elem is not None:
            fn_elem = attr_elem.find("file-name")
            if fn_elem is not None and fn_elem.text:
                filename = clean_filename(fn_elem.text.strip())
        
        if not filename:
            # Fallback to hash + default extension for the mime type
            ext = mimetypes.guess_extension(mime_type) or ""
            filename = f"{md5_hash}{ext}"
        
        resources[md5_hash] = {
            "binary_data": binary_data,
            "filename": filename,
            "mime_type": mime_type
        }
        
    return resources

def convert_html_to_md(html_content, resources):
    """Convert HTML content of a note to Markdown, replacing en-media tags with local file links."""
    # Use BeautifulSoup and markdownify if available, otherwise fallback to basic string replacements
    try:
        from bs4 import BeautifulSoup
        from markdownify import markdownify as md
    except ImportError:
        print("[!] Warning: 'beautifulsoup4' and 'markdownify' not installed.")
        print("[!] Falling back to plain text parser. For rich Markdown output, please run: pip install beautifulsoup4 markdownify")
        # Simple plain-text fallback
        text = re.sub('<[^<]+?>', '', html_content)
        return text

    # Parse note XHTML
    soup = BeautifulSoup(html_content, "html.parser")
    
    # Process <en-media> tags (attachments/images)
    for media in soup.find_all("en-media"):
        hash_val = media.get("hash")
        if hash_val and hash_val in resources:
            res_info = resources[hash_val]
            filename = res_info["filename"]
            mime = res_info["mime_type"]
            
            # Relative path to resource from the notebook's folder
            # Since notebook folder is c:/{user}/ever_md/markdown/<NotebookName>/
            # and resources are in c:/{user}/ever_md/markdown/_resources/
            # relative path is ../_resources/<filename>
            relative_path = f"../_resources/{filename}"
            
            # Replace tag with markdown image or link
            if mime.startswith("image/"):
                new_tag = soup.new_tag("img", src=relative_path, alt=filename)
            else:
                new_tag = soup.new_tag("a", href=relative_path)
                new_tag.string = f"Attachment: {filename}"
            
            media.replace_with(new_tag)
        else:
            media.decompose() # Remove invalid/empty media tags
            
    # Convert XHTML to Markdown
    markdown_text = md(str(soup), heading_style="ATX")
    return markdown_text

def convert_enex_to_markdown():
    """Convert all .enex files in the export folder into beautiful local Markdown files with attachments."""
    ensure_directories()
    check_and_sanitize_notebooks()
    enex_files = list(ENEX_DIR.glob("*.enex"))
    
    if not enex_files:
        print(f"[-] No .enex files found in {ENEX_DIR}. Please run 'export' first.")
        return

    print(f"[*] Starting conversion of {len(enex_files)} .enex file(s) to Markdown...")
    
    for enex_path in enex_files:
        notebook_name = enex_path.stem
        notebook_dir = MARKDOWN_DIR / clean_filename(notebook_name)
        notebook_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"[*] Parsing notebook: {notebook_name}...")
        
        try:
            # Parse ENEX XML
            # Some ENEX files contain special entities or block symbols, using a robust parser
            tree = ET.parse(enex_path)
            root = tree.getroot()
        except Exception as e:
            print(f"[-] Error parsing XML for notebook {notebook_name}: {e}")
            continue
            
        note_count = 0
        for note in root.findall("note"):
            title_elem = note.find("title")
            title = title_elem.text.strip() if title_elem is not None and title_elem.text else "Untitled"
            clean_title = clean_filename(title)
            
            # Extract tags
            tags = [t.text.strip() for t in note.findall("tag") if t.text]
            
            # Parse note content (XHTML inside CDATA)
            content_elem = note.find("content")
            if content_elem is None or content_elem.text is None:
                continue
                
            content_xml = content_elem.text.strip()
            
            # Parse resources (attachments)
            resources = parse_resources(note)
            
            # Save resources physically
            for md5_hash, res_info in resources.items():
                dest_path = RESOURCES_DIR / res_info["filename"]
                try:
                    dest_path.write_bytes(res_info["binary_data"])
                except Exception as e:
                    print(f"[-] Failed to save attachment {res_info['filename']}: {e}")
            
            # Convert HTML to Markdown
            markdown_content = convert_html_to_md(content_xml, resources)
            
            # Add metadata block (Front Matter)
            front_matter = "---\n"
            front_matter += f"title: \"{title.replace('&', '&amp;').replace('\"', '\\\"')}\"\n"
            if tags:
                front_matter += f"tags: {tags}\n"
            
            created_elem = note.find("created")
            if created_elem is not None and created_elem.text:
                front_matter += f"created: {created_elem.text}\n"
                
            updated_elem = note.find("updated")
            if updated_elem is not None and updated_elem.text:
                front_matter += f"updated: {updated_elem.text}\n"
                
            front_matter += "---\n\n"
            
            full_content = front_matter + markdown_content
            
            # Handle duplicate titles by adding a counter
            note_file_path = notebook_dir / f"{clean_title}.md"
            counter = 1
            while note_file_path.exists():
                note_file_path = notebook_dir / f"{clean_title}_{counter}.md"
                counter += 1
                
            try:
                note_file_path.write_text(full_content, encoding="utf-8")
                note_count += 1
            except Exception as e:
                print(f"[-] Failed to write note '{title}': {e}")
                
        print(f"[+] Converted {note_count} notes for notebook '{notebook_name}'.")
        
    print(f"[+] All conversions finished! Markdown files saved in: {MARKDOWN_DIR}")

def check_dependencies():
    """Check if required python packages are installed."""
    deps = {
        "evernote-backup": False,
        "beautifulsoup4 (bs4)": False,
        "markdownify": False
    }
    
    try:
        import evernote_backup
        deps["evernote-backup"] = True
    except ImportError:
        pass
        
    try:
        import bs4
        deps["beautifulsoup4 (bs4)"] = True
    except ImportError:
        pass
        
    try:
        import markdownify
        deps["markdownify"] = True
    except ImportError:
        pass
        
    all_ok = all(deps.values())
    return deps, all_ok

def check_database():
    """Inspect local SQLite database for synced tables and content."""
    # 1. Check token status in token_bk.db
    token_exists = DB_PATH.exists()
    token_size_mb = DB_PATH.stat().st_size / (1024 * 1024) if token_exists else 0
    token_tables = {}
    token_ok = False
    
    if token_exists:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row[0] for row in cursor.fetchall()]
            for table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM [{table}];")
                count = cursor.fetchone()[0]
                token_tables[table] = count
            conn.close()
            # If config table has rows, token is OK
            if token_tables.get("config", 0) > 0:
                token_ok = True
        except Exception as e:
            token_tables["error"] = str(e)
            
    # 2. Check note status in note.db
    note_exists = NOTE_DB_PATH.exists()
    note_size_mb = NOTE_DB_PATH.stat().st_size / (1024 * 1024) if note_exists else 0
    note_tables = {}
    notes_count = 0
    notebooks_count = 0
    
    if note_exists:
        try:
            conn = sqlite3.connect(NOTE_DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row[0] for row in cursor.fetchall()]
            for table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM [{table}];")
                count = cursor.fetchone()[0]
                note_tables[table] = count
            conn.close()
            notes_count = note_tables.get("notes", 0)
            notebooks_count = note_tables.get("notebooks", 0)
        except Exception as e:
            note_tables["error"] = str(e)
            
    return {
        "token_exists": token_exists,
        "token_size_mb": token_size_mb,
        "token_tables": token_tables,
        "token_ok": token_ok,
        "note_exists": note_exists,
        "note_size_mb": note_size_mb,
        "note_tables": note_tables,
        "notes_count": notes_count,
        "notebooks_count": notebooks_count
    }

def check_exports():
    """Check exported .enex files count."""
    if not ENEX_DIR.exists():
        return 0, []
    enex_files = list(ENEX_DIR.glob("*.enex"))
    return len(enex_files), [f.name for f in enex_files]

def check_markdown():
    """Check converted markdown notebooks and files."""
    if not MARKDOWN_DIR.exists():
        return 0, 0
    notebooks = [d for d in MARKDOWN_DIR.iterdir() if d.is_dir() and d.name != "_resources"]
    total_md = sum(len(list(d.glob("*.md"))) for d in notebooks)
    return len(notebooks), total_md

def print_status_report():
    """Analyze the execution environment and print a status report in standard English."""
    print("==================================================================")
    print("           [Evernote Backup Tool] Environment Status Analysis")
    print("==================================================================")
    
    # 1. Dependencies Check (Step 1)
    deps, deps_ok = check_dependencies()
    print("[1] Step 1: Python Libraries Dependency Verification")
    for dep, status in deps.items():
        status_str = "Installed" if status else "MISSING"
        print(f"    - {dep:<25} : [{status_str}]")
    
    if deps_ok:
        step1_action = "[All OK] Required libraries are fully installed."
    else:
        step1_action = "[Required Action] Missing dependencies found. Run library installer first."
    print(f"    --> Diagnostic Recommendation : {step1_action}")
    print()
    
    # 2. Database & OAuth Security Token Check (Step 2)
    db_status = check_database()
    print("[2] Step 2: Evernote API Security Token & Local Authentication Status")
    step2_needed = not db_status["token_ok"]
    if db_status["token_exists"]:
        if "error" in db_status["token_tables"]:
            print(f"    - Token DB Status   : Error occurred ({db_status['token_tables']['error']})")
        elif db_status["token_ok"]:
            print(f"    - Token DB Status   : Valid local authentication token found (token_bk.db)")
            print(f"    - Token DB File Size: {db_status['token_size_mb']:.2f} MB")
        else:
            print("    - Token DB Status   : Database exists but token record is missing.")
    else:
        print("    - Token DB Status   : Local authentication database (token_bk.db) is missing.")
        
    if not step2_needed:
        step2_action = "[All OK] Evernote secure login token is safely stored locally in token_bk.db."
    else:
        step2_action = "[Required Action] No security token exists. Please perform Step 2 OAuth login authentication."
    print(f"    --> Diagnostic Recommendation : {step2_action}")
    print()
    
    # 3. Data Sync & Markdown Conversion Check (Step 3)
    enex_count, _ = check_exports()
    nb_count, md_count = check_markdown()
    
    print("[3] Step 3: Local Database Sync & Markdown Conversion Status")
    if db_status["note_exists"]:
        print(f"    - Local Database Notes: {db_status['notes_count']} notes ({db_status['notebooks_count']} notebooks inside note.db)")
        print(f"    - Local DB File Size  : {db_status['note_size_mb']:.2f} MB")
    else:
        print("    - Local Database Notes: 0 notes found (Database note.db missing/empty)")
        
    print(f"    - Exported ENEX Files : {enex_count} notebooks exported successfully")
    print(f"    - Converted Markdown  : {md_count} notes converted ({nb_count} notebooks)")
    
    if step2_needed:
        step3_action = "[Blocked] Evernote login (Step 2) must be authorized before download/sync can begin."
    elif not db_status["note_exists"] or db_status["notes_count"] == 0:
        step3_action = "[Required Action] Security token is valid but local database note.db is empty.\n                              Execute One-Click Full Backup or Sync to perform initial download."
    elif enex_count == 0 or md_count == 0:
        step3_action = "[Recommended Action] Notes are downloaded locally, but reader files (.md) have not been compiled.\n                              Run One-Click Full Backup or Convert menu."
    else:
        step3_action = "[All OK] All notes are fully backed up and converted to Markdown.\n                              Run Sync to incrementally retrieve the latest remote edits."
    print(f"    --> Diagnostic Recommendation : {step3_action}")
    print("==================================================================")

def main():
    # If no arguments or "status" is provided, print the status report
    action = sys.argv[1].lower() if len(sys.argv) >= 2 else "status"
    
    if action == "status":
        print_status_report()
        print("\nUsage:")
        print("  python backup.py init      - Initialize Evernote local DB (OAuth Browser authentication)")
        print("  python backup.py sync      - Sync latest notes from Evernote to Local DB")
        print("  python backup.py export    - Export notes from Local DB to .enex files")
        print("  python backup.py convert   - Convert exported .enex files to beautiful Markdown with attachments")
        print("  python backup.py all       - Run sync + export + convert sequentially")
        print("  python backup.py status    - Print this environment status report")
    elif action == "init":
        init_db()
    elif action == "sync":
        sync_db()
    elif action == "export":
        export_enex()
    elif action == "convert":
        convert_enex_to_markdown()
    elif action == "all":
        sync_db()
        export_enex()
        convert_enex_to_markdown()
    else:
        print(f"[-] Unknown command: {action}")
        sys.exit(1)

if __name__ == "__main__":
    main()
