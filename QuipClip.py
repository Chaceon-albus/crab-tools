import shutil
import argparse
import json
import re
import shutil
import subprocess

from pathlib import Path
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
    r = re.search(r"{.*}", r.group(0), re.DOTALL)

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

    if output.suffix.lower() != ".m4a":
        output = output.parent.joinpath(f"{output.stem}.m4a")

    # 1. clip -> flac
    temp_output = output.parent.joinpath(f"{output.stem}.flac")

    print(f"{str(input)} -> {str(temp_output)} (temporary)")

    cmd = [
        "ffmpeg", "-hide_banner",
        "-i", str(input.resolve()),
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-map_metadata", "-1", # no metadata
        "-vn",
        "-y", str(temp_output.resolve()),
    ]

    subprocess.run(
        cmd, check=True,
        capture_output=True, text=True,
        encoding="utf-8", errors="ignore"
    )

    loudness = get_loudness(temp_output)
    measured = f"measured_I={loudness.I}:measured_LRA={loudness.LRA}:measured_TP={loudness.TP}:measured_thresh={loudness.Thresh}"
    target = f"loudnorm=I={args.LUFS}:LRA={args.LRA}:TP={args.TP}"

    print("measure temp output:", loudness)

    cmd = [
        "ffmpeg", "-hide_banner",
        "-i", str(temp_output.resolve()),
        "-map_metadata", "-1", # no metadata
        "-vn", "-c:a", "aac", "-ab", "192k",
        "-af", f"{target}:{measured}",
        "-y", str(output.resolve()),
    ]

    print(f"{str(temp_output)} -> {str(output)} (loudnorm)")

    subprocess.run(
        cmd, check=True,
        capture_output=True, text=True,
        encoding="utf-8", errors="ignore"
    )

    print(f"delete {temp_output}")
    temp_output.unlink(missing_ok=True)

    print("FINISHED")


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Clip audio from input video and loudnorm it!")

    parser.add_argument("fn", type=str)
    parser.add_argument("--start", "-s", type=str, required=True)
    parser.add_argument("--end", "-e", type=str, required=True)
    parser.add_argument("--output", "-o", type=str, required=True)
    parser.add_argument("-i", "--LUFS", type=float, help="loudness target", default=-18.0)
    parser.add_argument("-l", "--LRA", type=float, help="loudness range", default=7.0)
    parser.add_argument("-t", "--TP", type=float, help="true peak loudness", default=-1.0)

    args = parser.parse_args()

    encode_clip(args)