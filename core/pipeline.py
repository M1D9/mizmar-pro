import os 
from .export import export_tiktok
from .upscale import build_upscaler, upscale_video_frames
from .interpolate import interpolate_ffmpeg

def process_video(input_path, output_path, do_upscale=True, do_60fps=True):
    tmp = "output/_tmp.mp4"
    current = input_path

    #1. Upscale
    if do_upscale:
        upsampler = build_upscaler(scale=2)
        # نفكّر الفيديو إلى إطارات، ثم نقوم بترقيتها، وأخيرًا نجمعها مرة أخرى إلى فيديو
        # (نستخدم ffmpeg للقراءة/الكتابة، الكود الكامل أطول — أعطيك إياه بعد ما تجرب الأساس)
        current = tmp
        # هنا يجب أن تقوم بكتابة الكود الذي يقوم بترقية الفيديو باستخدام upsampler، ثم حفظه في current (tmp)

        #2. 60fps
        if do_60fps:
            interpolate_ffmpeg(current, tmp)

        #تصدير الفيديو النهائي باستخدام إعدادات TikTok
        export_tiktok(tmp, output_path, "tiktok_1080p_60")
        os.remove(tmp)  # تنظيف الملف المؤقت
        print(f"تمت معالجة الفيديو وحفظه في: {output_path}")