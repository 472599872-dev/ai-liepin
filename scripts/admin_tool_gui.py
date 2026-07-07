from __future__ import annotations

import json
import os
import platform
import queue
import re
import subprocess
import sys
import threading
from datetime import date, timedelta
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, filedialog, messagebox
from tkinter import scrolledtext, ttk


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from liepin_agent.license import current_machine_id, machine_report, write_signed_license


MACHINE_ID_PATTERN = re.compile(r"^LPA-[0-9A-F]{8}-[0-9A-F]{8}-[0-9A-F]{8}-[0-9A-F]{8}$")
DEFAULT_PRIVATE_KEY = ROOT / "secrets" / "license_private_key.pem"
DEFAULT_OUTPUT = ROOT / "license.json"


def default_expires_at(days: int = 365) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def open_path(path: Path) -> None:
    if platform.system().lower() == "windows":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif platform.system().lower() == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class AdminToolApp:
    def __init__(self) -> None:
        self.root = Tk()
        self.root.title("猎聘招聘智能体授权/打包工具")
        self.root.geometry("980x720")
        self.root.minsize(900, 640)

        self.style = ttk.Style()
        if "clam" in self.style.theme_names():
            self.style.theme_use("clam")
        self.style.configure("TFrame", background="#f5f5f7")
        self.style.configure("TLabel", background="#f5f5f7", foreground="#1d1d1f", font=("Microsoft YaHei UI", 10))
        self.style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        self.style.configure("Hint.TLabel", foreground="#6e6e73")
        self.style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 6))
        self.style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(14, 7))
        self.style.configure("TCheckbutton", background="#f5f5f7", font=("Microsoft YaHei UI", 10))
        self.style.configure("TNotebook", background="#f5f5f7")
        self.style.configure("TNotebook.Tab", font=("Microsoft YaHei UI", 10), padding=(18, 8))

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.build_process: subprocess.Popen[str] | None = None

        self._build_layout()
        self._poll_log_queue()

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="授权/打包工具", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"项目目录：{ROOT}", style="Hint.TLabel").pack(side="left", padx=(18, 0))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)

        license_tab = ttk.Frame(notebook, padding=18)
        build_tab = ttk.Frame(notebook, padding=18)
        machine_tab = ttk.Frame(notebook, padding=18)
        notebook.add(license_tab, text="生成授权")
        notebook.add(build_tab, text="Windows 打包")
        notebook.add(machine_tab, text="本机机器码")

        self._build_license_tab(license_tab)
        self._build_build_tab(build_tab)
        self._build_machine_tab(machine_tab)

        self.status_var = StringVar(value="就绪")
        status = ttk.Label(outer, textvariable=self.status_var, style="Hint.TLabel")
        status.pack(fill="x", pady=(10, 0))

    def _build_license_tab(self, parent: ttk.Frame) -> None:
        form = ttk.Frame(parent)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        self.machine_id_var = StringVar()
        self.customer_var = StringVar()
        self.days_var = StringVar(value="365")
        self.expires_at_var = StringVar(value=default_expires_at(365))
        self.valid_from_var = StringVar(value="")
        self.private_key_var = StringVar(value=str(DEFAULT_PRIVATE_KEY))
        self.output_var = StringVar(value=str(DEFAULT_OUTPUT))
        self.feature_vars = {
            "desktop": BooleanVar(value=True),
            "ai_scoring": BooleanVar(value=True),
            "auto_greeting": BooleanVar(value=True),
        }

        self._row(form, 0, "目标机器码", self.machine_id_var, "粘贴新版应用弹窗里的 LPA-... 机器码")
        ttk.Button(form, text="读取剪贴板", command=self._paste_machine_id).grid(row=0, column=3, padx=(10, 0), sticky="ew")

        self._row(form, 1, "客户/使用者", self.customer_var, "例如：某某公司/张三")
        self._row(form, 2, "授权天数", self.days_var, "输入天数后点右侧按钮刷新到期日")
        ttk.Button(form, text="按天数计算", command=self._refresh_expires_at).grid(row=2, column=3, padx=(10, 0), sticky="ew")
        self._row(form, 3, "到期日期", self.expires_at_var, "YYYY-MM-DD")
        self._row(form, 4, "生效日期", self.valid_from_var, "可空；YYYY-MM-DD")

        ttk.Label(form, text="功能").grid(row=5, column=0, sticky="w", pady=8)
        features = ttk.Frame(form)
        features.grid(row=5, column=1, columnspan=3, sticky="ew", pady=8)
        for feature, label in (("desktop", "桌面应用"), ("ai_scoring", "AI评分"), ("auto_greeting", "自动沟通")):
            ttk.Checkbutton(features, text=label, variable=self.feature_vars[feature]).pack(side="left", padx=(0, 18))

        self._path_row(form, 6, "私钥文件", self.private_key_var, self._choose_private_key)
        self._path_row(form, 7, "输出文件", self.output_var, self._choose_output)

        ttk.Label(form, text="备注").grid(row=8, column=0, sticky="nw", pady=8)
        self.note_text = scrolledtext.ScrolledText(form, height=4, font=("Microsoft YaHei UI", 10), wrap="word")
        self.note_text.grid(row=8, column=1, columnspan=3, sticky="ew", pady=8)

        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(18, 10))
        ttk.Button(actions, text="生成 license.json", style="Primary.TButton", command=self._generate_license).pack(side="left")
        ttk.Button(actions, text="打开输出目录", command=self._open_output_dir).pack(side="left", padx=(10, 0))
        ttk.Button(actions, text="复制命令", command=self._copy_generate_command).pack(side="left", padx=(10, 0))

        self.license_log = scrolledtext.ScrolledText(parent, height=12, font=("Consolas", 10), wrap="word")
        self.license_log.pack(fill="both", expand=True, pady=(8, 0))
        self._log_license("等待生成授权。")

    def _build_build_tab(self, parent: ttk.Frame) -> None:
        info = ttk.Frame(parent)
        info.pack(fill="x")
        info.columnconfigure(1, weight=1)

        self.clean_build_var = BooleanVar(value=True)
        self.no_zip_var = BooleanVar(value=False)
        env_status = "已找到 .env，会随成品复制" if (ROOT / ".env").exists() else "未找到 .env，成品不会自带 API Key"

        ttk.Label(info, text="项目目录").grid(row=0, column=0, sticky="w", pady=8)
        ttk.Label(info, text=str(ROOT), style="Hint.TLabel").grid(row=0, column=1, sticky="w", pady=8)
        ttk.Label(info, text=".env 状态").grid(row=1, column=0, sticky="w", pady=8)
        ttk.Label(info, text=env_status, style="Hint.TLabel").grid(row=1, column=1, sticky="w", pady=8)

        options = ttk.Frame(parent)
        options.pack(fill="x", pady=(10, 8))
        ttk.Checkbutton(options, text="清理旧 build/dist 后重新打包", variable=self.clean_build_var).pack(side="left")
        ttk.Checkbutton(options, text="只生成目录，不压缩 zip", variable=self.no_zip_var).pack(side="left", padx=(24, 0))

        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(8, 10))
        ttk.Button(actions, text="开始打包", style="Primary.TButton", command=self._start_build).pack(side="left")
        ttk.Button(actions, text="停止打包", command=self._stop_build).pack(side="left", padx=(10, 0))
        ttk.Button(actions, text="打开 dist", command=lambda: open_path(ROOT / "dist")).pack(side="left", padx=(10, 0))
        ttk.Button(actions, text="复制打包命令", command=self._copy_build_command).pack(side="left", padx=(10, 0))

        self.build_log = scrolledtext.ScrolledText(parent, height=20, font=("Consolas", 10), wrap="word")
        self.build_log.pack(fill="both", expand=True)
        self._log_build("Windows 打包需要在 Windows 10/11 上运行。")
        self._log_build("推荐命令：.\\scripts\\build_windows.ps1 -Clean")

    def _build_machine_tab(self, parent: ttk.Frame) -> None:
        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(0, 10))
        ttk.Button(actions, text="刷新本机机器码", style="Primary.TButton", command=self._refresh_machine_report).pack(side="left")
        ttk.Button(actions, text="复制机器码", command=self._copy_current_machine_id).pack(side="left", padx=(10, 0))
        ttk.Button(actions, text="复制完整报告", command=self._copy_machine_report).pack(side="left", padx=(10, 0))

        self.machine_report_text = scrolledtext.ScrolledText(parent, height=26, font=("Consolas", 10), wrap="word")
        self.machine_report_text.pack(fill="both", expand=True)
        self._refresh_machine_report()

    def _row(self, parent: ttk.Frame, row: int, label: str, variable: StringVar, hint: str) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=8)
        entry = ttk.Entry(parent, textvariable=variable, font=("Microsoft YaHei UI", 10))
        entry.grid(row=row, column=1, sticky="ew", pady=8)
        ttk.Label(parent, text=hint, style="Hint.TLabel").grid(row=row, column=2, sticky="w", padx=(10, 0), pady=8)

    def _path_row(self, parent: ttk.Frame, row: int, label: str, variable: StringVar, command) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=8)
        ttk.Entry(parent, textvariable=variable, font=("Microsoft YaHei UI", 10)).grid(row=row, column=1, columnspan=2, sticky="ew", pady=8)
        ttk.Button(parent, text="选择", command=command).grid(row=row, column=3, padx=(10, 0), sticky="ew")

    def _paste_machine_id(self) -> None:
        try:
            text = self.root.clipboard_get().strip().upper()
        except Exception:
            messagebox.showwarning("剪贴板为空", "没有读取到剪贴板内容。")
            return
        self.machine_id_var.set(text)

    def _refresh_expires_at(self) -> None:
        try:
            days = int(self.days_var.get().strip())
        except ValueError:
            messagebox.showerror("授权天数错误", "授权天数必须是数字。")
            return
        if days <= 0:
            messagebox.showerror("授权天数错误", "授权天数必须大于 0。")
            return
        self.expires_at_var.set(default_expires_at(days))

    def _choose_private_key(self) -> None:
        path = filedialog.askopenfilename(
            title="选择授权私钥",
            initialdir=str((ROOT / "secrets") if (ROOT / "secrets").exists() else ROOT),
            filetypes=[("PEM files", "*.pem"), ("All files", "*.*")],
        )
        if path:
            self.private_key_var.set(path)

    def _choose_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="保存 license.json",
            initialdir=str(ROOT),
            initialfile="license.json",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.output_var.set(path)

    def _selected_features(self) -> list[str]:
        return [name for name, var in self.feature_vars.items() if var.get()]

    def _generate_license(self) -> None:
        machine_id = self.machine_id_var.get().strip().upper()
        customer = self.customer_var.get().strip()
        expires_at = self.expires_at_var.get().strip()
        valid_from = self.valid_from_var.get().strip()
        private_key = Path(self.private_key_var.get().strip())
        output = Path(self.output_var.get().strip())
        note = self.note_text.get("1.0", "end").strip()

        if not MACHINE_ID_PATTERN.match(machine_id):
            messagebox.showerror("机器码格式错误", "机器码应类似：LPA-XXXXXXXX-XXXXXXXX-XXXXXXXX-XXXXXXXX。")
            return
        if not customer:
            messagebox.showerror("缺少客户名称", "请填写客户/使用者名称。")
            return
        if not private_key.exists():
            messagebox.showerror("私钥不存在", f"找不到私钥文件：{private_key}")
            return
        if not output.parent.exists():
            output.parent.mkdir(parents=True, exist_ok=True)

        try:
            signed = write_signed_license(
                private_key_path=private_key,
                output_path=output,
                machine_id=machine_id,
                customer=customer,
                expires_at=expires_at,
                valid_from=valid_from,
                features=self._selected_features(),
                note=note,
            )
        except Exception as exc:
            messagebox.showerror("生成失败", str(exc))
            self._log_license(f"生成失败：{exc}")
            return

        self.status_var.set(f"已生成授权：{output}")
        self._log_license("")
        self._log_license("已生成 license.json")
        self._log_license(f"输出：{output}")
        self._log_license(f"客户：{signed['customer']}")
        self._log_license(f"机器码：{signed['machine_id']}")
        self._log_license(f"有效期至：{signed['expires_at']}")
        self._log_license(f"功能：{', '.join(signed.get('features', [])) or '-'}")
        messagebox.showinfo("生成成功", f"已生成：\n{output}")

    def _open_output_dir(self) -> None:
        output = Path(self.output_var.get().strip() or str(DEFAULT_OUTPUT))
        open_path(output.parent)

    def _copy_generate_command(self) -> None:
        features = ",".join(self._selected_features())
        command = (
            "python scripts/license_tool.py generate "
            f"--machine-id {self.machine_id_var.get().strip().upper()} "
            f"--customer \"{self.customer_var.get().strip()}\" "
            f"--expires-at {self.expires_at_var.get().strip()} "
            f"--features {features} "
            f"--output \"{self.output_var.get().strip()}\""
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(command)
        self.status_var.set("已复制生成命令")

    def _build_command(self) -> list[str]:
        script = ROOT / "scripts" / "build_windows.ps1"
        command = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)]
        if self.clean_build_var.get():
            command.append("-Clean")
        if self.no_zip_var.get():
            command.append("-NoZip")
        return command

    def _start_build(self) -> None:
        if self.build_process and self.build_process.poll() is None:
            messagebox.showwarning("正在打包", "当前已有打包进程在运行。")
            return
        if platform.system().lower() != "windows":
            self._log_build("当前不是 Windows，不能直接执行 PowerShell 打包。请复制命令到 Windows 构建机运行。")
            messagebox.showinfo("需要 Windows", "Windows 打包需要在 Windows 10/11 上运行。")
            return
        command = self._build_command()
        self._log_build("")
        self._log_build("开始打包：")
        self._log_build(" ".join(command))
        self.status_var.set("正在打包...")

        thread = threading.Thread(target=self._run_build_process, args=(command,), daemon=True)
        thread.start()

    def _run_build_process(self, command: list[str]) -> None:
        try:
            self.build_process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert self.build_process.stdout is not None
            for line in self.build_process.stdout:
                self.log_queue.put(line.rstrip())
            code = self.build_process.wait()
            self.log_queue.put(f"__BUILD_DONE__{code}")
        except Exception as exc:
            self.log_queue.put(f"__BUILD_ERROR__{exc}")

    def _stop_build(self) -> None:
        if self.build_process and self.build_process.poll() is None:
            self.build_process.terminate()
            self._log_build("已发送停止信号。")
            self.status_var.set("已请求停止打包")
        else:
            self._log_build("当前没有正在运行的打包进程。")

    def _copy_build_command(self) -> None:
        command = ".\\scripts\\build_windows.ps1"
        if self.clean_build_var.get():
            command += " -Clean"
        if self.no_zip_var.get():
            command += " -NoZip"
        self.root.clipboard_clear()
        self.root.clipboard_append(command)
        self.status_var.set("已复制打包命令")

    def _poll_log_queue(self) -> None:
        while True:
            try:
                item = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if item.startswith("__BUILD_DONE__"):
                code = item.replace("__BUILD_DONE__", "", 1)
                self._log_build(f"打包结束，退出码：{code}")
                self.status_var.set("打包完成" if code == "0" else f"打包失败：{code}")
            elif item.startswith("__BUILD_ERROR__"):
                message = item.replace("__BUILD_ERROR__", "", 1)
                self._log_build(f"打包异常：{message}")
                self.status_var.set("打包异常")
            else:
                self._log_build(item)
        self.root.after(200, self._poll_log_queue)

    def _refresh_machine_report(self) -> None:
        report = machine_report()
        self.current_report = report
        self.machine_report_text.delete("1.0", "end")
        self.machine_report_text.insert("end", json.dumps(report, ensure_ascii=False, indent=2))
        self.status_var.set(f"本机机器码：{report['machine_id']}")

    def _copy_current_machine_id(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(current_machine_id())
        self.status_var.set("已复制本机机器码")

    def _copy_machine_report(self) -> None:
        text = self.machine_report_text.get("1.0", "end").strip()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set("已复制完整机器码报告")

    def _log_license(self, message: str) -> None:
        self.license_log.insert("end", message + "\n")
        self.license_log.see("end")

    def _log_build(self, message: str) -> None:
        self.build_log.insert("end", message + "\n")
        self.build_log.see("end")

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    AdminToolApp().run()


if __name__ == "__main__":
    main()
