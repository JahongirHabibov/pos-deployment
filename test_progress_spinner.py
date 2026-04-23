#!/usr/bin/env python3
"""
Test script for the new _run_compose_with_progress() function.
Simulates a docker pull with progress spinner instead of verbose logging.
Uses a small image to avoid long waits.
"""

import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import scrolledtext
from pathlib import Path
import datetime

REPO_DIR = Path(__file__).parent.resolve()
LOG_DIR = REPO_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Colors
C_SUCCESS = "#28a745"
C_DANGER = "#dc3545"
C_INFO = "#0288d1"

class ProgressTester:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Progress Spinner Test")
        self.root.geometry("900x650")
        
        # Input section
        input_frame = tk.Frame(self.root, bg="#f0f0f0", pady=10, padx=10)
        input_frame.pack(fill=tk.X)
        
        tk.Label(input_frame, text="Docker Image (image:tag):",
                 font=("Segoe UI", 10, "bold"), bg="#f0f0f0").pack(anchor="w", pady=(0, 3))
        
        self.image_input = tk.Entry(
            input_frame, font=("Segoe UI", 11), width=60,
            relief=tk.SOLID, bd=1
        )
        self.image_input.pack(anchor="w", fill=tk.X, pady=(0, 5))
        self.image_input.insert(0, "")
        
        self.input_error = tk.Label(input_frame, text="", font=("Segoe UI", 9),
                                     bg="#f0f0f0", fg="#dc3545")
        self.input_error.pack(anchor="w", pady=(0, 0))
        
        # Log widget
        tk.Label(self.root, text="Pull-Test Output (mit Progress-Spinner):",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 5))
        
        self._log_widget = scrolledtext.ScrolledText(
            self.root, height=25, width=100, font=("Courier", 9),
            state=tk.DISABLED, bg="#0d1117", fg="#c9d1d9",
            insertbackground="white", relief=tk.SOLID, bd=1
        )
        self._log_widget.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Info label
        self.info_label = tk.Label(
            self.root, text="Image eingeben (z.B. ubuntu:latest, alpine:latest) und 'Test Starten' klicken",
            font=("Segoe UI", 9), fg="#666"
        )
        self.info_label.pack(anchor="w", padx=10, pady=(0, 5))
        
        # Buttons
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.btn_test = tk.Button(
            btn_frame, text="Test Starten", width=30,
            bg="#4a6cf7", fg="white", font=("Segoe UI", 10, "bold"),
            command=self._run_test
        )
        self.btn_test.pack(side=tk.LEFT, padx=5)
        
        self.btn_quit = tk.Button(
            btn_frame, text="Beenden", width=15,
            bg="#f0f0f0", font=("Segoe UI", 10),
            command=self.root.destroy
        )
        self.btn_quit.pack(side=tk.RIGHT, padx=5)
        
        self.test_running = False
    
    def _log(self, text, fg=None):
        """Append text to log widget (thread-safe)."""
        def _append():
            self._log_widget.configure(state=tk.NORMAL)
            if fg:
                tag = f"_col_{fg.replace('#', '')}"
                self._log_widget.tag_configure(tag, foreground=fg)
                self._log_widget.insert(tk.END, text + "\n", tag)
            else:
                self._log_widget.insert(tk.END, text + "\n")
            self._log_widget.see(tk.END)
            self._log_widget.configure(state=tk.DISABLED)
        
        self.root.after(0, _append)
    
    def _run_compose_with_progress(self, image_name, operation_label):
        """Test version: Run a docker pull with progress spinner."""
        
        # Use the provided image
        cmd = ["docker", "pull", image_name]
        
        try:
            p = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            self._log("❌ Docker nicht gefunden! Stellen Sie sicher, dass Docker installiert ist.", C_DANGER)
            return None
        
        # Circular buffer: keep last 500 lines for error reporting
        output_buffer = []
        buffer_max_size = 500
        lock = threading.Lock()
        stop_progress = threading.Event()
        
        # Thread 1: Read output and buffer it
        def _read_output():
            for line in p.stdout:
                clean = line.rstrip()
                with lock:
                    output_buffer.append(clean)
                    if len(output_buffer) > buffer_max_size:
                        output_buffer.pop(0)
        
        # Thread 2: Update progress line every 1 second (spinner animates smoothly)
        def _show_progress():
            spinners = ["|" , "/", "-", "\\"]
            counter = 0
            progress_line_id = None
            
            while not stop_progress.is_set():
                spinner = spinners[counter % 4]
                timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                msg = f"  {timestamp} {spinner} {operation_label}..."
                
                # First time: use _log to add line
                # Subsequent: replace the last line
                if progress_line_id is None:
                    self._log(msg, "#aaaaaa")
                    progress_line_id = "___progress___"
                else:
                    # Replace last line by removing and re-adding
                    def _replace():
                        try:
                            self._log_widget.configure(state=tk.NORMAL)
                            # Delete last line
                            line_start = self._log_widget.index("end-1c linestart")
                            line_end = self._log_widget.index("end-1c")
                            self._log_widget.delete(line_start, line_end)
                            # Insert new progress line
                            self._log_widget.insert(tk.END, msg + "\n", "")
                            self._log_widget.see(tk.END)
                            self._log_widget.configure(state=tk.DISABLED)
                        except tk.TclError:
                            pass
                    
                    self.root.after(0, _replace)
                
                counter += 1
                # Update spinner every 1 second for smooth animation
                if stop_progress.is_set():
                    break
                time.sleep(1)
        
        # Start the threads
        reader_thread = threading.Thread(target=_read_output, daemon=True)
        progress_thread = threading.Thread(target=_show_progress, daemon=True)
        reader_thread.start()
        progress_thread.start()
        
        # Wait for process to complete
        p.wait()
        stop_progress.set()
        reader_thread.join(timeout=2)
        progress_thread.join(timeout=2)
        
        # Log final result
        if p.returncode == 0:
            self._log(f"  ✓ {operation_label} erfolgreich", C_SUCCESS)
        else:
            # On error, show last N lines from buffer for debugging
            error_context_lines = 10
            self._log(f"  ✗ {operation_label} fehlgeschlagen", C_DANGER)
            with lock:
                if output_buffer:
                    self._log("", None)
                    self._log("  — Letzte Ausgabezeilen:", "#888888")
                    for line in output_buffer[-error_context_lines:]:
                        self._log(f"    {line}", "#888888")
        
        return p
    
    def _run_test(self):
        """Run the test."""
        # Validate image input
        image_name = self.image_input.get().strip()
        if not image_name:
            self.input_error.configure(text="❌ Image-Name ist erforderlich! (Format: image:tag z.B. ubuntu:latest)",
                                      fg="#dc3545")
            return
        
        if ":" not in image_name:
            self.input_error.configure(text="❌ Tag ist erforderlich! (Format: image:tag z.B. ubuntu:latest)",
                                      fg="#dc3545")
            return
        
        self.input_error.configure(text="")
        
        if self.test_running:
            return
        
        self.test_running = True
        self.btn_test.configure(state=tk.DISABLED)
        self.image_input.configure(state=tk.DISABLED)
        self._log_widget.configure(state=tk.NORMAL)
        self._log_widget.delete("1.0", tk.END)
        self._log_widget.configure(state=tk.DISABLED)
        
        self._log("=" * 80, "#7ec8e3")
        self._log("PROGRESS SPINNER TEST", "#7ec8e3")
        self._log("=" * 80, "#7ec8e3")
        self._log("", None)
        
        self._log(f"Test: docker pull {image_name} mit Progress-Animation", C_INFO)
        self._log("Erwartet: Spinner-Zeile alle ~5 Sekunden aktualisiert", C_INFO)
        self._log("(Nicht: Jede einzelne Output-Zeile geloggt)", C_INFO)
        self._log("", None)
        
        # Create log file for this test
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        log_file = LOG_DIR / f"progress-test-{ts}.log"
        
        def task():
            # Redirect _log to also write to file
            original_log_widget = None
            try:
                with open(log_file, "w", encoding="utf-8") as f:
                    f.write("Progress Spinner Test\n")
                    f.write("=" * 80 + "\n\n")
                
                proc = self._run_compose_with_progress(image_name, f"Docker-Image {image_name} wird heruntergeladen")
                
                self._log("", None)
                self._log("=" * 80, "#7ec8e3")
                self._log("TEST ABGESCHLOSSEN", "#7ec8e3")
                self._log("=" * 80, "#7ec8e3")
                self._log("", None)
                self._log(f"✓ Log-Datei: {log_file}", C_SUCCESS)
                self._log(f"✓ Log-Dateigröße: {log_file.stat().st_size} bytes", C_SUCCESS)
                self._log("", None)
                self._log("💡 Mit der neuen Progress-Funktion:", C_INFO)
                self._log("   - Nur 1-2 Progress-Zeilen sichtbar", C_INFO)
                self._log("   - Log-Datei bleibt klein", C_INFO)
                self._log("   - Spinner zeigt, dass es noch läuft", C_INFO)
                
            finally:
                self.test_running = False
                self.btn_test.configure(state=tk.NORMAL)
                self.image_input.configure(state=tk.NORMAL)
                self.info_label.configure(
                    text=f"✓ Test abgeschlossen. Log: {log_file}",
                    fg="#28a745"
                )
        
        threading.Thread(target=task, daemon=True).start()
    
    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    tester = ProgressTester()
    tester.run()
