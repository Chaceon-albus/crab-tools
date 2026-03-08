import argparse
import json
import subprocess

from pathlib import Path


def get_audio_info(fn: Path) -> tuple[int, int]:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels",
        "-of", "json",
        str(fn)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    return int(stream.get("sample_rate", 0)), int(stream.get("channels", 2))

def encode_flac(input: Path, output: Path) -> None:

    sample_rate, channels = get_audio_info(input.resolve())

    audio_filters = []

    if channels == 6:
        # 5.1 -> 2.0 Downmix (target: foobar QQM hearing)
        audio_filters.append("pan=stereo|FL < 1.0*FL + 0.4*FC + 0.5*SL + 0.3*LFE|FR < 1.0*FR + 0.4*FC + 0.5*SR + 0.3*LFE")

    if sample_rate > 48000:
        # resample to 48 kHz
        audio_filters.append("aresample=resampler=soxr:osr=48000:precision=33:dither_method=triangular")

    af_params = []
    if audio_filters:
        af_params = ["-af", ",".join(audio_filters)]

    cmd = [
        "ffmpeg", "-hide_banner",
        "-i", str(input.resolve()),
        "-map_metadata", "0",
        "-map", "0:a",
        "-map", "0:v?",
    ] + af_params + [
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
    except subprocess.CalledProcessError as e:
        print(f"编码失败：\n{e}")
        if e.stderr:
            print(f"详细错误信息：\n{e.stderr.strip()}")
    except Exception as e:
        print(f"编码失败：\n{e}")
    else:
        print("编码成功！")


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Re-encode lossless music in <= 48 kHz sample rate.")

    parser.add_argument("--input", "-i", type=str, required=True, help="input file or dir")
    parser.add_argument("--output", "-o", type=str, required=True, help="output file or dir")

    args = parser.parse_args()

    ipath = Path(args.input)
    opath = Path(args.output)

    if ipath.is_file():
        opath.parent.mkdir(parents=True, exist_ok=True)
        encode_flac(ipath, opath)
    elif ipath.is_dir():
        opath.mkdir(parents=True, exist_ok=True)
        for fn in ipath.iterdir():
            if fn.is_file() and fn.suffix.lower() == ".flac":
                encode_flac(
                    fn,
                    opath.joinpath(f"{fn.stem}.flac"),
                )