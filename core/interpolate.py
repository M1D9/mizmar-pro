import subprocess

def interpolate_video(input_path, output_path, target_fps=60):
    subprocess.run([
        "ffmpeg", "-y", "-i", input_path,
        "-vf", f"minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1",
        "-c:v", "libx264", "-crf", "17", "-preset", "slow",
        "-c:a", "copy", output_path,
    ], check=True)
