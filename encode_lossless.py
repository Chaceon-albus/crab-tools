import argparse
import json
import subprocess

from pathlib import Path


def get_sample_rate(fn: Path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate",
        "-of", "json",
        str(fn)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    data = json.loads(result.stdout)
    return int(data["streams"][0]["sample_rate"])


def encode_alac(input: Path, output: Path):

    sample_rate = get_sample_rate(input.resolve())

    resample_params = []
    if sample_rate > 48000:
        sample_rate = 48000
        resample_params = ["-af", f"aresample=resampler=soxr:osr={sample_rate}:precision=28"]

    cmd = [
        "ffmpeg", "-hide_banner",
        "-i", str(input.resolve()),
        "-map_metadata", "0",
        "-map", "0:a",
        "-map", "0:v?",
    ] + resample_params + [
        "-ar", f"{sample_rate}",
        "-c:v", "copy",
        "-disposition:v", "attached_pic",
        "-c:a", "flac",
        "-compression_level", "12",
        "-y", str(output.resolve()),
    ]

    try:
        print(f"{input} -> {output}...", end=" ")
        subprocess.run(
            cmd, check=True,
            capture_output=True, text=True, encoding="utf-8", errors="ignore"
        )
    except Exception as e:
        print(f"转换失败：\n{e}")
        # print(" ".join(cmd))
    else:
        print("转换成功！")


if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument("--input", "-i", type=str, required=True)
    parser.add_argument("--output", "-o", type=str, required=True)

    args = parser.parse_args()

    odir = Path(args.output)
    if not odir.exists(): odir.mkdir(parents=True)

    for fn in Path(args.input).iterdir():
        if fn.is_file() and fn.suffix.lower() == ".flac":
            encode_alac(
                fn,
                odir.joinpath(f"{fn.stem}.flac"),
            )