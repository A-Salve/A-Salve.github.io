import ipaddress
import queue
import re
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import paramiko


RC_FILE = Path("rc_list.txt")
PASSWORD_PATTERN = re.compile(r"(password|пароль)", re.IGNORECASE)


@dataclass(frozen=True)
class RcEntry:
    name: str
    host: str


class RcRepository:
    """Reads RC records from a user-editable text file."""

    @staticmethod
    def load(path: Path) -> list[RcEntry]:
        if not path.exists():
            return []

        entries: list[RcEntry] = []
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = [part.strip() for part in line.split(";", maxsplit=1)]
            if len(parts) != 2:
                continue

            name, host = parts
            if not name:
                continue

            try:
                ipaddress.ip_address(host)
            except ValueError:
                continue

            entries.append(RcEntry(name=name, host=host))

        return entries


class FontSenderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Отправка шрифтов на принтеры")
        self.root.geometry("760x700")
        self.root.minsize(700, 620)

        self.entries: dict[str, RcEntry] = {}
        self.saved_username = ""
        self.saved_password = ""
        self.selected_numbers: set[int] = set()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.in_progress = False

        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.rc_var = tk.StringVar()
        self.prefix_var = tk.StringVar(value="CLP")
        self.font_var = tk.StringVar(value="ZEBRA")
        self.mode_var = tk.StringVar(value="range")
        self.range_from_var = tk.StringVar()
        self.range_to_var = tk.StringVar()
        self.manual_var = tk.StringVar()

        self._build_ui()
        self._load_rc_entries()
        self._update_mode_ui()
        self._poll_logs()

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=14)
        container.pack(fill=tk.BOTH, expand=True)

        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        creds_frame = ttk.LabelFrame(container, text="Авторизация", padding=10)
        creds_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(creds_frame, text="Логин:").grid(row=0, column=0, sticky="w")
        ttk.Entry(creds_frame, textvariable=self.username_var, width=24).grid(row=0, column=1, padx=(8, 16))
        ttk.Label(creds_frame, text="Пароль:").grid(row=0, column=2, sticky="w")
        ttk.Entry(creds_frame, textvariable=self.password_var, width=24, show="*").grid(row=0, column=3, padx=(8, 12))
        ttk.Button(creds_frame, text="Сохранить", command=self._save_credentials).grid(row=0, column=4)

        prefs_frame = ttk.Frame(container)
        prefs_frame.pack(fill=tk.X, pady=(0, 8))

        rc_frame = ttk.LabelFrame(prefs_frame, text="Выбор РЦ", padding=10)
        rc_frame.pack(fill=tk.X, pady=(0, 8))
        self.rc_combo = ttk.Combobox(rc_frame, textvariable=self.rc_var, state="readonly")
        self.rc_combo.pack(fill=tk.X)

        fmt_frame = ttk.LabelFrame(prefs_frame, text="Настройки", padding=10)
        fmt_frame.pack(fill=tk.X)

        ttk.Label(fmt_frame, text="Префикс: ").grid(row=0, column=0, sticky="w")
        ttk.Entry(fmt_frame, textvariable=self.prefix_var, width=14).grid(row=0, column=1, sticky="w", padx=(8, 30))

        ttk.Label(fmt_frame, text="Шрифт: ").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            fmt_frame,
            textvariable=self.font_var,
            values=["ZEBRA", "CITIZEN"],
            state="readonly",
            width=14,
        ).grid(row=0, column=3, sticky="w", padx=(8, 0))

        printer_frame = ttk.LabelFrame(container, text="Номера принтеров", padding=10)
        printer_frame.pack(fill=tk.X, pady=(0, 8))

        mode_row = ttk.Frame(printer_frame)
        mode_row.pack(fill=tk.X)
        ttk.Radiobutton(mode_row, text="Диапазон", value="range", variable=self.mode_var, command=self._update_mode_ui).pack(side=tk.LEFT)
        ttk.Radiobutton(mode_row, text="Ручной ввод", value="manual", variable=self.mode_var, command=self._update_mode_ui).pack(side=tk.LEFT, padx=(14, 0))

        self.mode_inputs = ttk.Frame(printer_frame)
        self.mode_inputs.pack(fill=tk.X, pady=(8, 6))

        controls = ttk.Frame(printer_frame)
        controls.pack(fill=tk.X)
        ttk.Button(controls, text="Сбросить", command=self._reset_numbers).pack(side=tk.RIGHT)

        self.selected_label = ttk.Label(printer_frame, text="Выбрано: -")
        self.selected_label.pack(fill=tk.X, pady=(8, 0))

        term_frame = ttk.LabelFrame(container, text="Терминал", padding=10)
        term_frame.pack(fill=tk.BOTH, expand=True)
        self.terminal = ScrolledText(term_frame, height=16, state=tk.DISABLED, wrap=tk.WORD)
        self.terminal.pack(fill=tk.BOTH, expand=True)

        send_btn = ttk.Button(container, text="Отправить шрифты на принтеры", command=self._start_send)
        send_btn.pack(fill=tk.X, pady=(8, 0))
        self.send_button = send_btn

    def _load_rc_entries(self) -> None:
        entries = RcRepository.load(RC_FILE)
        self.entries = {entry.name: entry for entry in entries}

        names = sorted(self.entries.keys())
        self.rc_combo["values"] = names
        if names:
            self.rc_var.set(names[0])
        else:
            self.rc_var.set("")
            self._log("Не найден файл rc_list.txt или в нем нет корректных строк.")

    def _save_credentials(self) -> None:
        username = self.username_var.get().strip()
        password = self.password_var.get()
        if not username or not password:
            messagebox.showwarning("Проверка", "Введите логин и пароль.")
            return

        self.saved_username = username
        self.saved_password = password
        self._log("Учетные данные сохранены в памяти сессии.")

    def _update_mode_ui(self) -> None:
        for child in self.mode_inputs.winfo_children():
            child.destroy()

        if self.mode_var.get() == "range":
            ttk.Label(self.mode_inputs, text="С:").grid(row=0, column=0, sticky="w")
            ttk.Entry(self.mode_inputs, textvariable=self.range_from_var, width=10).grid(row=0, column=1, padx=(8, 16))
            ttk.Label(self.mode_inputs, text="ПО:").grid(row=0, column=2, sticky="w")
            ttk.Entry(self.mode_inputs, textvariable=self.range_to_var, width=10).grid(row=0, column=3, padx=(8, 16))
            ttk.Button(self.mode_inputs, text="Добавить", command=self._add_range).grid(row=0, column=4)
        else:
            entry = ttk.Entry(self.mode_inputs, textvariable=self.manual_var, width=16)
            entry.grid(row=0, column=0, padx=(0, 8))
            entry.bind("<Return>", lambda _: self._add_manual())
            ttk.Button(self.mode_inputs, text="+", width=3, command=self._add_manual).grid(row=0, column=1)

    def _add_range(self) -> None:
        try:
            start = int(self.range_from_var.get().strip())
            end = int(self.range_to_var.get().strip())
        except ValueError:
            messagebox.showwarning("Проверка", "Диапазон должен содержать целые числа.")
            return

        if start <= 0 or end <= 0 or start > end:
            messagebox.showwarning("Проверка", "Укажите корректный диапазон (С <= ПО, числа > 0).")
            return

        for value in range(start, end + 1):
            self.selected_numbers.add(value)

        self._refresh_selected_numbers()
        self._log(f"Добавлен диапазон: {start}-{end}.")

    def _add_manual(self) -> None:
        raw = self.manual_var.get().strip()
        if not raw:
            return

        if not raw.isdigit() or int(raw) <= 0:
            messagebox.showwarning("Проверка", "Введите положительный номер принтера.")
            return

        self.selected_numbers.add(int(raw))
        self.manual_var.set("")
        self._refresh_selected_numbers()

    def _reset_numbers(self) -> None:
        self.selected_numbers.clear()
        self._refresh_selected_numbers()
        self._log("Список принтеров очищен.")

    def _refresh_selected_numbers(self) -> None:
        if not self.selected_numbers:
            self.selected_label.config(text="Выбрано: -")
            return

        prefix = self.prefix_var.get().strip() or "CLP"
        formatted = ",".join(f"{prefix}{n}" for n in sorted(self.selected_numbers))
        self.selected_label.config(text=f"Выбрано: {formatted}")

    def _start_send(self) -> None:
        if self.in_progress:
            return

        if not self.saved_username or not self.saved_password:
            messagebox.showwarning("Проверка", "Сначала сохраните логин и пароль.")
            return

        rc_name = self.rc_var.get().strip()
        if rc_name not in self.entries:
            messagebox.showwarning("Проверка", "Выберите РЦ из списка.")
            return

        if not self.selected_numbers:
            messagebox.showwarning("Проверка", "Добавьте номера принтеров.")
            return

        prefix = self.prefix_var.get().strip()
        if not prefix:
            messagebox.showwarning("Проверка", "Префикс принтера не должен быть пустым.")
            return

        font = self.font_var.get().strip() or "ZEBRA"

        self.in_progress = True
        self.send_button.config(state=tk.DISABLED)
        threading.Thread(
            target=self._send_worker,
            args=(self.entries[rc_name].host, prefix, font, sorted(self.selected_numbers)),
            daemon=True,
        ).start()

    def _send_worker(self, host: str, prefix: str, font: str, numbers: list[int]) -> None:
        try:
            self._log(f"Подключение к {host}...")
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=host,
                username=self.saved_username,
                password=self.saved_password,
                timeout=15,
                auth_timeout=15,
                banner_timeout=15,
            )
            channel = ssh.invoke_shell()
            channel.settimeout(0.3)

            self._read_shell(channel, timeout=2.0)
            self._send_line(channel, "sudo su -")
            output = self._read_shell(channel, timeout=3.0)
            if PASSWORD_PATTERN.search(output):
                self._send_line(channel, self.saved_password)
                self._read_shell(channel, timeout=2.0)

            first_list = self._render_first_list(prefix, font, numbers)
            second_list = self._render_second_list(prefix, numbers)

            self._send_line(channel, "cd fastload")
            self._read_shell(channel, timeout=1.0)
            self._send_multiline_file(channel, "list.txt", first_list)
            self._send_line(channel, "./fastload list.txt")
            self._read_shell(channel, timeout=8.0)

            self._send_line(channel, "cd ../1.230.0")
            self._read_shell(channel, timeout=1.0)
            self._send_multiline_file(channel, "list.txt", second_list)
            self._send_line(channel, "./fastload list.txt")
            self._read_shell(channel, timeout=8.0)

            self._send_line(channel, "exit")
            self._send_line(channel, "exit")
            ssh.close()
            self._log("Задача успешно завершена.")
        except Exception as exc:  # noqa: BLE001
            self._log(f"Ошибка: {exc}")
        finally:
            self.root.after(0, self._finish_send)

    def _finish_send(self) -> None:
        self.in_progress = False
        self.send_button.config(state=tk.NORMAL)

    def _send_line(self, channel: paramiko.Channel, line: str) -> None:
        channel.send(line + "\n")

    def _send_multiline_file(self, channel: paramiko.Channel, remote_path: str, content: str) -> None:
        self._send_line(channel, f"cat > {remote_path} <<'EOF'")
        for row in content.splitlines():
            self._send_line(channel, row)
        self._send_line(channel, "EOF")
        self._read_shell(channel, timeout=1.5)

    def _read_shell(self, channel: paramiko.Channel, timeout: float) -> str:
        end = time.time() + timeout
        chunks: list[str] = []

        while time.time() < end:
            if channel.recv_ready():
                data = channel.recv(65535).decode("utf-8", errors="replace")
                chunks.append(data)
                for line in data.splitlines():
                    self._log(line)
            else:
                time.sleep(0.1)

        return "\n".join(chunks)

    def _render_first_list(self, prefix: str, font: str, numbers: list[int]) -> str:
        lines = [f"{prefix}{number};{font}" for number in numbers]
        lines.append("")
        return "\n".join(lines)

    def _render_second_list(self, prefix: str, numbers: list[int]) -> str:
        lines = [f"{prefix}{number}" for number in numbers]
        lines.append("")
        return "\n".join(lines)

    def _log(self, message: str) -> None:
        self.log_queue.put(message.rstrip())

    def _poll_logs(self) -> None:
        appended = False
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break

            appended = True
            self.terminal.configure(state=tk.NORMAL)
            self.terminal.insert(tk.END, message + "\n")
            self.terminal.configure(state=tk.DISABLED)

        if appended:
            self.terminal.see(tk.END)

        self.root.after(150, self._poll_logs)


def main() -> None:
    root = tk.Tk()
    app = FontSenderApp(root)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
