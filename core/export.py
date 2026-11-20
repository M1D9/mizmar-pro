import ffmpeg
import subprocess

TIKTOK_PRESETS = {
    "tiktok_1080p_60":{
        "width": 1080,"height": 1920,
        "fps": 60 , "video_bitrate" : "12M",
        "audio_bitrate" : "192k",
        "codec" : "libx264", "preset": "slow", "crf": "17",
    },
    "tiktok_4k_60":{
        "width" : 2160, "height" : 3840,
        "fps" : 60, "video_bitrate" : "45M",
        "audio_bitrate" : "192k",
        "codec" : "libx264", "preset": "slow", "crf": "16",
    },
}
def export_video(input_path, output_path, preset_name="tiktok_1080p_60"):
    p = TIKTOK_PRESETS[preset_name]
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", f"scale={p['width']}:{p['height']}:force_original_aspect_ratio=decrease,"
               f"pad={p['width']}:{p['height']}:(ow-iw)/2:(oh-ih)/2",
        "-r", str(p["fps"]),
        "-c:v", p["codec"], "-preset", p["preset"], "-crf", p["crf"],
        "-maxrate", p["video_bitrate"], "-bufsize", "2M",
        "-c:a", "aac", "-b:a", p["audio_bitrate"],
        "-movflags", "+faststart",
        output_path,
    ]
    subprocess.run(cmd, check=True)