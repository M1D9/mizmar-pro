# مزمار برو

تستخدم نسخة Windows محرك CPU مضمنّاً، ويمكن اختيار تنزيل محرك CUDA منفصل عند الحاجة. لا يحتاج جهاز المستخدم إلى تثبيت Python أو FFmpeg.

## بناء المحركين

يتطلب جهاز البناء Python 3.11، وPython Launcher (`py`)، وRust، وNode.js. من جذر المشروع:

```powershell
npm --prefix .\mizmar-ui install
.\build-windows.ps1 -CudaReleaseTag v0.1.0 -SkipTauriBuild
```

ينشئ السكربت بيئة `venv-cpu` بـ PyTorch CPU، ويستخدم `venv` الموجود لمحرك CUDA إن كان CUDA-enabled (وإلا ينشئ `venv-cuda`). يبني:

- `mizmar-ui\src-tauri\binaries\mizmar-engine-cpu-x86_64-pc-windows-msvc.exe` — المحرك المضمن، ويوقف البناء إذا بلغ 1 GiB.
- أجزاء محرك CUDA وبيانه في `release-assets\cuda\v0.1.0`. اطبع SHA-256 من مخرجات السكربت وانسخه إلى `CUDA_ENGINE_SHA256` في `src-tauri\src\engine.rs`.

لإعادة بناء محرك CPU فقط بعد تعديل كود Python، استخدم `.\build-windows.ps1 -CpuEngineOnly -SkipTauriBuild`.

يستخرج المحركان ملفات `models` وقت البناء. يحتاج خيار GFPGAN إلى `models\GFPGANv1.4.pth`؛ الوزن غير موجود افتراضياً.

## نشر CUDA على GitHub Releases

حدّ حجم كل ملف Release في GitHub هو 2 GiB ([التوثيق الرسمي](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases#storage-and-bandwidth-quotas))؛ لذلك يقسم السكربت محرك CUDA إلى أجزاء 1.5 GiB ويولّد بياناً يحوي الأسماء والأحجام والبصمة الكاملة. يدمج التطبيق الأجزاء في AppData ويتحقق من SHA-256 المضمن في التطبيق قبل تثبيت الملف.

1. افتح مستودع GitHub ثم **Releases → Draft a new release**، وأنشئ/اختر الوسم نفسه الممرر إلى `-CudaReleaseTag`، مثل `v0.1.0`.
2. أرفق ملف `*.manifest.json` وكل ملفات `*.partNNN` الموجودة في مجلد الإصدار، ثم انشر Release. لا ترفع ملف المحرك الأصلي كملف واحد.
3. انسخ رابط البيان المباشر، بصيغة `https://github.com/<OWNER>/<REPO>/releases/download/v0.1.0/mizmar-engine-cuda-x86_64-pc-windows-msvc.manifest.json`، إلى `CUDA_ENGINE_MANIFEST_URL` في `engine.rs`.
4. طابق SHA-256 الذي طُبع مع القيمة في `CUDA_ENGINE_SHA256` (وحدّثها عند إعادة بناء CUDA)، ثم أعد بناء المثبّت CPU. التطبيق يعطّل التنزيل إلى أن يُضبط الرابط وتطابق البصمة.

يتم التنزيل عبر Rust مباشرةً إلى ملف مؤقت، مع فحص المساحة الحرة، حدّ للحجم، تقدم مرئي، والتحقق من HTTPS واسم الأصول وأحجام الأجزاء والبصمة النهائية. يبقى محرك CPU المضمن كما هو عند فشل الاتصال أو التحقق.

## بناء المثبّت

ضع نسخ Windows القابلة للتوزيع من `ffmpeg.exe` و`ffprobe.exe` داخل `mizmar-ui\src-tauri\resources\ffmpeg`، وافحص الترخيص وأرفق إشعارات البناء الذي اخترته. ثم شغّل من جذر المشروع:

```powershell
.\build-windows.ps1 -CudaReleaseTag v0.1.0
```

تُضمّن Tauri محرك CPU فقط، ويُنشئ الإعداد الحالي مثبّت NSIS من نوع `.exe` تحت `mizmar-ui\src-tauri\target\release\bundle\nsis`. MSI غير مفعّل في `tauri.conf.json`. اختبر المثبّت على Windows نظيف بلا Python أو FFmpeg.

من صفحة الإعدادات، يفعّل المستخدم CUDA بعد تنزيله والتحقق منه. يتطلب CUDA بطاقة NVIDIA وتعريفاً متوافقاً؛ الاختيار يدوي ولا يُجري فحصاً تلقائياً للبطاقة. اختيار المحرك يتم في طبقة Tauri قبل تشغيل Python، لأن `run_upscale.py` لا يستطيع تبديل الملف التنفيذي الذي بدأ تشغيله.

تُحفظ إعدادات المهام والنتائج داخل مجلد بيانات التطبيق. يظل `run.bat` للتشغيل اليدوي أثناء التطوير فقط.

## الأدوات

- رفع الجودة: Real-ESRGAN أو GFPGAN ثم تصدير FFmpeg؛ يتطلب GFPGAN وزن `models/GFPGANv1.4.pth`.
- تمويه الحركة: كثافة 1–10 ونمط `tmix` قابل للاختيار. خيار RIFE يستخدم فلتر FFmpeg `minterpolate` وليس نموذج RIFE العصبي؛ SVPFlow غير متاح في إصدار FFmpeg الحالي.
- الرفع بدون ضغط: يستخدم `tiktok-quality` لترقيع MP4/H.264 بمعامل 10 أو 20 أو 30. ملفات H.265 تُعاد تغليفها بنسخ المسارات مع وسم `hvc1` دون إعادة ترميز؛ معامل التضخيم لا ينطبق عليها.
- تحويل الصيغ: MP4 أو MOV أو AVI أو MKV.

يعرض FFprobe معلومات الملف المختار في صفحات المعالجة. يُحفظ الناتج كملف جديد دون تعديل المصدر. لا تضمن أي من أدوات Lossless منع TikTok من إعادة ترميز الملف بعد الرفع.
