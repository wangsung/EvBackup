import os
import sys
import queue
import threading
import subprocess
import socket
import webbrowser
import json
from flask import Flask, jsonify, render_template, Response, request

# Define base paths and import checking functions from backup.py
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import backup
from backup import check_dependencies, check_database, check_exports, check_markdown

app = Flask(__name__)

# Register MDBrowser Blueprint
from mdbrowser.routes import browser_bp
app.register_blueprint(browser_bp, url_prefix='/browser')

# Register Jinja2 Translation Context Processor
def load_translations(lang):
    filepath = os.path.join(os.path.dirname(__file__), 'i18n', f'{lang}.json')
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        fallback_path = os.path.join(os.path.dirname(__file__), 'i18n', 'ko.json')
        try:
            with open(fallback_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

@app.context_processor
def inject_translations():
    lang = request.cookies.get('lang') or request.accept_languages.best_match(['ko', 'en']) or 'ko'
    if lang not in ['ko', 'en']:
        lang = 'ko'
    return dict(t=load_translations(lang), current_lang=lang)

# Global thread-safe process runner
class ProcessRunner:
    def __init__(self):
        self.process = None
        self.log_queue = queue.Queue()
        self.is_running = False
        self.current_action = None
        
    def run(self, action):
        if self.is_running:
            return False
            
        self.is_running = True
        self.current_action = action
        self.log_queue = queue.Queue()  # Clear old logs
        
        is_windows = os.name == 'nt'
        
        def worker():
            try:
                if action == "init":
                    # Start a new visible, interactive CMD terminal window for OAuth / login
                    # This satisfies sys.stdin.isatty() so evernote-backup does not crash with "OAuth requires user input!"
                    if is_windows:
                        cmd = f'start "Evernote Login" cmd /c "{sys.executable} backup.py init & pause"'
                        self.log_queue.put("[START] 로그인 인증 창(CMD)이 새로 실행되었습니다.\n")
                        self.log_queue.put("[*] 화면에 새로 열린 검은색 커맨드(CMD) 창에서 로그인 절차를 수행해 주십시오.\n")
                        self.log_queue.put("[*] 에버노트 인증 창(브라우저)이 열리면 동의를 완료해 주십시오.\n")
                        self.log_queue.put("[*] 인증 완료 후 커맨드 창을 닫으면 대시보드가 자동으로 갱신됩니다.\n")
                        self.process = subprocess.Popen(cmd, shell=True)
                        self.process.wait()
                    else:
                        # Fallback for non-Windows systems
                        cmd = [sys.executable, "backup.py", "init"]
                        self.log_queue.put("[START] 로그인 인증 프로세스가 실행되었습니다.\n")
                        self.process = subprocess.Popen(cmd)
                        self.process.wait()
                        
                    # Verify if token_bk.db was successfully created and populated
                    if backup.DB_PATH.exists() and backup.DB_PATH.stat().st_size > 0:
                        self.log_queue.put("[SUCCESS] 에버노트 인증 보안 토큰이 token_bk.db에 성공적으로 저장되었습니다!\n")
                    else:
                        self.log_queue.put("[ERROR] 에버노트 로그인 인증에 실패했거나 취소되었습니다. (token_bk.db 생성되지 않음)\n")
                else:
                    # Background non-interactive process running for sync/export/convert/all
                    cmd = [sys.executable, "backup.py", action]
                    self.process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,  # Line buffered
                        shell=is_windows
                    )
                    self.log_queue.put(f"[START] 백업 작업('{action}')이 비동기 스레드에서 시작되었습니다.\n")
                    
                    while True:
                        line = self.process.stdout.readline()
                        if not line and self.process.poll() is not None:
                            break
                        if line:
                            self.log_queue.put(line)
                            
                    rc = self.process.poll()
                    if rc == 0:
                        self.log_queue.put(f"[SUCCESS] 백업 작업('{action}')이 성공적으로 완료되었습니다!\n")
                    else:
                        self.log_queue.put(f"[ERROR] 백업 작업('{action}')이 실패하였습니다. (에러 코드: {rc})\n")
            except Exception as e:
                self.log_queue.put(f"[ERROR] 프로세스 실행 중 내부 예외 발생: {str(e)}\n")
            finally:
                self.is_running = False
                self.process = None
                self.current_action = None
                self.log_queue.put(None)  # Sentinel to close SSE stream
                
        threading.Thread(target=worker, daemon=True).start()
        return True

runner = ProcessRunner()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    if request.method == 'GET':
        return jsonify({
            "base_backup_dir": str(backup.BASE_BACKUP_DIR).replace("\\", "/")
        })
    elif request.method == 'POST':
        data = request.get_json() or {}
        new_dir = data.get("base_backup_dir")
        if not new_dir:
            return jsonify({"success": False, "error": "폴더 경로가 입력되지 않았습니다."}), 400
        
        # Normalize and keep forward slash representation for consistency
        new_dir = os.path.normpath(input_path := new_dir.strip()).replace("\\", "/")
        
        # Check and strip quotes from the folder path to prevent browser loading errors
        has_quotes = "'" in new_dir or '"' in new_dir
        if has_quotes:
            new_dir = new_dir.replace("'", "").replace('"', "")
            
        try:
            # Overwrite configuration file config.json
            config_data = {"base_backup_dir": new_dir}
            backup.CONFIG_PATH.write_text(json.dumps(config_data, indent=4), encoding="utf-8")
            
            # Re-evaluate path bindings in memory in the backup module
            backup.BASE_BACKUP_DIR = backup.Path(new_dir)
            backup.DB_DIR = backup.BASE_BACKUP_DIR / "db"
            backup.ENEX_DIR = backup.BASE_BACKUP_DIR / "enex"
            backup.MARKDOWN_DIR = backup.BASE_BACKUP_DIR / "markdown"
            backup.RESOURCES_DIR = backup.MARKDOWN_DIR / "_resources"
            backup.DB_PATH = backup.DB_DIR / "token_bk.db"
            backup.NOTE_DB_PATH = backup.DB_DIR / "note.db"
            
            # Recheck directories
            backup.ensure_directories()
            
            msg = "백업 폴더 경로가 변경 및 저장되었습니다."
            if has_quotes:
                msg = "[자동 교정 완료] 폴더 경로명에 포함된 따옴표(')는 브라우저 오류 예방을 위해 자동 제거 및 교정되었습니다."
            
            return jsonify({
                "success": True,
                "message": msg,
                "base_backup_dir": new_dir
            })
        except Exception as e:
            return jsonify({"success": False, "error": f"경로 변경 실패: {str(e)}"}), 500

@app.route('/api/browse_folder', methods=['POST'])
def api_browse_folder():
    import subprocess
    import sys
    import backup
    
    # Retrieve language from cookies or browser headers
    lang = request.cookies.get('lang') or request.accept_languages.best_match(['ko', 'en']) or 'ko'
    if lang not in ['ko', 'en']:
        lang = 'ko'
        
    dialog_title = "Select Backup Storage Folder" if lang == 'en' else "백업 저장소 폴더 선택"
    cancel_msg = "Directory selection cancelled." if lang == 'en' else "폴더 선택이 취소되었습니다."
    timeout_msg = "Directory selection timed out." if lang == 'en' else "폴더 선택 대기 시간이 초과되었습니다."
    error_msg_prefix = "Failed to open folder picker: " if lang == 'en' else "폴더 선택 창 열기 실패: "
    
    initial_dir = str(backup.BASE_BACKUP_DIR).replace("\\", "/")
    script = f"""
import tkinter as tk
from tkinter import filedialog
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
selected = filedialog.askdirectory(initialdir='{initial_dir}', title='{dialog_title}')
print(selected)
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=120
        )
        selected_path = result.stdout.strip()
        if selected_path:
            return jsonify({"success": True, "selected_path": selected_path})
        else:
            return jsonify({"success": False, "error": cancel_msg})
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "error": timeout_msg}), 408
    except Exception as e:
        return jsonify({"success": False, "error": f"{error_msg_prefix}{str(e)}"}), 500

@app.route('/api/status')
def api_status():
    deps, deps_ok = check_dependencies()
    db_status = check_database()
    enex_count, _ = check_exports()
    nb_count, md_count = check_markdown()
    
    return jsonify({
        "dependencies": {
            "packages": deps,
            "ok": deps_ok
        },
        "database": db_status,
        "exports": {
            "count": enex_count
        },
        "markdown": {
            "notebooks": nb_count,
            "notes": md_count
        },
        "active": runner.is_running,
        "active_action": runner.current_action
    })

@app.route('/api/run', methods=['POST'])
def api_run():
    data = request.get_json() or {}
    action = data.get('action')
    if not action:
        return jsonify({"success": False, "error": "액션이 제공되지 않았습니다."}), 400
        
    if action not in ['init', 'sync', 'export', 'convert', 'all']:
        return jsonify({"success": False, "error": "지원하지 않는 액션입니다."}), 400
        
    success = runner.run(action)
    if success:
        return jsonify({"success": True, "message": f"'{action}' 작업이 성공적으로 실행되었습니다."})
    else:
        return jsonify({"success": False, "error": "이미 다른 백업 프로세스가 실행 중입니다."}), 409

@app.route('/api/stream')
def api_stream():
    def event_stream():
        # Clear/ignore any old logs that might be stuck
        while True:
            try:
                line = runner.log_queue.get(timeout=20)
                if line is None:
                    break
                yield f"data: {line}\n\n"
            except queue.Empty:
                yield "data: :keep-alive\n\n"
                if not runner.is_running:
                    break
    return Response(event_stream(), mimetype='text/event-stream')

@app.route('/api/launch_browser', methods=['POST'])
def api_launch_browser():
    url = "http://127.0.0.1:5001/browser/"
    try:
        webbrowser.open(url)
        return jsonify({"success": True, "message": "MD 브라우저가 호출되었습니다."})
    except Exception as e:
        return jsonify({"success": False, "error": f"브라우저 실행 실패: {str(e)}"}), 500

@app.route('/api/open_explorer', methods=['POST'])
def api_open_explorer():
    path = str(backup.MARKDOWN_DIR)
    if os.name == 'nt':
        try:
            subprocess.Popen(['explorer', path.replace('/', '\\')])
            return jsonify({"success": True, "message": "로컬 백업 폴더가 성공적으로 실행되었습니다."})
        except Exception as e:
            return jsonify({"success": False, "error": f"폴더 열기 실패: {str(e)}"}), 500
    else:
        return jsonify({"success": False, "error": "윈도우 탐색기 전용 명령입니다."}), 400

if __name__ == '__main__':
    # Running server locally on port 5001
    print("[+] Starting Evernote BackupManager Web Server on http://127.0.0.1:5001")
    app.run(host='127.0.0.1', port=5001, debug=True)
