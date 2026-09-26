#!/usr/bin/env python3
"""
FFxtractor - a simple GUI to inspect and extract/convert streams
from media containers (mkv, mp4, avi, ...) using ffmpeg/ffprobe.

Changelog v7:
- Log filtering: hides "time=N/A" lines from GUI to prevent visual spam
- Progress bar still updates correctly using size-based fallback during N/A
- Fixed batch mode: correctly respects per-stream checkbox deselections
- Fixed output stream indices (-c:N uses output index, not input)
- Added "Flags" column to show forced/default subtitles
- Normalized paths to use forward slashes consistently
"""
import sys
import os
import json
import shutil
import subprocess
import re
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSettings
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QTableWidget, QTableWidgetItem,
    QCheckBox, QComboBox, QProgressBar, QTextEdit, QMessageBox,
    QLineEdit, QGroupBox, QFormLayout, QListWidget, QListWidgetItem,
    QTabWidget, QAbstractItemView
)

APP_NAME = "FFxtractor"
ORG_NAME = "FFxtractor"

# ---------------------------------------------------------------------------
# Codec presets for conversion
# ---------------------------------------------------------------------------
VIDEO_CODECS = [
    ("libx264",    "H.264 / AVC  (libx264)    - widely compatible"),
    ("libx265",    "H.265 / HEVC (libx265)    - better compression"),
    ("libvpx-vp9", "VP9          (libvpx-vp9) - open, web-friendly"),
    ("mpeg4",      "MPEG-4 Part 2 (mpeg4)     - legacy compatibility"),
]

AUDIO_CODECS = [
    ("aac",        "AAC   - default for mp4/mkv"),
    ("libmp3lame", "MP3   - universal compatibility"),
    ("libvorbis",  "Vorbis - open, good quality"),
    ("libopus",    "Opus  - best for speech/low bitrate"),
    ("flac",       "FLAC  - lossless (mkv only)"),
    ("pcm_s16le",  "PCM 16-bit - uncompressed (wav/mkv)"),
]

SUBTITLE_CODECS = [
    ("ass",    "ASS/SSA - styled subtitles (mkv)"),
    ("srt",    "SRT     - simple text (mkv)"),
    ("webvtt", "WebVTT  - web subtitles (webm/mp4)"),
    ("mov_text","mov_text - mp4 native subtitles"),
]

# Common install locations to probe as a fallback, per platform
COMMON_PATHS = {
    "ffmpeg": [
        "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg",
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ],
    "ffprobe": [
        "/usr/bin/ffprobe", "/usr/local/bin/ffprobe", "/opt/homebrew/bin/ffprobe",
        r"C:\ffmpeg\bin\ffprobe.exe",
    ],
}


def normalize_path(path):
    """Normalize path to use forward slashes consistently."""
    return path.replace("\\", "/")


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe path management
# ---------------------------------------------------------------------------
class FFmpegManager:
    """Finds, validates and persists the paths to ffmpeg and ffprobe."""

    def __init__(self):
        self.settings = QSettings(ORG_NAME, APP_NAME)
        self.ffmpeg_path = self.settings.value("ffmpeg_path", "")
        self.ffprobe_path = self.settings.value("ffprobe_path", "")

    def autodetect(self):
        if not self._is_valid(self.ffmpeg_path):
            self.ffmpeg_path = self._find("ffmpeg")
        if not self._is_valid(self.ffprobe_path):
            self.ffprobe_path = self._find("ffprobe")
        self._save()

    def _find(self, name):
        local = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "bin",
            name + (".exe" if os.name == "nt" else "")
        )
        if self._is_valid(local):
            return normalize_path(local)
        found = shutil.which(name)
        if found:
            return normalize_path(found)
        for candidate in COMMON_PATHS.get(name, []):
            if self._is_valid(candidate):
                return normalize_path(candidate)
        return ""

    @staticmethod
    def _is_valid(path):
        if not path or not os.path.isfile(path):
            return False
        try:
            subprocess.run([path, "-version"], capture_output=True, timeout=5)
            return True
        except Exception:
            return False

    def set_paths(self, ffmpeg_path, ffprobe_path):
        self.ffmpeg_path = normalize_path(ffmpeg_path)
        self.ffprobe_path = normalize_path(ffprobe_path)
        self._save()

    def _save(self):
        self.settings.setValue("ffmpeg_path", self.ffmpeg_path)
        self.settings.setValue("ffprobe_path", self.ffprobe_path)

    def is_ready(self):
        return self._is_valid(self.ffmpeg_path) and self._is_valid(self.ffprobe_path)


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------
def probe_file(ffprobe_path, filepath):
    cmd = [
        ffprobe_path, "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", filepath
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed:\n{result.stderr}")
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Single-file worker
# ---------------------------------------------------------------------------
class FFmpegWorker(QThread):
    progress = pyqtSignal(int)
    log_line = pyqtSignal(str)
    finished_ok = pyqtSignal(bool, str)

    def __init__(self, ffmpeg_path, cmd_args, duration_seconds, output_path=""):
        super().__init__()
        self.ffmpeg_path = ffmpeg_path
        self.cmd_args = cmd_args
        self.duration_seconds = duration_seconds or 0
        self.output_path = output_path
        self._process = None

    def run(self):
        full_cmd = [self.ffmpeg_path] + self.cmd_args
        self.log_line.emit("Command: " + " ".join(full_cmd))
        try:
            self._process = subprocess.Popen(
                full_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, bufsize=1
            )
        except Exception as e:
            self.finished_ok.emit(False, str(e))
            return

        time_re = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
        size_re = re.compile(r"size=\s*(\d+)KiB")
        last_pct = 0
        estimated_size = 0

        # Estimate output size from input (for copy mode they are similar)
        for i, arg in enumerate(self.cmd_args):
            if arg == "-i" and i + 1 < len(self.cmd_args):
                input_path = self.cmd_args[i + 1]
                if os.path.exists(input_path):
                    estimated_size = os.path.getsize(input_path)
                    break

        for line in self._process.stdout:
            line_stripped = line.rstrip()
            
            # Hide "time=N/A" lines from GUI log to prevent visual spam,
            # but we still process them below for size-based progress.
            if "time=N/A" not in line_stripped:
                self.log_line.emit(line_stripped)

            # Try time-based progress first
            match = time_re.search(line)
            if match and self.duration_seconds > 0:
                h, m, s, cs = match.groups()
                elapsed = int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100
                pct = min(100, int(elapsed / self.duration_seconds * 100))
                if pct > last_pct:
                    self.progress.emit(pct)
                    last_pct = pct
            else:
                # Fallback to size-based progress
                size_match = size_re.search(line)
                if size_match and estimated_size > 0:
                    current_size_kb = int(size_match.group(1))
                    current_size = current_size_kb * 1024
                    pct = min(100, int(current_size / estimated_size * 100))
                    if pct > last_pct:
                        self.progress.emit(pct)
                        last_pct = pct

        self._process.wait()
        if self._process.returncode == 0:
            self.progress.emit(100)
            self.finished_ok.emit(True, "Done.")
        else:
            self.finished_ok.emit(False, f"ffmpeg exited with code {self._process.returncode}")


# ---------------------------------------------------------------------------
# Batch worker
# ---------------------------------------------------------------------------
class BatchWorker(QThread):
    file_started = pyqtSignal(int, str)
    file_progress = pyqtSignal(int, int)
    file_finished = pyqtSignal(int, bool, str)
    all_finished = pyqtSignal(int, int)
    global_progress = pyqtSignal(int)
    log_line = pyqtSignal(str)

    def __init__(self, ffmpeg_path, jobs):
        super().__init__()
        self.ffmpeg_path = ffmpeg_path
        self.jobs = jobs  # list of (input_path, cmd_args, duration, output_path)
        self._stop_requested = False
        self._process = None

    def stop(self):
        self._stop_requested = True
        if self._process is not None:
            try:
                self._process.terminate()
            except Exception:
                pass

    def run(self):
        total_duration = sum(d for _, _, d, _ in self.jobs) or 1
        elapsed_so_far = 0.0
        success_count = 0
        time_re = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
        size_re = re.compile(r"size=\s*(\d+)KiB")

        for idx, (input_path, cmd_args, duration, output_path) in enumerate(self.jobs):
            if self._stop_requested:
                break

            filename = os.path.basename(input_path)
            self.file_started.emit(idx, filename)
            self.log_line.emit(f"\n=== [{idx+1}/{len(self.jobs)}] {filename} ===")

            full_cmd = [self.ffmpeg_path] + cmd_args
            self.log_line.emit("Command: " + " ".join(full_cmd))

            try:
                self._process = subprocess.Popen(
                    full_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    universal_newlines=True, bufsize=1
                )
            except Exception as e:
                self.file_finished.emit(idx, False, str(e))
                self.log_line.emit(f"ERROR starting ffmpeg: {e}")
                continue

            file_ok = True
            file_msg = "Done."
            last_file_pct = 0

            # Estimate output size
            estimated_size = 0
            if os.path.exists(input_path):
                estimated_size = os.path.getsize(input_path)

            for line in self._process.stdout:
                if self._stop_requested:
                    break
                
                line_stripped = line.rstrip()
                
                # Hide "time=N/A" lines from GUI log to prevent visual spam
                if "time=N/A" not in line_stripped:
                    self.log_line.emit(line_stripped)

                # Try time-based progress
                match = time_re.search(line)
                if match and duration > 0:
                    h, m, s, cs = match.groups()
                    file_elapsed = int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100
                    file_pct = min(100, int(file_elapsed / duration * 100))

                    if file_pct > last_file_pct:
                        self.file_progress.emit(idx, file_pct)
                        last_file_pct = file_pct

                    global_elapsed = elapsed_so_far + file_elapsed
                    global_pct = min(100, int(global_elapsed / total_duration * 100))
                    self.global_progress.emit(global_pct)
                else:
                    # Fallback to size-based progress
                    size_match = size_re.search(line)
                    if size_match and estimated_size > 0:
                        current_size_kb = int(size_match.group(1))
                        current_size = current_size_kb * 1024
                        file_pct = min(100, int(current_size / estimated_size * 100))

                        if file_pct > last_file_pct:
                            self.file_progress.emit(idx, file_pct)
                            last_file_pct = file_pct

                        # Global progress weighted by file duration
                        file_contribution = (duration / total_duration) * file_pct
                        prev_files_contribution = (elapsed_so_far / total_duration) * 100
                        global_pct = min(100, int(prev_files_contribution + file_contribution))
                        self.global_progress.emit(global_pct)

            self._process.wait()
            rc = self._process.returncode
            self._process = None

            if self._stop_requested:
                self.log_line.emit("Batch cancelled by user.")
                break

            if rc == 0:
                success_count += 1
            else:
                file_ok = False
                file_msg = f"ffmpeg exited with code {rc}"

            self.file_finished.emit(idx, file_ok, file_msg)
            self.log_line.emit(f"Result: {file_msg}")
            elapsed_so_far += duration

        if not self._stop_requested:
            self.global_progress.emit(100)
        self.all_finished.emit(success_count, len(self.jobs))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
ACTIONS = ["Copy", "Convert", "Discard"]
CODEC_TYPE_LABELS = {
    "video": "Video",
    "audio": "Audio",
    "subtitle": "Subtitle",
    "data": "Data",
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1000, 700)
        self.ffmpeg = FFmpegManager()
        self.ffmpeg.autodetect()

        self.input_path = ""
        self.probe_data = None
        self.worker = None

        self.batch_files = []
        self.batch_worker = None

        self._build_ui()
        self._check_ffmpeg_ready()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Top row
        input_row = QHBoxLayout()
        self.input_label = QLabel("No file loaded")
        open_btn = QPushButton("Open file...")
        open_btn.clicked.connect(self.open_file)
        settings_btn = QPushButton("ffmpeg settings...")
        settings_btn.clicked.connect(self.open_settings)
        input_row.addWidget(open_btn)
        input_row.addWidget(self.input_label, stretch=1)
        input_row.addWidget(settings_btn)
        layout.addLayout(input_row)

        # Conversion settings
        conv_group = QGroupBox("Conversion settings (used when action = Convert)")
        conv_layout = QHBoxLayout(conv_group)

        conv_layout.addWidget(QLabel("Video:"))
        self.video_codec_combo = QComboBox()
        for codec_name, display_name in VIDEO_CODECS:
            self.video_codec_combo.addItem(display_name, codec_name)
        conv_layout.addWidget(self.video_codec_combo)

        conv_layout.addWidget(QLabel("Audio:"))
        self.audio_codec_combo = QComboBox()
        for codec_name, display_name in AUDIO_CODECS:
            self.audio_codec_combo.addItem(display_name, codec_name)
        conv_layout.addWidget(self.audio_codec_combo)

        conv_layout.addWidget(QLabel("Subtitle:"))
        self.subtitle_codec_combo = QComboBox()
        for codec_name, display_name in SUBTITLE_CODECS:
            self.subtitle_codec_combo.addItem(display_name, codec_name)
        conv_layout.addWidget(self.subtitle_codec_combo)

        layout.addWidget(conv_group)

        # Tab widget
        self.tabs = QTabWidget()
        self.single_tab = self._build_single_tab()
        self.batch_tab = self._build_batch_tab()
        self.tabs.addTab(self.single_tab, "Single file")
        self.tabs.addTab(self.batch_tab, "Batch")
        layout.addWidget(self.tabs, stretch=1)

        # Run row
        run_row = QHBoxLayout()
        self.run_btn = QPushButton("Run")
        self.run_btn.clicked.connect(self.run_action)
        self.run_btn.setEnabled(False)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_action)
        self.cancel_btn.setEnabled(False)
        self.progress_bar = QProgressBar()
        run_row.addWidget(self.run_btn)
        run_row.addWidget(self.cancel_btn)
        run_row.addWidget(self.progress_bar, stretch=1)
        layout.addLayout(run_row)

        # Log view
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(180)
        layout.addWidget(self.log_view)

        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _build_single_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Stream table with 7 columns
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Include", "Index", "Type", "Codec", "Language", "Flags", "Action"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        # Output settings
        out_group = QGroupBox("Output")
        out_form = QFormLayout(out_group)
        out_path_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        out_browse_btn = QPushButton("Browse...")
        out_browse_btn.clicked.connect(self.choose_output)
        out_path_row.addWidget(self.output_edit)
        out_path_row.addWidget(out_browse_btn)
        out_form.addRow("Output file:", out_path_row)

        self.container_combo = QComboBox()
        self.container_combo.addItems(["mkv", "mp4", "avi", "webm", "mov"])
        out_form.addRow("Container:", self.container_combo)
        layout.addWidget(out_group)

        return widget

    def _build_batch_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Info label
        info_label = QLabel(
            "<b>Batch mode:</b> Configure stream actions in the 'Single file' tab first, "
            "then add files here. The same rules will be applied to all files."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; padding: 10px;")
        layout.addWidget(info_label)

        # File list
        list_row = QHBoxLayout()
        self.batch_list = QListWidget()
        self.batch_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        list_row.addWidget(self.batch_list, stretch=1)

        btn_col = QVBoxLayout()
        add_btn = QPushButton("Add files...")
        add_btn.clicked.connect(self.batch_add_files)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self.batch_remove_selected)
        clear_btn = QPushButton("Clear all")
        clear_btn.clicked.connect(self.batch_clear)
        btn_col.addWidget(add_btn)
        btn_col.addWidget(remove_btn)
        btn_col.addWidget(clear_btn)
        btn_col.addStretch()
        list_row.addLayout(btn_col)
        layout.addLayout(list_row)

        # Output folder
        out_group = QGroupBox("Batch output")
        out_form = QFormLayout(out_group)
        out_folder_row = QHBoxLayout()
        self.batch_output_edit = QLineEdit()
        self.batch_output_edit.setPlaceholderText(
            "Output folder - files will be named <input>_out.<container>"
        )
        out_browse_btn = QPushButton("Browse...")
        out_browse_btn.clicked.connect(self.batch_choose_output_folder)
        out_folder_row.addWidget(self.batch_output_edit)
        out_folder_row.addWidget(out_browse_btn)
        out_form.addRow("Output folder:", out_folder_row)

        self.batch_container_combo = QComboBox()
        self.batch_container_combo.addItems(["mkv", "mp4", "avi", "webm", "mov"])
        out_form.addRow("Container:", self.batch_container_combo)
        layout.addWidget(out_group)

        self.batch_list.model().rowsInserted.connect(self._update_batch_run_btn)
        self.batch_list.model().rowsRemoved.connect(self._update_batch_run_btn)

        return widget

    def _update_batch_run_btn(self, *args):
        self._refresh_run_btn_state()

    def _refresh_run_btn_state(self):
        if self.tabs.currentIndex() == 0:
            self.run_btn.setEnabled(bool(self.input_path))
        else:
            has_files = len(self.batch_files) > 0
            has_folder = bool(self.batch_output_edit.text().strip())
            self.run_btn.setEnabled(has_files and has_folder)

    def _on_tab_changed(self, idx):
        self._refresh_run_btn_state()
        self.run_btn.setText("Run" if idx == 0 else "Run Batch")

    def _check_ffmpeg_ready(self):
        if not self.ffmpeg.is_ready():
            QMessageBox.warning(
                self, APP_NAME,
                "ffmpeg/ffprobe not found automatically.\n"
                "Please set the paths manually in 'ffmpeg settings...'."
            )

    # ---------------------------------------------------------------- Settings
    def open_settings(self):
        ffmpeg_path, _ = QFileDialog.getOpenFileName(self, "Select ffmpeg executable")
        if not ffmpeg_path:
            return
        ffprobe_path, _ = QFileDialog.getOpenFileName(self, "Select ffprobe executable")
        if not ffprobe_path:
            return
        self.ffmpeg.set_paths(ffmpeg_path, ffprobe_path)
        self._check_ffmpeg_ready()

    # ---------------------------------------------------------------- Single file
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open media file", "",
            "Media files (*.mkv *.mp4 *.avi *.mov *.ts *.webm);;All files (*)"
        )
        if not path:
            return
        if not self.ffmpeg.is_ready():
            QMessageBox.critical(self, APP_NAME, "ffmpeg/ffprobe are not configured.")
            return
        try:
            self.probe_data = probe_file(self.ffmpeg.ffprobe_path, path)
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Probe failed:\n{e}")
            return
        self.input_path = path
        self.input_label.setText(path)
        self._populate_table()
        base, _ = os.path.splitext(path)
        self.output_edit.setText(base + "_out." + self.container_combo.currentText())
        self._refresh_run_btn_state()

    def _populate_table(self):
        streams = self.probe_data.get("streams", [])
        self.table.setRowCount(len(streams))

        for row, stream in enumerate(streams):
            # Include checkbox
            checkbox = QCheckBox()
            checkbox.setChecked(True)
            cell_widget = QWidget()
            cell_layout = QHBoxLayout(cell_widget)
            cell_layout.addWidget(checkbox)
            cell_layout.setAlignment(Qt.AlignCenter)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(row, 0, cell_widget)

            index = stream.get("index", row)
            codec_type = stream.get("codec_type", "?")
            codec_name = stream.get("codec_name", "?")
            language = stream.get("tags", {}).get("language", "-")

            # Extract flags (forced, default, etc.)
            disposition = stream.get("disposition", {})
            flags = []
            if disposition.get("forced") == 1:
                flags.append("forced")
            if disposition.get("default") == 1:
                flags.append("default")
            flags_str = ", ".join(flags) if flags else ""

            if codec_type == "video":
                info = f'{stream.get("width", "?")}x{stream.get("height", "?")}'
            elif codec_type == "audio":
                info = f'{stream.get("channels", "?")}ch @ {stream.get("sample_rate", "?")}Hz'
            else:
                info = ""

            self.table.setItem(row, 1, QTableWidgetItem(str(index)))
            self.table.setItem(row, 2, QTableWidgetItem(CODEC_TYPE_LABELS.get(codec_type, codec_type)))
            self.table.setItem(row, 3, QTableWidgetItem(codec_name))
            self.table.setItem(row, 4, QTableWidgetItem(language))
            self.table.setItem(row, 5, QTableWidgetItem(flags_str))

            action_combo = QComboBox()
            action_combo.addItems(ACTIONS)
            self.table.setCellWidget(row, 6, action_combo)

    def choose_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save output as")
        if path:
            self.output_edit.setText(path)

    # ---------------------------------------------------------------- Batch
    def batch_add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select media files to add to batch", "",
            "Media files (*.mkv *.mp4 *.avi *.mov *.ts *.webm);;All files (*)"
        )
        if not paths:
            return
        for p in paths:
            if p not in self.batch_files:
                self.batch_files.append(p)
                self.batch_list.addItem(QListWidgetItem(p))
        if not self.batch_output_edit.text().strip() and self.batch_files:
            self.batch_output_edit.setText(os.path.dirname(self.batch_files[0]))
        self._refresh_run_btn_state()

    def batch_remove_selected(self):
        rows = sorted([idx.row() for idx in self.batch_list.selectedIndexes()], reverse=True)
        for r in rows:
            self.batch_list.takeItem(r)
            del self.batch_files[r]
        self._refresh_run_btn_state()

    def batch_clear(self):
        self.batch_list.clear()
        self.batch_files.clear()
        self._refresh_run_btn_state()

    def batch_choose_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output folder")
        if folder:
            self.batch_output_edit.setText(folder)
            self._refresh_run_btn_state()

    # ---------------------------------------------------------------- Command building
    def _get_codec_for_type(self, codec_type):
        if codec_type == "video":
            return self.video_codec_combo.currentData()
        if codec_type == "audio":
            return self.audio_codec_combo.currentData()
        if codec_type == "subtitle":
            return self.subtitle_codec_combo.currentData()
        return "copy"

    def _extract_rules_from_table(self):
        """Extract per-stream rules from the table for batch processing.
        Returns a list of (stream_index, codec_type, action) tuples.
        Unchecked streams get action='Discard' automatically."""
        if not self.probe_data:
            return []

        streams = self.probe_data.get("streams", [])
        rules = []

        for row, stream in enumerate(streams):
            include_widget = self.table.cellWidget(row, 0).findChild(QCheckBox)
            action_widget = self.table.cellWidget(row, 6)
            action = action_widget.currentText()
            codec_type = stream.get("codec_type", "")
            index = stream.get("index", row)

            # Unchecked = Discard, regardless of what the combo says
            if not include_widget.isChecked():
                action = "Discard"

            rules.append((index, codec_type, action))

        return rules

    def _build_command_for_probe(self, input_path, probe_data, output_path):
        """Translate the table selections into an ffmpeg argument list."""
        streams = probe_data.get("streams", [])
        args = ["-y", "-i", input_path]
        map_args = []
        codec_args = []
        out_idx = 0  # output stream counter

        for row, stream in enumerate(streams):
            include_widget = self.table.cellWidget(row, 0).findChild(QCheckBox)
            if not include_widget.isChecked():
                continue
            action_widget = self.table.cellWidget(row, 6)
            action = action_widget.currentText()
            if action == "Discard":
                continue

            index = stream.get("index", row)
            codec_type = stream.get("codec_type", "")
            map_args += ["-map", f"0:{index}"]

            if action == "Copy":
                codec_args += [f"-c:{out_idx}", "copy"]
            elif action == "Convert":
                codec = self._get_codec_for_type(codec_type)
                codec_args += [f"-c:{out_idx}", codec]
            else:
                codec_args += [f"-c:{out_idx}", "copy"]

            out_idx += 1

        args += map_args + codec_args
        if not output_path:
            raise ValueError("No output path specified.")
        args.append(normalize_path(output_path))
        return args

    def _build_batch_command(self, input_path, probe_data, container, rules):
        """Build a command for batch mode using per-index rules from the table."""
        base, _ = os.path.splitext(os.path.basename(input_path))
        output_folder = self.batch_output_edit.text().strip()
        output_path = os.path.join(output_folder, f"{base}_out.{container}")
        output_path = normalize_path(output_path)

        streams = probe_data.get("streams", [])
        args = ["-y", "-i", input_path]
        map_args = []
        codec_args = []
        out_idx = 0

        for stream in streams:
            index = stream.get("index", 0)
            codec_type = stream.get("codec_type", "")

            # Find matching rule by index AND type
            action = "Copy"  # safe default
            for rule_idx, rule_type, rule_action in rules:
                if rule_idx == index and rule_type == codec_type:
                    action = rule_action
                    break

            if action == "Discard":
                continue

            map_args += ["-map", f"0:{index}"]

            if action == "Copy":
                codec_args += [f"-c:{out_idx}", "copy"]
            elif action == "Convert":
                codec = self._get_codec_for_type(codec_type)
                codec_args += [f"-c:{out_idx}", codec]
            else:
                codec_args += [f"-c:{out_idx}", "copy"]

            out_idx += 1

        args += map_args + codec_args
        args.append(output_path)
        return args, output_path

    # ---------------------------------------------------------------- Run
    def run_action(self):
        if self.tabs.currentIndex() == 0:
            self._run_single()
        else:
            self._run_batch()

    def _run_single(self):
        output_path = self.output_edit.text().strip()
        if os.path.exists(output_path):
            reply = QMessageBox.question(
                self, APP_NAME, "Output file already exists. Overwrite?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
        try:
            cmd_args = self._build_command_for_probe(
                self.input_path, self.probe_data, output_path
            )
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, str(e))
            return

        duration = float(self.probe_data.get("format", {}).get("duration", 0) or 0)
        self.log_view.clear()
        self.progress_bar.setValue(0)
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)

        self.worker = FFmpegWorker(self.ffmpeg.ffmpeg_path, cmd_args, duration, output_path)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.log_line.connect(self.log_view.append)
        self.worker.finished_ok.connect(self._on_single_finished)
        self.worker.start()

    def _on_single_finished(self, success, message):
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if success:
            QMessageBox.information(self, APP_NAME, message)
        else:
            QMessageBox.critical(self, APP_NAME, message)

    def _run_batch(self):
        if not self.batch_files:
            return

        if not self.probe_data:
            QMessageBox.critical(
                self, APP_NAME,
                "No file loaded in 'Single file' tab.\n"
                "Please open a file and configure stream actions first."
            )
            return

        rules = self._extract_rules_from_table()
        if not rules:
            QMessageBox.warning(
                self, APP_NAME,
                "No stream actions configured.\n"
                "Please configure actions in the 'Single file' tab first."
            )
            return

        output_folder = self.batch_output_edit.text().strip()
        if not os.path.isdir(output_folder):
            QMessageBox.critical(self, APP_NAME, "Output folder does not exist.")
            return

        container = self.batch_container_combo.currentText()

        # Check for existing files
        existing = []
        for p in self.batch_files:
            base, _ = os.path.splitext(os.path.basename(p))
            out = os.path.join(output_folder, f"{base}_out.{container}")
            if os.path.exists(out):
                existing.append(out)
        if existing:
            reply = QMessageBox.question(
                self, APP_NAME,
                f"{len(existing)} output file(s) already exist. Overwrite?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        # Build jobs
        jobs = []
        for p in self.batch_files:
            try:
                pdata = probe_file(self.ffmpeg.ffprobe_path, p)
            except Exception as e:
                self.log_view.append(f"Probe failed for {p}: {e}")
                continue
            duration = float(pdata.get("format", {}).get("duration", 0) or 0)
            cmd, output_path = self._build_batch_command(p, pdata, container, rules)
            jobs.append((p, cmd, duration, output_path))

        if not jobs:
            QMessageBox.warning(self, APP_NAME, "No valid files to process.")
            return

        self.log_view.clear()
        self.progress_bar.setValue(0)
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)

        self.batch_worker = BatchWorker(self.ffmpeg.ffmpeg_path, jobs)
        self.batch_worker.global_progress.connect(self.progress_bar.setValue)
        self.batch_worker.log_line.connect(self.log_view.append)
        self.batch_worker.file_started.connect(
            lambda i, n: self.log_view.append(f"Starting file {i+1}/{len(jobs)}: {n}")
        )
        self.batch_worker.file_finished.connect(
            lambda i, ok, msg: self.log_view.append(
                f"File {i+1} {'OK' if ok else 'FAILED'}: {msg}"
            )
        )
        self.batch_worker.all_finished.connect(self._on_batch_finished)
        self.batch_worker.start()

    def _on_batch_finished(self, success_count, total_count):
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        failed = total_count - success_count
        QMessageBox.information(
            self, APP_NAME,
            f"Batch completed.\n\n"
            f"Total: {total_count}\n"
            f"Successful: {success_count}\n"
            f"Failed: {failed}"
        )

    def cancel_action(self):
        if self.worker is not None and self.worker.isRunning():
            if self.worker._process is not None:
                try:
                    self.worker._process.terminate()
                except Exception:
                    pass
        if self.batch_worker is not None and self.batch_worker.isRunning():
            self.batch_worker.stop()
        self.log_view.append("Cancellation requested...")


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()