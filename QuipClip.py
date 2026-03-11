import argparse
import json
import re
import subprocess

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple


class Loudness(NamedTuple):
    I: float
    LRA: float
    TP: float
    Thresh: float


def get_loudness(fn: Path) -> Loudness:

    ex = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "info", "-nostats",
        "-i", str(fn.resolve()),
        "-vn", "-af", "loudnorm=print_format=json", "-f", "null", "-",
    ], capture_output=True)

    stderr = ex.stderr.decode("utf-8", errors="ignore")

    r = re.search(r"\[Parsed_loudnorm.*?\}", stderr, re.DOTALL)
    r = re.search(r"{.*}", r.group(0), re.DOTALL) # type: ignore

    if not r:
        print("ffmpeg gives unexpected output as:")
        for line in stderr.splitlines(): print(f" > {line}")
        exit(1)

    else:
        measure = json.loads(r.group(0))
        # print(measure)

        loudness = Loudness(
            I = measure["input_i"],
            LRA = measure["input_lra"],
            TP = measure["input_tp"],
            Thresh = measure["input_thresh"],
        )

        return loudness


def parse_time(t: str) -> float:
    if not t: return -1

    ts = t.split(":")
    tc = 0

    for t in ts:
        tc = tc * 60 + float(t)

    return tc


def encode_clip(args: argparse.Namespace):

    # 0. sanitize
    input = Path(args.fn)
    output = Path(args.output)

    start = parse_time(args.start)
    end = parse_time(args.end)

    if args.video:
        if output.suffix.lower() not in [".mp4", ".mkv"]:
            if args.lossless:
                output = output.parent.joinpath(f"{output.stem}.mkv")
            else:
                output = output.parent.joinpath(f"{output.stem}.mp4")
        if args.LUFS < -12.0:
            print(f"LUFS is {args.LUFS}, adjusting to -12 for video.")
            args.LUFS = -12.0
    else:
        if args.lossless:
            if output.suffix.lower() != ".flac":
                output = output.parent.joinpath(f"{output.stem}.flac")
        else:
            if output.suffix.lower() != ".m4a":
                output = output.parent.joinpath(f"{output.stem}.m4a")


    with TemporaryDirectory(prefix="QuipClip_") as temp_dir:
        # 1. clip -> flac
        temp_path = Path(temp_dir)
        temp_ext = ".mkv" if args.video else ".flac"
        temp_output = temp_path.joinpath(f"{output.stem}{temp_ext}")

        print(f"{str(input)} -> {str(temp_output)} (temporary)")

        cmd = [
            "ffmpeg", "-hide_banner",
            "-i", str(input.resolve()),
            "-ss", f"{start:.3f}",
            *(["-to", f"{end:.3f}"] if end > 0 else []),
            "-map_metadata", "-1", # no metadata
        ]

        if args.video:
            cmd.extend([
                "-c:v", "libx264", "-preset", "veryslow", "-crf", "23",
                "-c:a", "flac"
            ])
        else:
            cmd.extend(["-vn"])

        cmd.extend(["-y", str(temp_output.resolve())])

        subprocess.run(
            cmd, check=True,
            capture_output=True, text=True,
            encoding="utf-8", errors="ignore"
        )

        loudness = get_loudness(temp_output)
        measured = f"measured_I={loudness.I}:measured_LRA={loudness.LRA}:measured_TP={loudness.TP}:measured_thresh={loudness.Thresh}"

        target_LRA = min(max(float(loudness.LRA), 1.0), 50.0) if args.video else args.LRA
        target = f"loudnorm=I={args.LUFS}:LRA={target_LRA}:TP={args.TP}"

        print("measure temp output:", loudness)

        cmd = [
            "ffmpeg", "-hide_banner",
            "-i", str(temp_output.resolve()),
            "-map_metadata", "-1", # no metadata
        ]

        if args.video:
            cmd.extend(["-c:v", "copy"])
        else:
            cmd.extend(["-vn"])

        if args.lossless:
            cmd.extend([
                "-c:a", "flac",
                "-af", f"{target}:{measured},aresample=resampler=soxr:osr=48000:precision=33:dither_method=triangular",
                "-y", str(output.resolve()),
            ])
        else:
            audio_bitrate = "320k" if args.video else "192k"
            cmd.extend([
                "-c:a", "aac", "-ab", audio_bitrate,
                "-af", f"{target}:{measured},aresample=resampler=soxr:osr=48000:precision=33:dither_method=triangular",
                "-y", str(output.resolve()),
            ])

        print(f"{str(temp_output)} -> {str(output)} (loudnorm)")

        subprocess.run(
            cmd, check=True,
            capture_output=True, text=True,
            encoding="utf-8", errors="ignore"
        )

        final_loudness = get_loudness(output)
        print("measure final output:", final_loudness)

    print("FINISHED")


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Clip audio from input video and loudnorm it!")

    parser.add_argument("fn", type=str)
    parser.add_argument("output_fn", type=str, nargs="?")
    parser.add_argument("--start", "-ss", type=str, default="0")
    parser.add_argument("--end", "-to", type=str, default="")
    parser.add_argument("--output", "-o", type=str, required=False)
    parser.add_argument("-i", "--LUFS", type=float, help="loudness target", default=-18.0)
    parser.add_argument("-l", "--LRA", type=float, help="loudness range", default=7.0)
    parser.add_argument("-t", "--TP", type=float, help="true peak loudness", default=-1.0)
    parser.add_argument("--video", action="store_true", help="encode video as well")
    parser.add_argument("--lossless", action="store_true", help="encode audio as flac")

    args = parser.parse_args()
    args.output = args.output if args.output else args.output_fn

    encode_clip(args)