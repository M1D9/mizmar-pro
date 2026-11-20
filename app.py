import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
import os
import sys

# استيراد دالة المعالجة التي كتبناها في run_upscale.py
# (تأكد أن دالة process_video_for_tiktok موجودة في run_upscale.py)
from run_upscale import process_video_for_tiktok

class MizmarApp:
    def __init__(self,root):
        self.root = root
        self.root.title("مزمار تحسين - Mizmar Enhancement")
        self.root.geometry("550x400")
        self.root.configure(padx=20, pady=20, bg="#f0f0f0")

        self.input_path = tk.StringVar()
        self.output_path = tk.StringVar()

        # --- العنوان الرئيسي---
        title_label = tk.Label(root, text="مزمار تحسين", font=("Arial", 20, "bold"), bg="#f0f0f0",fg="#333")
        title_label.pack(pady=(0, 5))

        subtitle_label = tk.Label(root, text="الذكاء الاصطناعي لتحسين فيديوهات تيك توك", font=("Arial", 11), bg="#f0f0f0", fg="#666")
        subtitle_label.pack(pady=(0, 20))

        # --- اختيار ملف الإدخال ---
        frame_input = tk.Frame(root, bg="#f0f0f0")
        frame_input.pack(fill="x", pady=5)
        tk.Label(frame_input, text="الفيديو الأصلي:" , width=12, anchor="w" , bg="#f0f0f0", font=("Arial",10,"bold")).pack(side="left")
        tk.Entry(frame_input, textvariable=self.input_path, state="readonly", font=("Arial", 10)).pack(side="left", expand=True, fill="x", padx=5)
        tk.Button(frame_input, text="تحديد", command=self.select_input, bg="#e0e0e0").pack(side="right")

        # --- اختيار مسار الحفظ ---
        frame_output = tk.Frame(root, bg="#f0f0f0")
        frame_output.pack(fill="x", pady=15)
        tk.Label(frame_output, text="احفظ باسم:", width=12, anchor="w", bg="#f0f0f0", font=("Arial",10, "bold")).pack(side="left")
        tk.Entry(frame_output, textvariable=self.output_path, state="readonly", font=("Arial", 10)).pack(side="left", expand=True, fill="x", padx=5)
        tk.Button(frame_output, text="تحديد", command=self.select_output, bg="#e0e0e0").pack(side="right")

        # --- شريط التقدم ---
        self.progress = ttk.Progressbar(root, orient="horizontal", length=450, mode="indeterminate")
        self.progress.pack(pady=20)

        # --- زر البدء ---
        self.start_btn = tk.Button(root, text="بدء التحسين 🚀", font=("Arial", 14, "bold"), bg="#4CAF50", fg="white", cursor="hand2", command=self.start_processing)
        self.start_btn.pack(pady=10, fill="x", ipady=5)

        # --- رسالة الحالة ---
        self.status_label = tk.Label(root, text="جاهز للبدء...", fg="gray", bg="#f0f0f0", font=("Arial", 10))
        self.status_label.pack()

    def select_input(self):
        file_path = filedialog.askopenfilename(filetypes=[("Video Files", "*.mp4 *.mov *.avi")])
        if file_path:
            self.input_path.set(file_path)
            if not self.output_path.get():
                dir_name = os.path.dirname(file_path)
                self.output_path.set(os.path.join(dir_name, "mizmar_tiktok.mp4"))

    def select_output(self):
        file_path = filedialog.asksaveasfilename(defaultextension=".mp4", filetypes=[("Video Files","*.mp4")])
        if file_path:
            self.output_path.set(file_path)

    def start_processing(self):
        input_file = self.input_path.get()
        output_file = self.output_path.get()
        if not input_file or not output_file:
            messagebox.showerror("خطأ", "الرجاء تحديد الفيديو الأصلي ومسار الحفظ أولاً.")
            return

        self.start_btn.config(state="disabled", text="جاري المعالجة...", bg="#cccccc")
        self.progress.start(10)
        self.status_label.config(text="جاري المعالجة (قد يستغرق هذا عدة دقائق)...", fg="blue")

        threading.Thread(target=self.run_process, args=(input_file, output_file), daemon=True).start()

    def run_process(self, input_file, output_file):
        try:
            process_video_for_tiktok(input_file, output_file)
            self.root.after(0, self.processing_success, output_file) 
        except Exception as e:
            self.root.after(0, self.processing_error, str(e))


    def processing_success(self, output_file):
        self.progress.stop()
        self.start_btn.config(state="normal", text="بدء التحسين 🚀", bg="#4CAF50")
        self.status_label.config(text="تمت المعالجة بنجاح!", fg="green")
        messagebox.showinfo("مزمار تحسين", f"تم تحسين الفيديو بنجاح بواسطة مزمار!\nتم الحفظ في:\n{output_file}")

    def processing_error(self, error_msg):
        self.progress.stop()
        self.start_btn.config(state="normal", text="بدء التحسين 🚀", bg="#4CAF50")
        self.status_label.config(text="حدث خطأ أثناء المعالجة.", fg="red")
        messagebox.showerror("خطأ في مزمار", f"حدث خطأ:\n{error_msg}")

if __name__ == "__main__":
    root = tk.Tk()
    app = MizmarApp(root)
    root.mainloop()
