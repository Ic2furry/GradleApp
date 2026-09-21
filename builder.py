import json
import os
import platform
import queue
import re
import shutil
import subprocess
import threading
import tkinter as tk
import shlex
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_NAME = "Minecraft Gradle Builder"
BASE_DIR = Path(__file__).resolve().parent
JAVA_DIR = BASE_DIR / "java"
CONFIG_FILE = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "project": "",
    "java": "",
    "task": "clean build",
    "extra_args": "",
    "java_args": "",
    "theme": "dark",
}


def load_config():
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg = DEFAULT_CONFIG.copy()
                cfg.update(data)
                return cfg
    except Exception:
        pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    try:
        CONFIG_FILE.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        messagebox.showerror("Ошибка", f"Не удалось сохранить настройки:\n{exc}")


def find_java_home(path: Path):
    """Return a JDK home if path looks like a JDK directory."""
    if not path.is_dir():
        return None

    if platform.system() == "Windows":
        java = path / "bin" / "java.exe"
        javac = path / "bin" / "javac.exe"
    else:
        java = path / "bin" / "java"
        javac = path / "bin" / "javac"

    if java.exists() and javac.exists():
        return path
    return None


def scan_jdks():
    JAVA_DIR.mkdir(parents=True, exist_ok=True)
    result = []

    # JDKs directly inside java/
    for p in sorted(JAVA_DIR.iterdir(), key=lambda x: x.name.lower()):
        if p.is_dir() and find_java_home(p):
            result.append(p)

    return result


def java_version(java_home: Path):
    exe = java_home / "bin" / ("java.exe" if os.name == "nt" else "java")
    try:
        proc = subprocess.run(
            [str(exe), "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        text = proc.stdout.strip()
        first = text.splitlines()[0] if text else "версия неизвестна"
        match = re.search(r'"([^"]+)"', text)
        version = match.group(1) if match else first
        return version
    except Exception:
        return "не удалось определить"


class BuilderApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_NAME)
        self.geometry("1050x720")
        self.minsize(850, 560)

        self.config_data = load_config()
        self.process = None
        self.output_queue = queue.Queue()
        self.build_running = False

        self.project_var = tk.StringVar(value=self.config_data.get("project", ""))
        self.java_var = tk.StringVar(value=self.config_data.get("java", ""))
        self.task_var = tk.StringVar(value=self.config_data.get("task", "clean build"))
        self.extra_args_var = tk.StringVar(value=self.config_data.get("extra_args", ""))
        self.java_args_var = tk.StringVar(value=self.config_data.get("java_args", ""))
        self.theme_var = tk.StringVar(value=self.config_data.get("theme", "dark"))
        self.status_var = tk.StringVar(value="Готово")
        self.java_info_var = tk.StringVar(value="")

        self.create_widgets()
        # Apply saved theme
        try:
            self.apply_theme(self.theme_var.get())
        except Exception:
            pass
        self.refresh_java_list()
        self.after(100, self.poll_output)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def create_widgets(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        title = ttk.Label(root, text=APP_NAME, font=("Segoe UI", 18, "bold"))
        title.pack(anchor="w", pady=(0, 12))

        project_frame = ttk.LabelFrame(root, text="Проект Minecraft-мода", padding=10)
        project_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(project_frame, text="Папка проекта:").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Entry(project_frame, textvariable=self.project_var).grid(
            row=0, column=1, sticky="ew", padx=8
        )
        ttk.Button(
            project_frame, text="Обзор...", command=self.choose_project
        ).grid(row=0, column=2)

        project_frame.columnconfigure(1, weight=1)

        java_frame = ttk.LabelFrame(root, text="Java", padding=10)
        java_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(java_frame, text="Версия JDK:").grid(
            row=0, column=0, sticky="w"
        )

        self.java_combo = ttk.Combobox(
            java_frame,
            textvariable=self.java_var,
            state="readonly",
            width=45,
        )
        self.java_combo.grid(row=0, column=1, sticky="ew", padx=8)
        self.java_combo.bind("<<ComboboxSelected>>", self.on_java_selected)

        ttk.Button(
            java_frame, text="Обновить", command=self.refresh_java_list
        ).grid(row=0, column=2, padx=(0, 5))

        ttk.Button(
            java_frame, text="Добавить JDK...", command=self.add_jdk
        ).grid(row=0, column=3)

        ttk.Label(
            java_frame,
            text=f"Папка Java: {JAVA_DIR}",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))

        ttk.Label(
            java_frame,
            textvariable=self.java_info_var,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        java_frame.columnconfigure(1, weight=1)

        gradle_frame = ttk.LabelFrame(root, text="Gradle", padding=10)
        gradle_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(gradle_frame, text="Команда:").grid(
            row=0, column=0, sticky="w"
        )

        self.task_combo = ttk.Combobox(
            gradle_frame,
            textvariable=self.task_var,
            values=[
                "clean build",
                "build",
                "clean",
                "assemble",
                "jar",
                "runClient",
                "runServer",
                "dependencies",
            ],
        )
        self.task_combo.grid(row=0, column=1, sticky="ew", padx=8)

        ttk.Label(gradle_frame, text="Доп. аргументы:").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Entry(
            gradle_frame, textvariable=self.extra_args_var
        ).grid(row=1, column=1, sticky="ew", padx=8, pady=(8, 0))

        ttk.Label(gradle_frame, text="Аргументы Java:").grid(
            row=2, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Entry(
            gradle_frame, textvariable=self.java_args_var
        ).grid(row=2, column=1, sticky="ew", padx=8, pady=(8, 0))

        gradle_frame.columnconfigure(1, weight=1)

        buttons = ttk.Frame(root)
        buttons.pack(fill="x", pady=(0, 10))

        self.build_button = ttk.Button(
            buttons, text="СБОРКА", command=self.start_build
        )
        self.build_button.pack(side="left")

        self.stop_button = ttk.Button(
            buttons, text="ОСТАНОВИТЬ", command=self.stop_build, state="disabled"
        )
        self.stop_button.pack(side="left", padx=6)

        ttk.Button(
            buttons, text="Открыть build/libs", command=self.open_build_libs
        ).pack(side="left", padx=6)

        ttk.Button(
            buttons, text="Очистить лог", command=self.clear_log
        ).pack(side="left", padx=6)

        ttk.Button(
            buttons, text="Сохранить настройки", command=self.save_current
        ).pack(side="right")

        # Theme selector
        ttk.Label(buttons, text="Тема:").pack(side="right", padx=(0, 6))
        self.theme_combo = ttk.Combobox(
            buttons,
            textvariable=self.theme_var,
            values=["dark", "light"],
            width=8,
            state="readonly",
        )
        self.theme_combo.pack(side="right")
        self.theme_combo.bind("<<ComboboxSelected>>", self.on_theme_selected)

        status_frame = ttk.Frame(root)
        status_frame.pack(fill="x", pady=(0, 6))

        ttk.Label(status_frame, text="Статус:").pack(side="left")
        ttk.Label(
            status_frame, textvariable=self.status_var
        ).pack(side="left", padx=6)

        self.progress = ttk.Progressbar(
            status_frame, mode="indeterminate", length=160
        )
        self.progress.pack(side="right")

        log_frame = ttk.LabelFrame(root, text="Gradle Output", padding=6)
        log_frame.pack(fill="both", expand=True)

        self.log = tk.Text(
            log_frame,
            wrap="none",
            font=("Consolas", 10),
            undo=False,
            bg="#101010",
            fg="#dddddd",
            insertbackground="white",
        )
        self.log.pack(side="left", fill="both", expand=True)

        yscroll = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.log.yview
        )
        yscroll.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=yscroll.set)

        xscroll = ttk.Scrollbar(
            root, orient="horizontal", command=self.log.xview
        )
        xscroll.pack(fill="x")
        self.log.configure(xscrollcommand=xscroll.set)

    def choose_project(self):
        folder = filedialog.askdirectory(title="Выберите папку Gradle-проекта")
        if folder:
            self.project_var.set(folder)
            self.save_current(silent=True)
            self.write_log(f"> Проект выбран: {folder}\n")

    def refresh_java_list(self):
        jdks = scan_jdks()

        values = []
        paths = {}

        if jdks:
            # Определяем версии JDK параллельно, чтобы ускорить обновление списка
            max_workers = min(4, len(jdks))
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                versions = list(ex.map(java_version, jdks))

            for jdk, version in zip(jdks, versions):
                label = f"{jdk.name} — Java {version}"
                values.append(label)
                paths[label] = str(jdk)

        self.java_paths = paths
        self.java_combo["values"] = values

        current = self.java_var.get()
        selected = None

        if current in values:
            selected = current
        else:
            saved_path = self.config_data.get("java", "")
            for label, path in paths.items():
                if os.path.normcase(path) == os.path.normcase(saved_path):
                    selected = label
                    break

        if selected:
            self.java_var.set(selected)
            self.update_java_info()
        elif values:
            self.java_var.set(values[0])
            self.update_java_info()
        else:
            self.java_var.set("")
            self.java_info_var.set(
                "JDK не найден. Добавьте JDK кнопкой «Добавить JDK...»."
            )

    def on_java_selected(self, _event=None):
        self.update_java_info()
        self.save_current(silent=True)

    def on_theme_selected(self, _event=None):
        self.apply_theme(self.theme_var.get())
        self.save_current(silent=True)

    def apply_theme(self, theme: str):
        """Apply a simple light/dark theme to the UI."""
        style = ttk.Style()
        if theme == "dark":
            bg = "#252525"
            frame_bg = "#2b2b2b"
            fg = "#e6e6e6"
            entry_bg = "#353535"
            log_bg = "#101010"
        else:
            bg = "#f0f0f0"
            frame_bg = "#f5f5f5"
            fg = "#000000"
            entry_bg = "#ffffff"
            log_bg = "#ffffff"

        try:
            self.configure(bg=bg)
        except Exception:
            pass

        # General styles
        try:
            style.configure("TFrame", background=frame_bg)
            style.configure("TLabel", background=frame_bg, foreground=fg)
            style.configure("TLabelFrame", background=frame_bg, foreground=fg)
            style.configure("TEntry", fieldbackground=entry_bg, foreground=fg)
            style.configure("TCombobox", fieldbackground=entry_bg, foreground=fg)
            style.configure("TButton", background=frame_bg, foreground=fg)
        except Exception:
            pass

        # Text widget
        try:
            self.log.configure(bg=log_bg, fg=fg, insertbackground=fg)
        except Exception:
            pass

    def update_java_info(self):
        label = self.java_var.get()
        path = getattr(self, "java_paths", {}).get(label, "")
        if path:
            self.java_info_var.set(f"JAVA_HOME: {path}")
        else:
            self.java_info_var.set("")

    def add_jdk(self):
        source = filedialog.askdirectory(
            title="Выберите установленную папку JDK"
        )
        if not source:
            return

        source_path = Path(source)
        if not find_java_home(source_path):
            messagebox.showerror(
                "Не JDK",
                "В выбранной папке не найдены bin/java и bin/javac.",
            )
            return

        default_name = source_path.name
        target_name = default_name

        target = JAVA_DIR / target_name

        if target.exists():
            # Add a suffix instead of overwriting an existing JDK.
            i = 2
            while (JAVA_DIR / f"{default_name}_{i}").exists():
                i += 1
            target = JAVA_DIR / f"{default_name}_{i}"

        copy = messagebox.askyesno(
            "Добавить JDK",
            f"Скопировать JDK в:\n{target}\n\n"
            "Да — сделать отдельную копию в папке builder/java.\n"
            "Нет — просто отменить добавление.",
        )

        if not copy:
            return

        try:
            JAVA_DIR.mkdir(parents=True, exist_ok=True)
            self.write_log(f"> Копирование JDK: {source_path} -> {target}\n")
            shutil.copytree(source_path, target)
            self.write_log("> JDK добавлена.\n")
            self.refresh_java_list()
        except Exception as exc:
            messagebox.showerror("Ошибка", f"Не удалось скопировать JDK:\n{exc}")

    def get_selected_java_home(self):
        label = self.java_var.get()
        path = getattr(self, "java_paths", {}).get(label)
        if not path:
            return None
        return Path(path)

    def get_gradle_executable(self, project: Path):
        if os.name == "nt":
            wrapper = project / "gradlew.bat"
        else:
            wrapper = project / "gradlew"

        if wrapper.exists():
            return wrapper

        # Fallback to a globally installed Gradle.
        return "gradle"

    def validate(self):
        project_text = self.project_var.get().strip()
        if not project_text:
            messagebox.showwarning("Проект", "Выберите папку Gradle-проекта.")
            return None

        project = Path(project_text)
        if not project.is_dir():
            messagebox.showerror("Проект", "Папка проекта не существует.")
            return None

        java_home = self.get_selected_java_home()
        if not java_home:
            messagebox.showwarning(
                "Java",
                "Выберите JDK из папки builder/java.",
            )
            return None

        gradle = self.get_gradle_executable(project)

        if isinstance(gradle, Path) and not gradle.exists():
            messagebox.showerror(
                "Gradle",
                "В проекте не найден gradlew.bat.",
            )
            return None

        return project, java_home, gradle

    def build_command(self, project, java_home, gradle):
        if isinstance(gradle, Path):
            command = [str(gradle)]
        else:
            command = [gradle]

        tasks = self.task_var.get().strip()
        if tasks:
            # Deliberately split like a normal command line for simple Gradle tasks.
            if os.name == "nt":
                command.extend(tasks.split())
            else:
                command.extend(shlex.split(tasks))

        extra = self.extra_args_var.get().strip()
        if extra:
            command.extend(extra.split() if os.name == "nt" else shlex.split(extra))

        return command

    def start_build(self):
        if self.build_running:
            return

        validated = self.validate()
        if not validated:
            return

        project, java_home, gradle = validated
        command = self.build_command(project, java_home, gradle)

        self.save_current(silent=True)

        env = os.environ.copy()
        env["JAVA_HOME"] = str(java_home)

        java_bin = java_home / "bin"
        env["PATH"] = str(java_bin) + os.pathsep + env.get("PATH", "")

        java_args = self.java_args_var.get().strip()
        if java_args:
            env["JAVA_TOOL_OPTIONS"] = (
                (env.get("JAVA_TOOL_OPTIONS", "") + " " + java_args).strip()
            )

        self.clear_log()
        self.write_log("=== Minecraft Gradle Builder ===\n")
        self.write_log(f"Project: {project}\n")
        self.write_log(f"JAVA_HOME: {java_home}\n")
        self.write_log(f"Command: {' '.join(command)}\n")
        self.write_log("=" * 70 + "\n\n")

        self.build_running = True
        self.build_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.progress.start(10)
        self.status_var.set("Сборка выполняется...")

        thread = threading.Thread(
            target=self.run_gradle,
            args=(command, project, env),
            daemon=True,
        )
        thread.start()

    def run_gradle(self, command, project, env):
        try:
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW

            self.process = subprocess.Popen(
                command,
                cwd=str(project),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )

            for line in iter(self.process.stdout.readline, ""):
                if line:
                    self.output_queue.put(("log", line))

            return_code = self.process.wait()
            self.output_queue.put(("finished", return_code))

        except FileNotFoundError as exc:
            self.output_queue.put(
                ("error", f"Не удалось запустить Gradle: {exc}\n")
            )
        except Exception as exc:
            self.output_queue.put(("error", f"Ошибка запуска: {exc}\n"))
        finally:
            self.process = None

    def stop_build(self):
        if not self.build_running or not self.process:
            return

        try:
            self.write_log("\n> Остановка Gradle...\n")
            self.process.terminate()
        except Exception as exc:
            self.write_log(f"> Не удалось остановить процесс: {exc}\n")

    def poll_output(self):
        try:
            while True:
                kind, data = self.output_queue.get_nowait()

                if kind == "log":
                    self.write_log(data)

                elif kind == "finished":
                    self.finish_build(data)

                elif kind == "error":
                    self.write_log(data)
                    self.finish_build(-1)

        except queue.Empty:
            pass

        self.after(100, self.poll_output)

    def finish_build(self, return_code):
        self.build_running = False
        self.build_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.progress.stop()

        if return_code == 0:
            self.status_var.set("BUILD SUCCESSFUL")
            self.write_log("\n=== BUILD SUCCESSFUL ===\n")
        elif return_code < 0:
            self.status_var.set("Сборка остановлена")
            self.write_log("\n=== BUILD STOPPED ===\n")
        else:
            self.status_var.set(f"BUILD FAILED (код {return_code})")
            self.write_log(
                f"\n=== BUILD FAILED — код {return_code} ===\n"
            )

    def open_build_libs(self):
        project_text = self.project_var.get().strip()
        if not project_text:
            messagebox.showwarning("Проект", "Сначала выберите проект.")
            return

        path = Path(project_text) / "build" / "libs"

        if not path.exists():
            messagebox.showwarning(
                "build/libs",
                f"Папка ещё не существует:\n{path}",
            )
            return

        try:
            if os.name == "nt":
                os.startfile(path)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("Ошибка", str(exc))

    def clear_log(self):
        self.log.delete("1.0", "end")

    def write_log(self, text):
        self.log.insert("end", text)
        self.log.see("end")

    def save_current(self, silent=False):
        java_home = self.get_selected_java_home()

        self.config_data.update(
            {
                "project": self.project_var.get(),
                "java": str(java_home) if java_home else "",
                "task": self.task_var.get(),
                "extra_args": self.extra_args_var.get(),
                "java_args": self.java_args_var.get(),
                "theme": self.theme_var.get(),
            }
        )

        try:
            save_config(self.config_data)
            if not silent:
                self.status_var.set("Настройки сохранены")
        except Exception:
            if not silent:
                raise

    def on_close(self):
        if self.build_running:
            if not messagebox.askyesno(
                "Сборка выполняется",
                "Gradle ещё работает. Остановить сборку и закрыть программу?",
            ):
                return
            self.stop_build()

        self.save_current(silent=True)
        self.destroy()


if __name__ == "__main__":
    JAVA_DIR.mkdir(parents=True, exist_ok=True)
    app = BuilderApp()
    app.mainloop()
