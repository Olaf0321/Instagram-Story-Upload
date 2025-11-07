from pathlib import Path
import mimetypes
import time
import os
import csv
from datetime import datetime
from instagrapi import Client
from instagrapi.types import StoryLink
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog
import threading
import sys
from tkinter import filedialog

SESSION_FOLDER = "sessions"  # Folder to store session files
STATUS_FOLDER = "status_reports"  # Folder to store status report files

class TextRedirector:
    """Redirects stdout/stderr to GUI text widget."""
    def __init__(self, widget):
        self.widget = widget

    def write(self, text):
        self.widget.configure(state='normal')
        self.widget.insert(tk.END, text)
        self.widget.see(tk.END)
        self.widget.configure(state='disabled')
        self.widget.update_idletasks()

    def flush(self):
        pass

def debug_log(message, level="INFO"):
    """Print debug messages with timestamp and level (Japanese)."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Map English levels to Japanese
    level_map = {
        "INFO": "情報",
        "DEBUG": "デバッグ",
        "SUCCESS": "成功",
        "WARNING": "警告",
        "ERROR": "エラー"
    }

    jp_level = level_map.get(level.upper(), level)  # fallback to original if not found
    print(f"[{timestamp}] [{jp_level}] {message}")

def login_with_session(username, password):
    """Login with session support and 2FA handling."""
    cl = Client()
    
    # Set user agent to avoid detection
    cl.delay_range = [3, 7]  # Random delay between requests
    
    # Create sessions folder if it doesn't exist
    if not os.path.exists(SESSION_FOLDER):
        os.makedirs(SESSION_FOLDER)
        debug_log(f"Created sessions folder: {SESSION_FOLDER}", "DEBUG")
    
    session_file = os.path.join(SESSION_FOLDER, f"{username}_session.json")
    
    if os.path.exists(session_file):
        try:
            cl.load_settings(session_file)
            cl.get_timeline_feed()
            debug_log(f"Logged in using saved session for {username}", "SUCCESS")
            return cl
        except Exception as e:
            debug_log(f"Session invalid for {username}: {str(e)}", "WARNING")
            debug_log(f"Removing invalid session file", "DEBUG")
            os.remove(session_file)
    else:
        debug_log(f"No existing session file found for {username}", "INFO")
    
    # Fresh login
    try:
        debug_log(f"Attempting fresh login for {username}...", "INFO")
        cl.login(username, password)
        debug_log("Login successful!", "SUCCESS")
        
        cl.dump_settings(session_file)
        debug_log(f"New session saved to {session_file}", "SUCCESS")
        return cl
        
    except Exception as e:
        error_str = str(e).lower()
        debug_log(f"Login exception occurred: {str(e)}", "ERROR")
        
        if "two_factor_required" in error_str:
            debug_log(f"  Two-factor authentication required for {username}", "WARNING")
            print(f"\n  2FA REQUIRED: Please enter the code in the console window.")
            verification_code = input(f"Enter the 6-digit verification code for {username}: ")
            debug_log(f"Received verification code, attempting 2FA login...", "INFO")
            
            cl.two_factor_login(username, password, verification_code)
            debug_log("2FA login successful!", "SUCCESS")
            
            cl.dump_settings(session_file)
            debug_log(f"  2FA session saved for {username}", "SUCCESS")
            return cl
        
        debug_log(f"Login failed with error: {str(e)}", "ERROR")
        raise

def upload_story_with_retry(cl, username, password, file_path, mime_type, caption, link_url=None):
    """Upload with automatic retry on session expiry."""
    debug_log(f"{username} のストーリー投稿を準備中...", "情報")
    
    links = []
    if link_url and link_url.strip():
        links = [StoryLink(webUri=link_url)]
        debug_log(f"Story link added: {link_url}", "DEBUG")
    else:
        debug_log("リンクURLが指定されていません", "デバッグ")
    
    try:
        if mime_type.startswith("image/"):
            debug_log(f"Uploading image: {file_path.name}", "INFO")
            cl.photo_upload_to_story(str(file_path), caption=caption, links=links)
            debug_log("Story uploaded successfully!", "SUCCESS")
            
        elif mime_type.startswith("video/"):
            debug_log(f"Uploading video: {file_path.name}", "INFO")
            cl.video_upload_to_story(str(file_path), caption=caption, links=links)
            debug_log("Story (video) uploaded successfully!", "SUCCESS")
        else:
            error_msg = f"対応していないファイルタイプです: {mime_type}"
            debug_log(error_msg, "ERROR")
            raise ValueError(f"  {error_msg}")
            
    except Exception as e:
        error_msg = str(e).lower()
        debug_log(f"アップロード例外が発生しました: {str(e)}", "ERROR")
        
        if "login_required" in error_msg or "403" in error_msg:
            debug_log("セッション期限切れを検出 — 再ログイン中...", "警告")
            
            session_file = os.path.join(SESSION_FOLDER, f"{username}_session.json")
            if os.path.exists(session_file):
                debug_log(f"Removing expired session file", "DEBUG")
                os.remove(session_file)
            
            debug_log("再ログインを試みています...", "情報")
            cl = login_with_session(username, password)
            
            debug_log("Waiting 2 seconds before retry...", "DEBUG")
            time.sleep(2)
            
            # Retry upload
            debug_log("Retrying upload after session refresh...", "INFO")
            if mime_type.startswith("video/"):
                cl.video_upload_to_story(str(file_path), caption=caption, links=links)
            else:
                cl.photo_upload_to_story(str(file_path), caption=caption, links=links)
            debug_log("  Story uploaded successfully after refresh!", "SUCCESS")
        else:
            debug_log(f"  Upload failed with non-session error: {e}", "ERROR")
            raise

def process_selected_accounts(selected_rows, csv_file="accounts.csv"):
    """Process only selected accounts from CSV file and post TWO stories per account."""
    debug_log(f"Starting processing for {len(selected_rows)} selected accounts", "INFO")
    
    if not os.path.exists(csv_file):
        debug_log(f"CSV file '{csv_file}' not found!", "ERROR")
        return None
    
    # Create status reports folder if it doesn't exist
    if not os.path.exists(STATUS_FOLDER):
        os.makedirs(STATUS_FOLDER)
        debug_log(f"Created status reports folder: {STATUS_FOLDER}", "DEBUG")
    
    # Read all rows from CSV
    rows = []
    with open(csv_file, 'r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames
        
        # Add 'status' column if it doesn't exist
        if 'status' not in fieldnames:
            fieldnames = list(fieldnames) + ['status']
        
        for row in reader:
            rows.append(row)
    
    debug_log(f"Total accounts in CSV: {len(rows)}", "INFO")
    
    # Process only selected accounts
    for row_index in selected_rows:
        i = row_index
        row = rows[i]
        
        username = row.get('username', '').strip()
        password = row.get('password', '').strip()
        post_file_no_link = row.get('post_file_no_link', '').strip()  # NEW: Story 1 file
        post_file = row.get('post_file', '').strip()  # Story 2 file (with link)
        post_caption = row.get('post_caption', '').strip()
        link_url = row.get('link_url', '').strip()
        
        debug_log(f"\n{'='*60}", "INFO")
        debug_log(f"Processing account {row_index + 1}: {username}", "INFO")
        debug_log(f"{'='*60}", "INFO")
        
        if not username or not password:
            debug_log(f"Skipping row {i+1}: Missing username or password", "WARNING")
            rows[i]['status'] = "Error: Missing credentials"
            continue
        
        try:
            # Login
            cl = login_with_session(username, password)
            time.sleep(2)
            
            stories_posted = 0
            
            # ========================================
            # POST STORY #1: IMAGE WITH LINK
            # ========================================
            if post_file:
                debug_log(f"\n📸 ストーリー#2: リンク付き画像を投稿中...", "情報")
                
                file_path = Path(post_file)
                
                if not file_path.exists():
                    debug_log(f"File not found for Story #2: {post_file}", "ERROR")
                else:
                    mime_type, _ = mimetypes.guess_type(file_path)
                    
                    if not mime_type:
                        debug_log(f"Could not detect file type for Story #2: {file_path}", "ERROR")
                    else:
                        try:
                            if link_url:
                                upload_story_with_retry(cl, username, password, file_path, mime_type, post_caption, link_url)
                                debug_log(f"  Story #1 posted successfully (with link)!", "SUCCESS")
                            else:
                                debug_log(f"  No link URL provided, posting Story #2 without link", "WARNING")
                                upload_story_with_retry(cl, username, password, file_path, mime_type, post_caption, None)
                                debug_log(f"  Story #2 posted successfully (no link available)!", "SUCCESS")
                            
                            stories_posted += 1
                            
                        except Exception as e:
                            debug_log(f"Failed to post Story #2: {str(e)}", "ERROR")
            else:
                debug_log(f"  No file provided for Story #2 (with link), skipping...", "WARNING")

            # ========================================
            # POST STORY #2: IMAGE WITHOUT LINK
            # ========================================
            if post_file_no_link:
                debug_log(f"\n📸 ストーリー#1: リンクなし画像を投稿中...", "情報")
                
                file_path_no_link = Path(post_file_no_link)
                
                if not file_path_no_link.exists():
                    debug_log(f"File not found for Story #1: {post_file_no_link}", "ERROR")
                else:
                    mime_type, _ = mimetypes.guess_type(file_path_no_link)
                    
                    if not mime_type:
                        debug_log(f"Could not detect file type for Story #1: {file_path_no_link}", "ERROR")
                    else:
                        try:
                            upload_story_with_retry(cl, username, password, file_path_no_link, mime_type, post_caption, None)
                            debug_log(f"  Story #2 posted successfully (no link)!", "SUCCESS")
                            stories_posted += 1
                            
                            # Wait between stories
                            delay = 5
                            debug_log(f"Waiting {delay} seconds before posting Story #2...", "DEBUG")
                            time.sleep(delay)
                            
                        except Exception as e:
                            debug_log(f"Failed to post Story #1: {str(e)}", "ERROR")
            else:
                debug_log(f"  No file provided for Story #1 (no link), skipping...", "WARNING")
            
            # Update status based on number of stories posted
            posted_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if stories_posted == 2:
                rows[i]['status'] = f"成功（2件のストーリー） - {posted_time}"
            elif stories_posted == 1:
                rows[i]['status'] = f"部分成功（1件のストーリー） - {posted_time}"
            else:
                rows[i]['status'] = f"エラー：ストーリーが投稿されませんでした - {posted_time}"
            
            debug_log(f"  Posted {stories_posted} stories for {username}", "SUCCESS")
            
            # Delay between accounts
            delay = 10
            debug_log(f"Waiting {delay} seconds before next account...", "DEBUG")
            time.sleep(delay)
            
        except Exception as e:
            error_msg = f"Error: {str(e)[:50]}"
            rows[i]['status'] = error_msg
            debug_log(f"  Failed to process {username}", "ERROR")
            debug_log(f"Full error: {str(e)}", "ERROR")
            continue
    
    # Generate status report
    current_time = datetime.now()
    timestamp_str = current_time.strftime("%Y-%m-%d_%I-%M-%S_%p")
    status_filename = os.path.join(STATUS_FOLDER, f"status_report_{timestamp_str}.csv")
    
    debug_log(f"Creating new status report: {status_filename}", "INFO")
    
    # Write new status report CSV
    with open(status_filename, 'w', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    
    debug_log(f"\n{'='*60}", "INFO")
    # debug_log(f"Status report created: {status_filename}", "SUCCESS")
    # debug_log(f"{'='*60}", "INFO")
    
    # Summary statistics
    success_count = sum(1 for idx in selected_rows if '成功' in rows[idx].get('status', ''))
    error_count = sum(1 for idx in selected_rows if 'エラー' in rows[idx].get('status', ''))
    partial_count = sum(1 for idx in selected_rows if '部分成功' in rows[idx].get('status', ''))

    debug_log(f"集計: 完全成功（2件のストーリー） {success_count} 件, 部分成功（1件のストーリー） {partial_count} 件, エラー {error_count} 件, 処理済み {len(selected_rows)} 件", "INFO")
    
    return status_filename


class AccountDialog(tk.Toplevel):
    """Dialog for adding/editing accounts with separate file fields."""
    def __init__(self, parent, title="Add Account", account_data=None):
        super().__init__(parent)
        self.title(title)
        self.geometry("550x450")
        self.resizable(False, False)
        
        self.result = None
        self.account_data = account_data or {}
        
        # Center the dialog on parent window
        self.transient(parent)
        self.grab_set()
        
        # Create widgets first
        self.create_widgets()
        
        # Center the dialog after widgets are created
        self.update_idletasks()
        
        # Get parent window position and size
        parent_x = parent.winfo_x()
        parent_y = parent.winfo_y()
        parent_width = parent.winfo_width()
        parent_height = parent.winfo_height()
        
        # Get dialog size
        dialog_width = self.winfo_width()
        dialog_height = self.winfo_height()
        
        # Calculate center position
        x = parent_x + (parent_width - dialog_width) // 2
        y = parent_y + (parent_height - dialog_height) // 2
        
        # Set the position
        self.geometry(f"+{x}+{y}")
        
    def create_widgets(self):
        # Define fonts
        label_font = ("Yu Gothic", 10, "bold")
        entry_font = ("Yu Gothic", 10)
        button_font = ("Yu Gothic", 10, "bold")
        
        # Main frame with padding
        main_frame = ttk.Frame(self, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Username
        ttk.Label(main_frame, text="ユーザー名:", font=label_font).grid(row=0, column=0, sticky="w", pady=5)
        self.username_entry = ttk.Entry(main_frame, width=40, font=entry_font)
        self.username_entry.grid(row=0, column=1, pady=5, padx=(10, 0))
        self.username_entry.insert(0, self.account_data.get('username', ''))
        
        # Password
        ttk.Label(main_frame, text="パスワード:", font=label_font).grid(row=1, column=0, sticky="w", pady=5)
        
        password_frame = ttk.Frame(main_frame)
        password_frame.grid(row=1, column=1, pady=5, padx=(10, 0))
        
        self.password_entry = ttk.Entry(password_frame, width=32, show="*", font=entry_font)
        self.password_entry.pack(side=tk.LEFT)
        self.password_entry.insert(0, self.account_data.get('password', ''))
        
        # Show/Hide password button
        self.show_password_var = tk.BooleanVar(value=False)
        self.show_hide_btn = tk.Button(password_frame, text="👁", command=self.toggle_password,
                                       font=entry_font, width=3, cursor="hand2")
        self.show_hide_btn.pack(side=tk.LEFT, padx=(5, 0))
        
        # Story #1 File (No Link)
        ttk.Label(main_frame, text="ストーリー#1（リンクなし）:", font=label_font).grid(row=2, column=0, sticky="w", pady=5)
        
        file_frame_1 = ttk.Frame(main_frame)
        file_frame_1.grid(row=2, column=1, pady=5, padx=(10, 0))
        
        self.post_file_no_link_entry = ttk.Entry(file_frame_1, width=26, font=entry_font)
        self.post_file_no_link_entry.pack(side=tk.LEFT)
        self.post_file_no_link_entry.insert(0, self.account_data.get('post_file_no_link', ''))
        
        # Browse button for Story #1
        browse_btn_1 = tk.Button(file_frame_1, text="参照…", command=lambda: self.browse_file(1),
                              font=("Yu Gothic", 9), width=8, cursor="hand2")
        browse_btn_1.pack(side=tk.LEFT, padx=(5, 0))
        
        # Story #2 File (With Link)
        ttk.Label(main_frame, text="ストーリー#2（リンク付き）:", font=label_font).grid(row=3, column=0, sticky="w", pady=5)
        
        file_frame_2 = ttk.Frame(main_frame)
        file_frame_2.grid(row=3, column=1, pady=5, padx=(10, 0))
        
        self.post_file_entry = ttk.Entry(file_frame_2, width=26, font=entry_font)
        self.post_file_entry.pack(side=tk.LEFT)
        self.post_file_entry.insert(0, self.account_data.get('post_file', ''))
        
        # Browse button for Story #2
        browse_btn_2 = tk.Button(file_frame_2, text="参照…", command=lambda: self.browse_file(2),
                              font=("Yu Gothic", 9), width=8, cursor="hand2")
        browse_btn_2.pack(side=tk.LEFT, padx=(5, 0))
        
        # Caption
        ttk.Label(main_frame, text="キャプション:", font=label_font).grid(row=4, column=0, sticky="nw", pady=5)
        self.caption_text = tk.Text(main_frame, width=30, height=5, wrap=tk.WORD, font=entry_font)
        self.caption_text.grid(row=4, column=1, pady=5, padx=(10, 0))
        self.caption_text.insert("1.0", self.account_data.get('post_caption', ''))
        
        # Link URL
        ttk.Label(main_frame, text="リンクURL:", font=label_font).grid(row=5, column=0, sticky="w", pady=5)
        self.link_url_entry = ttk.Entry(main_frame, width=40, font=entry_font)
        self.link_url_entry.grid(row=5, column=1, pady=5, padx=(10, 0))
        self.link_url_entry.insert(0, self.account_data.get('link_url', ''))
        
        # Buttons
        button_frame = ttk.Frame(main_frame)
        button_frame.grid(row=6, column=0, columnspan=2, pady=20)
        
        save_btn = tk.Button(button_frame, text="保存", command=self.save, 
                            font=button_font, width=12, bg="#0095f6", fg="white",
                            relief="raised", cursor="hand2")
        save_btn.pack(side=tk.LEFT, padx=5)
        
        cancel_btn = tk.Button(button_frame, text="キャンセル", command=self.cancel,
                              font=button_font, width=12, bg="#f0f0f0",
                              relief="raised", cursor="hand2")
        cancel_btn.pack(side=tk.LEFT, padx=5)
        
    def save(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()
        post_file_no_link = self.post_file_no_link_entry.get().strip()
        post_file = self.post_file_entry.get().strip()
        caption = self.caption_text.get("1.0", tk.END).strip()
        link_url = self.link_url_entry.get().strip()
        
        if not username:
            messagebox.showwarning("入力エラー", "ユーザー名は必須です！")
            return
        
        if not password:
            messagebox.showwarning("入力エラー", "パスワードは必須です！")
            return
            
        self.result = {
            'username': username,
            'password': password,
            'post_file_no_link': post_file_no_link,
            'post_file': post_file,
            'post_caption': caption,
            'link_url': link_url
        }
        self.destroy()
        
    def cancel(self):
        self.destroy()
    
    def toggle_password(self):
        """Toggle password visibility."""
        if self.show_password_var.get():
            # Hide password
            self.password_entry.config(show="*")
            self.show_hide_btn.config(text="👁")
            self.show_password_var.set(False)
        else:
            # Show password
            self.password_entry.config(show="")
            self.show_hide_btn.config(text="👁‍🗨")
            self.show_password_var.set(True)
    
    def browse_file(self, story_num):
        """Open file browser to select a post file."""
        # Define file types for images and videos
        filetypes = (
            ('All Media Files', '*.jpg *.jpeg *.png *.gif *.mp4 *.mov *.avi *.mkv'),
            ('Image Files', '*.jpg *.jpeg *.png *.gif'),
            ('Video Files', '*.mp4 *.mov *.avi *.mkv'),
            ('All Files', '*.*')
        )
        
        filename = filedialog.askopenfilename(
            title=f'ストーリー#{story_num}',
            filetypes=filetypes,
            parent=self
        )
        
        if filename:
            if story_num == 1:
                self.post_file_no_link_entry.delete(0, tk.END)
                self.post_file_no_link_entry.insert(0, filename)
            else:
                self.post_file_entry.delete(0, tk.END)
                self.post_file_entry.insert(0, filename)


class InstagramGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("インスタグラムストーリー投稿ツール - デュアルモード")
        self.root.geometry("1400x700")
        
        # Data
        self.accounts = []
        self.csv_file = "accounts.csv"
        
        # Create UI
        self.create_widgets()
        self.load_accounts()
        
    def create_widgets(self):
        # Top frame for buttons with border
        top_frame = ttk.LabelFrame(self.root, text="", padding="15", relief="solid", borderwidth=2)
        top_frame.pack(fill=tk.X, padx=10, pady=10)
        
        # Title and buttons in the same row
        title_label = ttk.Label(top_frame, text="インスタグラムストーリー投稿ツール", font=("Arial", 18, "bold"))
        title_label.pack(side=tk.LEFT, padx=(0, 30))
        
        # Buttons
        button_frame = ttk.Frame(top_frame)
        button_frame.pack(side=tk.RIGHT)
        
        self.add_btn = tk.Button(button_frame, text="アカウント追加", command=self.add_account,
                                 font=("Arial", 10, "bold"), width=12, height=1, bg="#28a745", fg="white",
                                 relief="raised", cursor="hand2")
        self.add_btn.pack(side=tk.LEFT, padx=5)
        
        self.edit_btn = tk.Button(button_frame, text="アカウント編集", command=self.edit_account,
                                  font=("Arial", 10, "bold"), width=12, height=1, bg="#ffc107",
                                  relief="raised", cursor="hand2")
        self.edit_btn.pack(side=tk.LEFT, padx=5)
        
        self.delete_btn = tk.Button(button_frame, text="アカウント削除", command=self.delete_account,
                                    font=("Arial", 10, "bold"), width=12, height=1, bg="#dc3545", fg="white",
                                    relief="raised", cursor="hand2")
        self.delete_btn.pack(side=tk.LEFT, padx=5)
        
        self.select_all_btn = tk.Button(button_frame, text="全選択", command=self.select_all, 
                                         font=("Arial", 10, "bold"), width=12, height=1, bg="#f0f0f0", 
                                         relief="raised", cursor="hand2")
        self.select_all_btn.pack(side=tk.LEFT, padx=5)
        
        self.deselect_all_btn = tk.Button(button_frame, text="全解除", command=self.deselect_all,
                                           font=("Arial", 10, "bold"), width=12, height=1, bg="#f0f0f0",
                                           relief="raised", cursor="hand2")
        self.deselect_all_btn.pack(side=tk.LEFT, padx=5)
        
        self.refresh_btn = tk.Button(button_frame, text="更新", command=self.load_accounts,
                                      font=("Arial", 10, "bold"), width=12, height=1, bg="#f0f0f0",
                                      relief="raised", cursor="hand2")
        self.refresh_btn.pack(side=tk.LEFT, padx=5)
        
        self.post_btn = tk.Button(button_frame, text="ストーリー投稿", command=self.post_stories,
                                   font=("Arial", 10, "bold"), width=12, height=1, bg="#0095f6", fg="white",
                                   relief="raised", cursor="hand2", activebackground="#0081d9")
        self.post_btn.pack(side=tk.LEFT, padx=5)
        
        # Main container - split into two parts
        main_container = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        main_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Top part - Table
        table_frame = ttk.Frame(main_container)
        main_container.add(table_frame, weight=1)
        
       # Create Treeview for table with Japanese columns
        columns = ("選択", "ユーザー名", "ストーリー#1(リンクなし)", "ストーリー#2(リンク付き)", "キャプション", "リンクURL")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="extended")

        # Configure columns (displayed headers in Japanese)
        self.tree.heading("選択", text="☑")
        self.tree.heading("ユーザー名", text="ユーザー名")
        self.tree.heading("ストーリー#1(リンクなし)", text="ストーリー#1(リンクなし)")
        self.tree.heading("ストーリー#2(リンク付き)", text="ストーリー#2(リンク付き)")
        self.tree.heading("キャプション", text="キャプション")
        self.tree.heading("リンクURL", text="リンクURL")

        # Set column widths and alignment
        self.tree.column("選択", width=50, anchor="center")
        self.tree.column("ユーザー名", width=150)
        self.tree.column("ストーリー#1(リンクなし)", width=250)
        self.tree.column("ストーリー#2(リンク付き)", width=250)
        self.tree.column("キャプション", width=250)
        self.tree.column("リンクURL", width=200)
        
        # Scrollbars for table
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        
        # Bind click event for checkbox toggle
        self.tree.bind("<Button-1>", self.on_tree_click)
        
        # Bottom part - Console/Log window
        console_frame = ttk.LabelFrame(main_container, text="コンソール出力", padding="5")
        main_container.add(console_frame, weight=1)
        
        self.console = scrolledtext.ScrolledText(console_frame, wrap=tk.WORD, bg="black", fg="white", font=("Consolas", 9))
        self.console.pack(fill=tk.BOTH, expand=True)
        self.console.configure(state='disabled')
        
        # Redirect stdout to console
        sys.stdout = TextRedirector(self.console)
        
        # Selected items tracking
        self.selected_items = set()
        
    def load_accounts(self):
        """Load accounts from CSV file."""
        self.tree.delete(*self.tree.get_children())
        self.accounts = []
        self.selected_items.clear()
        
        if not os.path.exists(self.csv_file):
            print(f"  CSVファイル '{self.csv_file}' が見つかりません。新規作成します...")
            # Create empty CSV with headers including new column
            with open(self.csv_file, 'w', encoding='utf-8', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=['username', 'password', 'post_file_no_link', 'post_file', 'post_caption', 'link_url', 'status'])
                writer.writeheader()
            return
        
        with open(self.csv_file, 'r', encoding='utf-8') as file:
            reader = csv.DictReader(file)
            for idx, row in enumerate(reader):
                username = row.get('username', '').strip()
                post_file_no_link = row.get('post_file_no_link', '').strip()
                post_file = row.get('post_file', '').strip()
                caption = row.get('post_caption', '').strip()
                link_url = row.get('link_url', '').strip()

                # Truncate caption for display
                display_caption = caption[:40] + "..." if len(caption) > 40 else caption
                
                self.accounts.append(row)
                item_id = self.tree.insert("", tk.END, values=("☐", username, post_file_no_link, post_file, display_caption, link_url))
        
        print(f"  {len(self.accounts)} 件のアカウントを {self.csv_file} から読み込みました")
    
    def save_accounts(self):
        """Save accounts to CSV file."""
        fieldnames = ['username', 'password', 'post_file_no_link', 'post_file', 'post_caption', 'link_url', 'status']
        
        with open(self.csv_file, 'w', encoding='utf-8', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.accounts)
        
        print(f"  {len(self.accounts)} 件のアカウントを '{self.csv_file}' に保存しました")
    
    def add_account(self):
        """Add a new account."""
        dialog = AccountDialog(self.root, title="新しいアカウントを追加")
        self.root.wait_window(dialog)
        
        if dialog.result:
            # Add status field
            dialog.result['status'] = ''
            self.accounts.append(dialog.result)
            self.save_accounts()
            self.load_accounts()
            print(f"  新しいアカウントを追加しました: {dialog.result['username']}")
    
    def edit_account(self):
        """Edit selected account."""
        # Get selected item from tree (only one should be selected for edit)
        selection = self.tree.selection()
        
        if not selection:
            messagebox.showwarning("未選択", "編集するアカウントを選択してください。")
            return
        
        if len(selection) > 1:
            messagebox.showwarning("複数選択", "編集するアカウントを1つだけ選択してください。")
            return
        
        # Get the index of the selected account
        all_items = self.tree.get_children()
        selected_index = all_items.index(selection[0])
        account_data = self.accounts[selected_index]
        
        dialog = AccountDialog(self.root, title="アカウント編集", account_data=account_data)
        self.root.wait_window(dialog)
        
        if dialog.result:
            # Preserve the status from the original account
            dialog.result['status'] = account_data.get('status', '')
            self.accounts[selected_index] = dialog.result
            self.save_accounts()
            self.load_accounts()
            print(f"  アカウントを更新しました: {dialog.result['username']}")
    
    def delete_account(self):
        """Delete selected account(s) based on checkboxes."""
        # Use checkbox selection instead of tree selection
        if not self.selected_items:
            messagebox.showwarning("未選択", "削除したいアカウントのチェックボックスを選択してください。")
            return
        
        # Confirm deletion
        if not messagebox.askyesno("削除確認", f"{len(self.selected_items)} 件のアカウントを削除してもよろしいですか？"):
            return
        
        # Get indices of selected items
        all_items = self.tree.get_children()
        selected_indices = sorted([all_items.index(item) for item in self.selected_items], reverse=True)
        
        # Delete from accounts list (reverse order to maintain indices)
        deleted_usernames = []
        for idx in selected_indices:
            deleted_usernames.append(self.accounts[idx]['username'])
            del self.accounts[idx]
        
        # Clear checkbox selection
        self.selected_items.clear()
        
        self.save_accounts()
        self.load_accounts()
        print(f"  {len(deleted_usernames)} 件のアカウントを削除しました: {', '.join(deleted_usernames)}")
    
    def on_tree_click(self, event):
        """Handle click on tree item."""
        region = self.tree.identify_region(event.x, event.y)
        if region == "cell":
            column = self.tree.identify_column(event.x)
            if column == "#1":  # Select column
                item = self.tree.identify_row(event.y)
                if item:
                    if item in self.selected_items:
                        self.selected_items.remove(item)
                        self.tree.set(item, "選択", "☐")
                    else:
                        self.selected_items.add(item)
                        self.tree.set(item, "選択", "☑")
    
    def select_all(self):
        """Select all accounts."""
        for item in self.tree.get_children():
            self.selected_items.add(item)
            self.tree.set(item, "選択", "☑")
        print(f"  全 {len(self.tree.get_children())} 件のアカウントを選択しました")
    
    def deselect_all(self):
        """Deselect all accounts."""
        for item in self.tree.get_children():
            if item in self.selected_items:
                self.selected_items.remove(item)
            self.tree.set(item, "選択", "☐")
        print("  すべてのアカウントの選択を解除しました")
    
    def post_stories(self):
        """Post stories to selected accounts."""
        if not self.selected_items:
            print(" アカウントが選択されていません！最低1件を選択してください。")
            return
        
        # Get indices of selected items
        all_items = self.tree.get_children()
        selected_indices = [all_items.index(item) for item in self.selected_items]
        
        # Disable buttons during posting
        self.post_btn.configure(state='disabled')
        self.refresh_btn.configure(state='disabled')
        self.select_all_btn.configure(state='disabled')
        self.deselect_all_btn.configure(state='disabled')
        self.add_btn.configure(state='disabled')
        self.edit_btn.configure(state='disabled')
        self.delete_btn.configure(state='disabled')
        
        # Run in separate thread to avoid freezing GUI
        thread = threading.Thread(target=self.post_thread, args=(selected_indices,))
        thread.daemon = True
        thread.start()
    
    def post_thread(self, selected_indices):
        """Thread function for posting stories."""
        try:
            status_file = process_selected_accounts(selected_indices, self.csv_file)
            # if status_file:
                # print(f"\n Status report saved to: {status_file}")
        except Exception as e:
            print(f" Error during posting: {e}")
        finally:
            # Re-enable buttons
            self.root.after(0, self.enable_buttons)
            self.root.after(0, self.load_accounts)
    
    def enable_buttons(self):
        """Re-enable buttons after posting."""
        self.post_btn.configure(state='normal')
        self.refresh_btn.configure(state='normal')
        self.select_all_btn.configure(state='normal')
        self.deselect_all_btn.configure(state='normal')
        self.add_btn.configure(state='normal')
        self.edit_btn.configure(state='normal')
        self.delete_btn.configure(state='normal')

# ====== MAIN EXECUTION ======
if __name__ == "__main__":
    root = tk.Tk()
    app = InstagramGUI(root)
    root.mainloop()