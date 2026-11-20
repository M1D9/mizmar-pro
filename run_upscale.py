import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import warnings
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlparse

import cv2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

warnings.filterwarnings("ignore")

RESOLUTIONS = {
    "1080p": (1080, 1920),
    "2k": (1440, 2560),
    "4k": (2160, 3840),
}
CODECS = {"libx264", "libx265"}
FRAME_RATES = {60, 120}
AI_MODELS = {"general", "faces"}
TOOL_TYPES = {"upscale", "motion_blur", "convert", "lossless", "high_quality_master"}
BLUR_STYLES = {"cinematic", "sports", "fast"}
INTERPOLATION_ENGINES = {"rife", "svpflow"}
TARGET_FORMATS = {"mp4", "mov", "avi", "mkv"}
CONVERT_QUALITIES = {"high", "medium", "low"}


def get_runtime_directory():
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    return Path(__file__).resolve().parent


def get_output_directory(project_root):
    configured_directory = os.environ.get("MIZMAR_OUTPUT_DIR")
    output_directory = (
        Path(configured_directory).expanduser().resolve()
        if configured_directory
        else Path(project_root) / "output"
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    return output_directory


def get_media_tool(tool_name: str) -> str:
    executable_name = f"{tool_name}.exe" if os.name == "nt" else tool_name
    configured_directory = os.environ.get("MIZMAR_FFMPEG_DIR")
    if configured_directory:
        bundled_executable = Path(configured_directory) / executable_name
        if bundled_executable.is_file():
            return str(bundled_executable)

    executable_path = shutil.which(tool_name)
    if executable_path:
        return executable_path
    raise FileNotFoundError(
        f"لم يتم العثور على {tool_name}.exe ضمن موارد التطبيق أو PATH."
        if os.name == "nt"
        else f"لم يتم العثور على {tool_name} ضمن موارد التطبيق أو PATH."
    )


def read_settings(config_path):
    with open(config_path, "r", encoding="utf-8") as settings_file:
        settings = json.load(settings_file)
    if not isinstance(settings, dict):
        raise ValueError("يجب أن يحتوي config.json على كائن إعدادات.")

    job_type = settings.get("job_type", "process")
    if job_type == "download":
        url = settings.get("url")
        authorized = settings.get("authorized_download", False)
        parsed_url = urlparse(url) if isinstance(url, str) else None
        if parsed_url is None or parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("رابط التنزيل يجب أن يبدأ بـ http:// أو https://.")
        if authorized is not True:
            raise ValueError("أكّد امتلاكك حق تنزيل هذا المحتوى أو الحصول على إذن.")
        return {"job_type": "download", "url": url}
    if job_type not in {"process", "probe"}:
        raise ValueError(f"نوع المهمة غير مدعوم: {job_type}")

    input_path = settings.get("input_path")
    tool_type = settings.get("tool_type", "upscale")

    if not isinstance(input_path, str) or not input_path.strip():
        raise ValueError("ملف الإعدادات لا يحتوي على input_path صالح.")
    if not isinstance(tool_type, str) or tool_type not in TOOL_TYPES:
        raise ValueError(f"نوع الأداة غير مدعوم: {tool_type}")
    input_file = Path(input_path).expanduser().resolve()
    if not input_file.is_file():
        raise FileNotFoundError(f"ملف الفيديو غير موجود: {input_file}")

    if job_type == "probe":
        return {"job_type": "probe", "input_path": input_file}

    common_settings = {"job_type": "process", "input_path": input_file, "tool_type": tool_type}
    if tool_type == "lossless":
        multiplier = settings.get("multiplier", 10)
        if isinstance(multiplier, bool) or not isinstance(multiplier, int) or multiplier not in {10, 20, 30}:
            raise ValueError("قيمة multiplier المدعومة لأداة Lossless هي 10 أو 20 أو 30.")
        return {**common_settings, "multiplier": multiplier}

    if tool_type == "upscale":
        resolution = settings.get("resolution")
        codec = settings.get("codec")
        fps = settings.get("fps")
        ai_model = settings.get("ai_model", "general")
        denoise = settings.get("denoise", False)
        if not isinstance(resolution, str) or resolution not in {*RESOLUTIONS, "original"}:
            raise ValueError(f"الدقة غير مدعومة: {resolution}")
        if not isinstance(codec, str) or codec not in CODECS:
            raise ValueError(f"الكوديك غير مدعوم: {codec}")
        if fps == "original":
            fps = None
        elif isinstance(fps, bool) or not isinstance(fps, int) or fps not in FRAME_RATES:
            raise ValueError(f"معدل الإطارات غير مدعوم: {fps}")
        if not isinstance(ai_model, str) or ai_model not in AI_MODELS:
            raise ValueError(f"نموذج الذكاء الاصطناعي غير مدعوم: {ai_model}")
        if not isinstance(denoise, bool):
            raise ValueError("يجب أن تكون قيمة denoise منطقية.")
        return {
            **common_settings,
            "resolution": resolution,
            "codec": codec,
            "fps": fps,
            "ai_model": ai_model,
            "denoise": denoise,
        }

    if tool_type == "motion_blur":
        amount = settings.get("blur_amount")
        if amount is None:
            legacy_intensity = settings.get("blur_intensity", "medium")
            legacy_amounts = {"low": 2, "medium": 3, "high": 5}
            if not isinstance(legacy_intensity, str) or legacy_intensity not in legacy_amounts:
                raise ValueError(f"شدة تمويه الحركة غير مدعومة: {legacy_intensity}")
            amount = legacy_amounts[legacy_intensity]
        if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 10:
            raise ValueError("يجب أن تكون كثافة التمويه عدداً صحيحاً من 1 إلى 10.")

        blur_style = settings.get("blur_style", "cinematic")
        if not isinstance(blur_style, str) or blur_style not in BLUR_STYLES:
            raise ValueError(f"نوع تمويه الحركة غير مدعوم: {blur_style}")
        interpolation_engine = settings.get("interpolation_engine")
        if interpolation_engine is not None and (
            not isinstance(interpolation_engine, str)
            or interpolation_engine not in INTERPOLATION_ENGINES
        ):
            raise ValueError(f"محرك توليد الإطارات غير مدعوم: {interpolation_engine}")
        return {
            **common_settings,
            "blur_amount": amount,
            "blur_style": blur_style,
            "interpolation_engine": interpolation_engine,
        }

    if tool_type == "high_quality_master":
        codec = settings.get("master_codec", "libx264")
        if not isinstance(codec, str) or codec not in CODECS:
            raise ValueError(f"كوديك النسخة الرئيسية غير مدعوم: {codec}")
        return {**common_settings, "master_codec": codec}

    target_format = settings.get("target_format")
    quality = settings.get("quality")
    if not isinstance(target_format, str) or target_format not in TARGET_FORMATS:
        raise ValueError(f"صيغة الإخراج غير مدعومة: {target_format}")
    if not isinstance(quality, str) or quality not in CONVERT_QUALITIES:
        raise ValueError(f"مستوى الجودة غير مدعوم: {quality}")
    return {**common_settings, "target_format": target_format, "quality": quality}


def upscale_video_frames(input_path, output_path, ai_model="general"):
    from core.upscale import build_face_restorer, build_upscaler

    if ai_model == "faces":
        print("1. جاري تحميل نموذج GFPGAN المخصص للوجوه...", flush=True)
        upsampler = build_face_restorer(scale=2)
    elif ai_model == "general":
        print("1. جاري تحميل نموذج Real-ESRGAN العام...", flush=True)
        upsampler = build_upscaler(scale=2)
    else:
        raise ValueError(f"نموذج الذكاء الاصطناعي غير مدعوم: {ai_model}")

    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise RuntimeError(f"تعذر فتح الفيديو: {input_path}")

    original_fps = capture.get(cv2.CAP_PROP_FPS)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if width <= 0 or height <= 0 or frame_count <= 0 or original_fps <= 0:
        capture.release()
        raise RuntimeError("تعذر قراءة معلومات الإطارات من الفيديو.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer = None
    completed = False
    try:
        writer = cv2.VideoWriter(
            str(output_path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            original_fps,
            (width * 2, height * 2),
        )
        if not writer.isOpened():
            raise RuntimeError("تعذر إنشاء ملف الفيديو المؤقت.")

        print(f"2. معالجة الإطارات بالذكاء الاصطناعي (العدد: {frame_count})...", flush=True)
        count = 0
        while True:
            has_frame, frame = capture.read()
            if not has_frame:
                break

            if ai_model == "faces":
                _, _, enhanced_frame = upsampler.enhance(
                    frame,
                    has_aligned=False,
                    only_center_face=False,
                    paste_back=True,
                )
                if enhanced_frame is None:
                    raise RuntimeError("لم يُرجع GFPGAN إطاراً محسّناً.")
            else:
                enhanced_frame, _ = upsampler.enhance(frame, outscale=2)
            writer.write(enhanced_frame)
            count += 1
            if count % 10 == 0 or count == frame_count:
                print(f"تمت معالجة {count}/{frame_count} إطار", flush=True)
        completed = count > 0
    finally:
        capture.release()
        if writer is not None:
            writer.release()
        if not completed:
            output_path.unlink(missing_ok=True)


def get_target_dimensions(resolution, source_width, source_height):
    if source_width <= 0 or source_height <= 0:
        raise ValueError("أبعاد الفيديو المصدر غير صالحة.")
    if resolution == "original":
        width = source_width - source_width % 2
        height = source_height - source_height % 2
        if width == 0 or height == 0:
            raise ValueError("أبعاد الفيديو الأصلية صغيرة جداً للتصدير.")
        return width, height
    if resolution not in RESOLUTIONS:
        raise ValueError(f"الدقة غير مدعومة: {resolution}")

    short_side, long_side = RESOLUTIONS[resolution]
    return (long_side, short_side) if source_width >= source_height else (short_side, long_side)


def build_video_filters(resolution, fps, denoise, source_width, source_height):
    target_width, target_height = get_target_dimensions(
        resolution, source_width, source_height
    )
    filters = []
    if denoise:
        filters.append("hqdn3d=1.5:1.5:6:6")
    filters.extend(["eq=contrast=1.05:saturation=1.1", "unsharp=5:5:0.8:5:5:0.0"])
    filters.extend(
        [
            f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease",
            f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black",
        ]
    )
    if fps is not None:
        filters.append(f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1")
    return ",".join(filters)


def apply_motion_blur(filters, amount, blur_style):
    if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 10:
        raise ValueError("يجب أن تكون كثافة التمويه عدداً صحيحاً من 1 إلى 10.")
    if blur_style not in BLUR_STYLES:
        raise ValueError(f"نوع تمويه الحركة غير مدعوم: {blur_style}")

    frame_count = max(2, amount)
    if blur_style == "cinematic":
        weights = " ".join(str(index + 1) for index in range(frame_count))
    elif blur_style == "sports":
        weights = " ".join("1" for _ in range(frame_count - 1)) + " 2"
    else:
        weights = " ".join("1" for _ in range(frame_count - 1)) + " 3"
    filters.append(f"tmix=frames={frame_count}:weights='{weights}'")


def export_video(
    video_input,
    audio_input,
    output_path,
    codec,
    fps,
    resolution,
    denoise,
    source_width,
    source_height,
):
    ffmpeg_exe = get_media_tool("ffmpeg")

    target_width, target_height = get_target_dimensions(
        resolution, source_width, source_height
    )
    video_filter = build_video_filters(
        resolution, fps, denoise, source_width, source_height
    )
    video_bitrate = {"4k": "45M", "2k": "25M", "1080p": "12M", "original": "12M"}[
        resolution
    ]
    buffer_size = {"4k": "90M", "2k": "50M", "1080p": "24M", "original": "24M"}[
        resolution
    ]
    print(
        f"التصدير النهائي: {target_width}x{target_height}, "
        f"{fps}fps, {codec}",
        flush=True,
    )
    command = [
        ffmpeg_exe,
        "-y",
        "-i",
        str(video_input),
        "-i",
        str(audio_input),
        "-filter_complex",
        f"[0:v]{video_filter}[v]",
        "-map",
        "[v]",
        "-map",
        "1:a:0?",
        "-c:v",
        codec,
        *(["-tag:v", "hvc1"] if codec == "libx265" else []),
        "-preset",
        "slow",
        "-crf",
        "16",
        "-maxrate",
        video_bitrate,
        "-bufsize",
        buffer_size,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "320k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    subprocess.run(command, check=True)


def process_video_for_tiktok(
    input_path,
    output_path,
    codec="libx264",
    fps=60,
    resolution="1080p",
    ai_model="general",
    denoise=False,
):
    input_path = Path(input_path).resolve()
    output_path = Path(output_path).resolve()
    source_info = probe_video(input_path)
    source_width = source_info["width"]
    source_height = source_info["height"]
    if not isinstance(source_width, int) or not isinstance(source_height, int):
        raise RuntimeError("تعذر تحديد أبعاد الفيديو المصدر.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_video = None
    try:
        video_input = input_path
        temp_handle, temp_name = tempfile.mkstemp(
            prefix="mizmar_upscaled_", suffix=".mp4", dir=str(output_path.parent)
        )
        os.close(temp_handle)
        temp_video = Path(temp_name)
        upscale_video_frames(input_path, temp_video, ai_model)
        video_input = temp_video

        export_video(
            video_input,
            input_path,
            output_path,
            codec,
            fps,
            resolution,
            denoise,
            source_width,
            source_height,
        )
    finally:
        if temp_video is not None:
            temp_video.unlink(missing_ok=True)

    print(f"اكتملت المعالجة: {output_path}", flush=True)
    return output_path


def upscale_video(
    input_path,
    output_path,
    codec,
    fps,
    resolution,
    ai_model="general",
    denoise=False,
):
    return process_video_for_tiktok(
        input_path,
        output_path,
        codec,
        fps,
        resolution,
        ai_model=ai_model,
        denoise=denoise,
    )


def motion_blur_video(
    input_path,
    output_path,
    blur_amount,
    blur_style="cinematic",
    interpolation_engine=None,
):
    ffmpeg_exe = get_media_tool("ffmpeg")

    filters = []
    if interpolation_engine == "svpflow":
        raise RuntimeError(
            "محرك SVPFlow غير متاح في هذا التطبيق. اختر خيار RIFE الذي يستخدم فلتر "
            "FFmpeg minterpolate (وليس نموذج RIFE العصبي)."
        )
    if interpolation_engine == "rife":
        source_fps = probe_video(input_path)["fps"]
        if source_fps is None or source_fps <= 0:
            raise RuntimeError("تعذر تحديد معدل إطارات المصدر للاستيفاء.")
        target_fps = min(120, max(1, round(source_fps * 2)))
        filters.append(
            f"minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1"
        )
    elif interpolation_engine is not None:
        raise ValueError(f"محرك توليد الإطارات غير مدعوم: {interpolation_engine}")
    apply_motion_blur(filters, blur_amount, blur_style)
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_exe,
        "-y",
        "-i",
        str(input_path),
        "-vf",
        ",".join(filters),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    subprocess.run(command, check=True)
    print(
        f"اكتمل تمويه الحركة (الكثافة {blur_amount}/10، النمط {blur_style}): "
        f"{output_path}",
        flush=True,
    )
    return output_path


def convert_video(input_path, output_path, target_format, quality):
    ffmpeg_exe = get_media_tool("ffmpeg")
    if target_format not in TARGET_FORMATS:
        raise ValueError(f"صيغة الإخراج غير مدعومة: {target_format}")
    quality_values = {
        "high": {"crf": "18", "vp9_crf": "24", "mpeg4_q": "3"},
        "medium": {"crf": "23", "vp9_crf": "32", "mpeg4_q": "6"},
        "low": {"crf": "28", "vp9_crf": "40", "mpeg4_q": "10"},
    }
    if quality not in quality_values:
        raise ValueError(f"مستوى الجودة غير مدعوم: {quality}")

    output_path = Path(output_path).with_suffix(f".{target_format}").resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg_exe,
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-pix_fmt",
        "yuv420p",
    ]
    values = quality_values[quality]
    if target_format == "webm":
        command.extend(
            [
                "-c:v",
                "libvpx-vp9",
                "-crf",
                values["vp9_crf"],
                "-b:v",
                "0",
                "-deadline",
                "good",
                "-cpu-used",
                "2",
                "-c:a",
                "libopus",
                "-b:a",
                "128k",
            ]
        )
    elif target_format == "avi":
        command.extend(
            [
                "-c:v",
                "mpeg4",
                "-q:v",
                values["mpeg4_q"],
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
            ]
        )
    else:
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                values["crf"],
                "-c:a",
                "aac",
                "-b:a",
                "192k",
            ]
        )
    if target_format in {"mp4", "mov"}:
        command.extend(["-movflags", "+faststart"])
    command.append(str(output_path))
    print(f"التصدير النهائي: {target_format}، الجودة {quality}", flush=True)
    subprocess.run(command, check=True)
    print(f"اكتمل التحويل ({target_format}, {quality}): {output_path}", flush=True)
    return output_path


def parse_optional_float(value):
    if value is None or value == "" or value == "N/A":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_frame_rate(value):
    if not isinstance(value, str) or not value or value == "N/A":
        return None
    try:
        rate = float(Fraction(value))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    return rate if math.isfinite(rate) else None


def probe_video(input_path):
    input_file = Path(input_path).expanduser().resolve()
    if not input_file.is_file():
        raise FileNotFoundError(f"ملف الفيديو غير موجود: {input_file}")

    ffprobe_exe = get_media_tool("ffprobe")

    command = [
        ffprobe_exe,
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name,codec_tag_string,width,height,avg_frame_rate,r_frame_rate,duration:format=duration",
        "-of",
        "json",
        str(input_file),
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError as error:
        details = error.stderr.strip() if error.stderr else str(error)
        raise RuntimeError(f"تعذر قراءة معلومات الفيديو عبر FFprobe: {details}") from error

    metadata = json.loads(result.stdout)
    video_stream = next(
        (stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "video"),
        None,
    )
    if video_stream is None:
        raise RuntimeError(f"لم يعثر FFprobe على مسار فيديو في الملف: {input_file}")

    format_info = metadata.get("format", {})
    duration = parse_optional_float(format_info.get("duration"))
    if duration is None:
        duration = parse_optional_float(video_stream.get("duration"))
    frame_rate = parse_frame_rate(video_stream.get("avg_frame_rate"))
    if frame_rate is None:
        frame_rate = parse_frame_rate(video_stream.get("r_frame_rate"))
    return {
        "file_name": input_file.name,
        "file_size_bytes": input_file.stat().st_size,
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "fps": frame_rate,
        "duration_seconds": duration,
        "codec": video_stream.get("codec_name"),
        "codec_tag": video_stream.get("codec_tag_string"),
    }


def read_mp4_top_level_boxes(input_file):
    input_file = Path(input_file)
    file_size = input_file.stat().st_size
    boxes = []
    offset = 0

    with input_file.open("rb") as source:
        while offset < file_size:
            source.seek(offset)
            header = source.read(8)
            if len(header) != 8:
                raise ValueError(f"ترويسة MP4 غير مكتملة عند الإزاحة {offset}.")

            box_size = int.from_bytes(header[:4], "big")
            box_type = header[4:8].decode("ascii", errors="replace")
            header_size = 8
            if box_size == 1:
                extended_size = source.read(8)
                if len(extended_size) != 8:
                    raise ValueError(f"حجم صندوق MP4 غير مكتمل: {box_type}.")
                box_size = int.from_bytes(extended_size, "big")
                header_size = 16
            elif box_size == 0:
                box_size = file_size - offset

            if box_size < header_size or offset + box_size > file_size:
                raise ValueError(f"حجم صندوق MP4 غير صالح: {box_type}.")

            boxes.append((box_type, offset, box_size))
            offset += box_size

    return boxes


def validate_mp4_container(input_file):
    box_types = {box_type for box_type, _, _ in read_mp4_top_level_boxes(input_file)}
    missing_boxes = {"ftyp", "moov", "mdat"} - box_types
    if missing_boxes:
        raise ValueError(
            "حاوية MP4 غير مكتملة؛ الصناديق المفقودة: "
            + ", ".join(sorted(missing_boxes))
        )


def remux_hevc_mp4(input_file, output_file):
    ffmpeg_exe = get_media_tool("ffmpeg")

    validate_mp4_container(input_file)
    temporary_output = output_file.with_name(f"{output_file.stem}.tmp.mp4")
    temporary_output.unlink(missing_ok=True)
    command = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_file),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
        "-c",
        "copy",
        "-tag:v",
        "hvc1",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(temporary_output),
    ]
    try:
        subprocess.run(command, check=True)
        if not temporary_output.is_file() or temporary_output.stat().st_size == 0:
            raise RuntimeError("لم يُنشأ ملف MP4 الناتج عن إعادة التغليف.")
        output_info = probe_video(temporary_output)
        if output_info["codec"] != "hevc" or output_info["codec_tag"] != "hvc1":
            raise RuntimeError(
                "تعذر التحقق من مخرج H.265: المطلوب codec=hevc ووسم hvc1."
            )
        os.replace(temporary_output, output_file)
    except Exception:
        temporary_output.unlink(missing_ok=True)
        raise


def apply_lossless_patch(input_video, multiplier, output_directory=None):
    if isinstance(multiplier, bool) or not isinstance(multiplier, int) or multiplier not in {10, 20, 30}:
        raise ValueError("قيمة multiplier المدعومة هي 10 أو 20 أو 30.")

    input_file = Path(input_video).expanduser().resolve()
    if input_file.suffix.lower() != ".mp4":
        raise ValueError("تدعم معالجة Lossless ملفات MP4 فقط، ولا تجري إعادة ترميز.")

    video_info = probe_video(input_file)
    if video_info["codec"] not in {"h264", "hevc"}:
        raise ValueError(
            "يدعم Lossless ملفات MP4 بترميز H.264 أو H.265 فقط، ولا يجري إعادة ترميز."
        )
    if output_directory is None:
        output_directory = get_output_directory(get_runtime_directory())
    else:
        output_directory = Path(output_directory)
        output_directory.mkdir(parents=True, exist_ok=True)
    source_id = hashlib.sha1(str(input_file).encode("utf-8")).hexdigest()[:8]
    method_suffix = f"{multiplier}x" if video_info["codec"] == "h264" else "hvc1"
    output_path = output_directory / (
        f"{input_file.stem}_mizmar_lossless_{method_suffix}_{source_id}.mp4"
    )

    if video_info["codec"] == "hevc":
        print(
            "تطبيع حاوية H.265 بنسخ المسارات دون إعادة ترميز مع وسم hvc1؛ "
            "معامل التضخيم لا ينطبق على H.265.",
            flush=True,
        )
        remux_hevc_mp4(input_file, output_path)
        print(f"اكتملت إعادة تغليف H.265 دون إعادة ترميز: {output_path}", flush=True)
        return output_path
    try:
        from tiktok_quality import transform

        print(f"تطبيق ترقيع MP4 مباشرة دون إعادة ترميز (multiplier={multiplier})...", flush=True)
        transform(str(input_file), str(output_path), multiplier=multiplier)
    except ImportError as error:
        raise RuntimeError(
            "مكتبة tiktok-quality غير مثبتة. ثبّتها باستخدام: pip install tiktok-quality"
        ) from error
    except SystemExit as error:
        raise RuntimeError(f"رفضت مكتبة tiktok-quality الملف: {error}") from error
    except Exception as error:
        raise RuntimeError(f"فشل ترقيع MP4 دون تغيير الملف الأصلي: {error}") from error

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError(f"لم يُنشأ ملف Lossless الناتج كما هو متوقع: {output_path}")

    print(f"اكتمل ترقيع Lossless: {output_path}", flush=True)
    return output_path


def export_high_quality_master(input_path, output_path, codec):
    ffmpeg_exe = get_media_tool("ffmpeg")
    ffprobe_exe = get_media_tool("ffprobe")
    if codec not in CODECS:
        raise ValueError(f"كوديك النسخة الرئيسية غير مدعوم: {codec}")

    input_path = Path(input_path).resolve()
    output_path = Path(output_path).with_suffix(".mp4").resolve()
    if input_path == output_path:
        raise ValueError("يجب أن يختلف مسار ملف الإخراج عن ملف المصدر.")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    probe_command = [
        ffprobe_exe,
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name",
        "-show_entries",
        "format=format_name",
        "-of",
        "json",
        str(input_path),
    ]
    probe_result = subprocess.run(
        probe_command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    source_info = json.loads(probe_result.stdout)
    streams = source_info.get("streams", [])
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    if not video_streams:
        raise RuntimeError(f"لم يعثر FFprobe على مسار فيديو في الملف: {input_path}")

    expected_video_codec = "h264" if codec == "libx264" else "hevc"
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    can_copy_video = video_streams[0].get("codec_name") == expected_video_codec
    can_copy_audio = bool(audio_streams) and all(
        stream.get("codec_name") == "aac" for stream in audio_streams
    )

    command = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
    ]
    if can_copy_video:
        command.extend(["-c:v", "copy"])
        if codec == "libx265":
            command.extend(["-tag:v", "hvc1"])
        print("كوديك الفيديو متوافق؛ سيتم نسخ مسار الفيديو دون إعادة ترميز.", flush=True)
    else:
        command.extend(
            [
                "-c:v",
                codec,
                "-preset",
                "slow",
                "-crf",
                "16",
                "-maxrate",
                "15M",
                "-bufsize",
                "30M",
                "-pix_fmt",
                "yuv420p",
            ]
        )
        if codec == "libx265":
            command.extend(["-tag:v", "hvc1"])
        print(
            f"إعادة ترميز الفيديو: {codec}، CRF 16، سقف معدل البت 15 Mbps.",
            flush=True,
        )
    if audio_streams and can_copy_audio:
        command.extend(["-c:a", "copy"])
    elif audio_streams:
        command.extend(["-c:a", "aac", "-b:a", "320k"])
        print("سيُحوّل الصوت إلى AAC بمعدل 320 kbps.", flush=True)
    command.extend(["-movflags", "+faststart", str(output_path)])
    subprocess.run(command, check=True)
    print(f"اكتمل تصدير النسخة الرئيسية: {output_path}", flush=True)
    return output_path


def print_video_info(video_path):
    ffprobe_exe = get_media_tool("ffprobe")

    command = [
        ffprobe_exe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,width,height,avg_frame_rate,bit_rate",
        "-show_entries",
        "format=bit_rate,duration",
        "-of",
        "json",
        str(video_path),
    ]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    metadata = json.loads(result.stdout)
    streams = metadata.get("streams", [])
    if not streams:
        raise RuntimeError(f"لم يعثر FFprobe على مسار فيديو في الملف: {video_path}")

    video_stream = streams[0]
    container = metadata.get("format", {})
    video_bitrate = video_stream.get("bit_rate")
    container_bitrate = container.get("bit_rate")
    duration = container.get("duration")

    print("معلومات الملف النهائي (FFprobe):", flush=True)
    print(
        "الدقة: "
        f"{video_stream.get('width', 'غير متاح')}x"
        f"{video_stream.get('height', 'غير متاح')}",
        flush=True,
    )
    print(f"الكوديك: {video_stream.get('codec_name', 'غير متاح')}", flush=True)
    print(f"معدل الإطارات: {video_stream.get('avg_frame_rate', 'غير متاح')}", flush=True)
    print(
        "Bitrate الفيديو: "
        f"{video_bitrate if video_bitrate is not None else 'غير متاح'} bit/s",
        flush=True,
    )
    print(
        "Bitrate الحاوية: "
        f"{container_bitrate if container_bitrate is not None else 'غير متاح'} bit/s",
        flush=True,
    )
    print(f"المدة: {duration if duration is not None else 'غير متاح'} ثانية", flush=True)


def download_video(url, project_root, output_directory=None):
    try:
        from yt_dlp import YoutubeDL
    except ImportError as error:
        raise RuntimeError("مكتبة yt-dlp غير مثبتة. ثبّتها من requirements.txt.") from error

    output_directory = (
        Path(output_directory)
        if output_directory is not None
        else Path(project_root) / "downloads"
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    options = {
        "format": "bestvideo*+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": str(output_directory / "%(title).160s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "restrictfilenames": True,
        "no_warnings": False,
    }
    print("بدء التنزيل — استخدم هذه الأداة فقط للمحتوى الذي تملك حق تنزيله.", flush=True)
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
        downloaded_path = Path(downloader.prepare_filename(info))

    if not downloaded_path.is_file():
        merged_path = downloaded_path.with_suffix(".mp4")
        if merged_path.is_file():
            downloaded_path = merged_path
    print(f"اكتمل التنزيل إلى: {downloaded_path}", flush=True)
    return downloaded_path


def main():
    if len(sys.argv) != 2:
        print("طريقة الاستخدام: run_upscale.py <config.json>", file=sys.stderr)
        return 2

    project_root = get_runtime_directory()
    os.chdir(project_root)
    output_directory = get_output_directory(project_root)
    engine_mode = os.environ.get("MIZMAR_ENGINE_MODE")
    if engine_mode in {"cpu", "cuda"}:
        print(f"محرك المعالجة: {engine_mode.upper()}", flush=True)

    settings = read_settings(sys.argv[1])
    if settings["job_type"] == "download":
        download_video(settings["url"], project_root, output_directory.parent / "downloads")
        return 0
    if settings["job_type"] == "probe":
        video_info = probe_video(settings["input_path"])
        print(f"VIDEO_INFO_JSON:{json.dumps(video_info, ensure_ascii=False)}", flush=True)
        return 0

    input_path = settings["input_path"]
    tool_type = settings["tool_type"]
    source_id = hashlib.sha1(str(input_path).encode("utf-8")).hexdigest()[:8]
    if tool_type == "upscale":
        resolution = settings["resolution"]
        output_path = output_directory / (
            f"{input_path.stem}_mizmar_upscale_{resolution}_{source_id}.mp4"
        )
        result_path = upscale_video(
            input_path,
            output_path,
            settings["codec"],
            settings["fps"],
            resolution,
            ai_model=settings["ai_model"],
            denoise=settings["denoise"],
        )
    elif tool_type == "motion_blur":
        output_path = output_directory / (
            f"{input_path.stem}_mizmar_motion_blur_{source_id}.mp4"
        )
        result_path = motion_blur_video(
            input_path,
            output_path,
            settings["blur_amount"],
            settings["blur_style"],
            settings["interpolation_engine"],
        )
    elif tool_type == "lossless":
        result_path = apply_lossless_patch(
            input_path, settings["multiplier"], output_directory
        )
    elif tool_type == "high_quality_master":
        codec = settings["master_codec"]
        codec_name = "h264" if codec == "libx264" else "h265"
        output_path = output_directory / (
            f"{input_path.stem}_mizmar_master_{codec_name}_{source_id}.mp4"
        )
        result_path = export_high_quality_master(
            input_path,
            output_path,
            codec,
        )
    else:
        target_format = settings["target_format"]
        output_path = output_directory / (
            f"{input_path.stem}_mizmar_convert_{source_id}.{target_format}"
        )
        result_path = convert_video(
            input_path,
            output_path,
            target_format,
            settings["quality"],
        )
    print_video_info(result_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
    