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
    default_dir = "C:/_My2026/_EVERBK"
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
    print("[*] [백업 파일 안전성 확인] 노트북 이름 및 특수 문자 포함 검사 중...")
    
    # Check if BASE_BACKUP_DIR contains quotes
    if "'" in str(BASE_BACKUP_DIR) or '"' in str(BASE_BACKUP_DIR):
        print("[!] 경고: 로컬 백업 폴더 경로명에 따옴표(' 또는 \")가 포함되어 있습니다.")
        print("    --> 브라우저 로드 오류 방지를 위해, 경로에서 따옴표를 제거하여 자동 교정하시기를 강력 권장합니다.")
        
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
                        print(f"[!] 경고: 에버노트 노트북 이름 '{name}'에 따옴표(') 또는 부적합한 특수문자가 포함되어 있습니다.")
                        print(f"    --> 파일 시스템 오류 및 브라우저 호환성을 위해 '{cleaned}' 디렉토리명으로 자동 교정되어 백업 및 변환됩니다.")
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
                print(f"[!] 경고: ENEX 파일 이름 '{name}'에 따옴표(') 또는 부적합한 특수문자가 포함되어 있습니다.")
                print(f"    --> 마크다운 변환 시 '{cleaned}' 디렉토리명으로 자동 교정되어 저장됩니다.")
                has_issues = True
                
    if has_issues:
        print("[*] 노트북/폴더 이름 안전성 검증 및 자동 교정 안내가 완료되었습니다.")
    else:
        print("[+] 검사 완료: 파일 이름 안전성에 이상이 없으며 백업 폴더와 완벽히 매칭됩니다.")
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
            # Since notebook folder is C:/_My2026/_EVERBK/markdown/<NotebookName>/
            # and resources are in C:/_My2026/_EVERBK/markdown/_resources/
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
    """Analyze the execution environment and print a status report in user-friendly Korean."""
    print("==================================================================")
    print("           [에버노트 로컬 백업 도구] 실행 환경 분석 및 가이드")
    print("==================================================================")
    
    # 1. Dependencies Check (Step 1)
    deps, deps_ok = check_dependencies()
    print("[1] 1단계: 파이썬 필수 라이브러리 설치 여부")
    for dep, status in deps.items():
        status_str = "설치됨 (Installed)" if status else "미설치 (MISSING)"
        print(f"    - {dep:<25} : [{status_str}]")
    
    if deps_ok:
        step1_action = "[실행 불필요] 백업에 필요한 필수 파이썬 라이브러리가 이미 완벽하게 설치되어 있습니다."
    else:
        step1_action = "[실행 권장] 라이브러리가 일부 누락되었습니다. [1]번 메뉴를 실행하여 라이브러리를 먼저 설치하십시오."
    print(f"    --> 진단 및 추천 가이드 : {step1_action}")
    print()
    
    # 2. Database & OAuth Security Token Check (Step 2)
    db_status = check_database()
    print("[2] 2단계: 에버노트 API 보안 토큰 및 로그인 인증 상태")
    step2_needed = not db_status["token_ok"]
    if db_status["token_exists"]:
        if "error" in db_status["token_tables"]:
            print(f"    - 토큰 DB 상태      : 오류 발생 ({db_status['token_tables']['error']})")
        elif db_status["token_ok"]:
            print(f"    - 토큰 DB 상태      : 인증 보안 토큰 유효 (token_bk.db)")
            print(f"    - 토큰 DB 파일 크기 : {db_status['token_size_mb']:.2f} MB")
        else:
            print("    - 토큰 DB 상태      : 파일은 존재하나 토큰 정보가 없습니다.")
    else:
        print("    - 토큰 DB 상태      : 로컬 인증 토큰 DB(token_bk.db)가 없습니다.")
        
    if not step2_needed:
        step2_action = "[실행 불필요] 이미 에버노트가 발행한 로그인 보안 토큰이 token_bk.db에 안전하게 저장되어 있습니다.\n                              (토큰 만료 전까지는 [2]번 로그인을 다시 하실 필요가 없습니다.)"
    else:
        step2_action = "[실행 필수] 로컬 로그인 보안 토큰이 없습니다.\n                              Evernote가 발행한 보안 토큰이 필요하므로, [2]번 메뉴를 실행해 브라우저 로그인을 완료해 주십시오."
    print(f"    --> 진단 및 추천 가이드 : {step2_action}")
    print()
    
    # 3. Data Sync & Markdown Conversion Check (Step 3)
    enex_count, _ = check_exports()
    nb_count, md_count = check_markdown()
    
    print("[3] 3단계: 로컬 데이터 동기화 및 마크다운 파일 변환 상태")
    if db_status["note_exists"]:
        print(f"    - 로컬 DB 노트 수   : {db_status['notes_count']}개 노트 (노트북 {db_status['notebooks_count']}개 - note.db)")
        print(f"    - 로컬 DB 파일 크기 : {db_status['note_size_mb']:.2f} MB")
    else:
        print("    - 로컬 DB 노트 수   : 0개 (다운로드 진행 안 됨)")
        
    print(f"    - ENEX 내보내기 파일 : {enex_count}개 노트북 내보내기 완료")
    print(f"    - 마크다운 변환 파일 : {md_count}개 노트 변환 완료 (노트북 {nb_count}개)")
    
    if step2_needed:
        step3_action = "[실행 일시정지] 2단계 에버노트 보안 토큰 발급([2]번 메뉴 실행)이 완료되어야 백업을 진행할 수 있습니다."
    elif not db_status["note_exists"] or db_status["notes_count"] == 0:
        step3_action = "[실행 필수] 보안 토큰은 유효하나 note.db에 다운로드된 노트 데이터가 전혀 없습니다.\n                              [3]번 메뉴(전체 백업) 또는 [4]번 메뉴(동기화)를 즉시 실행하여 최초 전체 다운로드를 받으십시오."
    elif enex_count == 0 or md_count == 0:
        step3_action = "[실행 권장] 노트를 note.db로는 내려받았으나, 뷰어에서 읽을 수 있는 마크다운 파일로 변환하지 않았습니다.\n                              [3]번 메뉴(전체 백업) 또는 [6]번 메뉴(마크다운 변환)를 실행해 주십시오."
    else:
        step3_action = "[실행 선택] 현재 모든 백업과 마크다운 변환이 정상 완료된 상태입니다.\n                              에버노트 클라우드의 최신 변경사항(추가/수정/삭제)만 반영하려면 [3]번 메뉴를 통해 증분 백업을 수행하십시오."
    print(f"    --> 진단 및 추천 가이드 : {step3_action}")
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
