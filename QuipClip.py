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


class VideoColor(NamedTuple):
    range: str | None
    space: str | None
    transfer: str | None
    primaries: str | None


X264_COLOR_PRIMARIES = {
    "bt709", "bt470m", "bt470bg", "smpte170m", "smpte240m",
    "film", "bt2020", "smpte428", "smpte431", "smpte432", "jedec-p22",
}
X264_COLOR_TRANSFERS = {
    "bt709", "bt470m", "bt470bg", "smpte170m", "smpte240m",
    "linear", "log100", "log316", "iec61966-2-4", "bt1361e",
    "iec61966-2-1", "bt2020-10", "bt2020-12", "smpte2084",
    "smpte428", "arib-std-b67",
}
X264_COLOR_MATRICES = {
    "gbr", "bt709", "fcc", "bt470bg", "smpte170m", "smpte240m",
    "ycgco", "bt2020nc", "bt2020c", "smpte2085",
    "chroma-derived-nc", "chroma-derived-c", "ictcp",
}


def known_color(value: str | None) -> str | None:
    if value in [None, "", "unknown", "unspecified"]:
        return None
    return value


def get_video_color(fn: Path) -> VideoColor:
    ex = subprocess.run([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=color_range,color_space,color_transfer,color_primaries",
        "-of", "json",
        str(fn.resolve()),
    ], capture_output=True, text=True, encoding="utf-8", errors="ignore")

    if ex.returncode != 0:
        return VideoColor(None, None, None, None)

    try:
        streams = json.loads(ex.stdout).get("streams", [])
    except json.JSONDecodeError:
        return VideoColor(None, None, None, None)

    if not streams:
        return VideoColor(None, None, None, None)

    stream = streams[0]
    return VideoColor(
        known_color(stream.get("color_range")),
        known_color(stream.get("color_space")),
        known_color(stream.get("color_transfer")),
        known_color(stream.get("color_primaries")),
    )


def video_color_args(color: VideoColor) -> list[str]:
    args = []

    if color.range:
        args.extend(["-color_range", color.range])
    if color.space:
        args.extend(["-colorspace", color.space])
    if color.transfer:
        args.extend(["-color_trc", color.transfer])
    if color.primaries:
        args.extend(["-color_primaries", color.primaries])

    return args


def x264_color_args(color: VideoColor) -> list[str]:
    params = []

    if color.range == "pc":
        params.append("fullrange=on")
    elif color.range == "tv":
        params.append("fullrange=off")

    if color.primaries in X264_COLOR_PRIMARIES:
        params.append(f"colorprim={color.primaries}")
    if color.transfer in X264_COLOR_TRANSFERS:
        params.append(f"transfer={color.transfer}")
    if color.space in X264_COLOR_MATRICES:
        params.append(f"colormatrix={color.space}")

    return ["-x264-params", ":".join(params)] if params else []


def get_audio_codec(fn: Path) -> str | None:
    ex = subprocess.run([
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name",
        "-of", "json",
        str(fn.resolve()),
    ], capture_output=True, text=True, encoding="utf-8", errors="ignore")

    if ex.returncode != 0:
        return None

    try:
        streams = json.loads(ex.stdout).get("streams", [])
    except json.JSONDecodeError:
        return None

    if not streams:
        return None

    return streams[0].get("codec_name")


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

    if start > end:
        print(f"Skip, start time {start} s is later than end time {end} s.")
        return

    if args.acopy:
        if not output.suffix:
            if args.video:
                if args.lossless:
                    output = output.parent.joinpath(f"{output.stem}.mkv")
                else:
                    output = output.parent.joinpath(f"{output.stem}.mp4")
            else:
                codec = get_audio_codec(input)
                suffix_map = {
                    "flac": ".flac",
                    "mp3": ".mp3",
                    "opus": ".opus",
                    "vorbis": ".ogg",
                    "aac": ".m4a",
                    "alac": ".m4a",
                }
                ext = suffix_map.get(codec) if codec else None
                if not ext:
                    if input.suffix.lower() not in [".mp4", ".mkv", ".avi", ".mov", ".flv", ".webmts", ".ts", ".webm"]:
                        ext = input.suffix.lower()
                    else:
                        ext = ".m4a"
                output = output.parent.joinpath(f"{output.stem}{ext}")
        else:
            if args.video and output.suffix.lower() not in [".mp4", ".mkv"]:
                if args.lossless:
                    output = output.parent.joinpath(f"{output.stem}.mkv")
                else:
                    output = output.parent.joinpath(f"{output.stem}.mp4")

        cmd = [
            "ffmpeg", "-hide_banner",
            "-ss", f"{start:.3f}",
            *(["-to", f"{end:.3f}"] if end > 0 else []),
            "-i", str(input.resolve()),
            "-ss", "0",
            "-map_metadata", "-1", # no metadata
        ]

        if args.video:
            video_color = get_video_color(input)
            cmd.extend([
                "-c:v", "libx264", "-preset", "veryslow", "-crf", "23",
                *video_color_args(video_color),
                *x264_color_args(video_color),
                "-c:a", "copy",
            ])
            print(f"{str(input)} -> {str(output)} (encode video, copy audio)")
        else:
            cmd.extend(["-vn", "-c:a", "copy"])
            print(f"{str(input)} -> {str(output)} (copy audio)")

        cmd.extend(["-y", str(output.resolve())])

        subprocess.run(
            cmd, check=True,
            capture_output=True, text=True,
            encoding="utf-8", errors="ignore"
        )

        final_loudness = get_loudness(output)
        print("measure final output:", final_loudness)
        print("FINISHED")
        return

    video_color = get_video_color(input) if args.video else VideoColor(None, None, None, None)

    if args.video:
        if output.suffix.lower() not in [".mp4", ".mkv"]:
            if args.lossless:
                output = output.parent.joinpath(f"{output.stem}.mkv")
            else:
                output = output.parent.joinpath(f"{output.stem}.mp4")
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
            "-ss", f"{start:.3f}",
            *(["-to", f"{end:.3f}"] if end > 0 else []),
            "-i", str(input.resolve()),
            "-ss", "0", # avoid wrong duration
            "-map_metadata", "-1", # no metadata
        ]

        if args.video:
            cmd.extend([
                "-c:v", "libx264", "-preset", "veryslow", "-crf", "23",
                *video_color_args(video_color),
                *x264_color_args(video_color),
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

        target_LUFS = args.LUFS
        if args.video and float(loudness.TP) > -99.0:
            target_LUFS = float(loudness.I) - (float(loudness.TP) - args.TP)

        if not args.loudnorm:
            measured_I = float(loudness.I)
            measured_TP = float(loudness.TP)
            gain = args.LUFS - measured_I
            if measured_TP > -99.0:
                gain = min(gain, args.TP - measured_TP)
            audio_filter = f"volume={gain:.2f}dB"
            print("measure temp output:", loudness)
            print(f"gain adjustment: {gain:.2f} dB")
        else:
            extra_opt = ":linear=true" if args.linear else ""
            target = f"loudnorm=I={target_LUFS}:LRA={target_LRA}:TP={args.TP}{extra_opt}"
            audio_filter = f"{target}:{measured}"
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
                "-af", f"{audio_filter},aresample=resampler=soxr:osr=48000:precision=33:dither_method=triangular",
                "-y", str(output.resolve()),
            ])
        else:
            audio_bitrate = "320k" if args.video else "192k"
            cmd.extend([
                "-c:a", "aac", "-ab", audio_bitrate,
                "-af", f"{audio_filter},aresample=resampler=soxr:osr=48000:precision=33:dither_method=triangular",
                "-y", str(output.resolve()),
            ])

        print(f"{str(temp_output)} -> {str(output)} ({'loudnorm' if args.loudnorm else 'volume'})")

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
    parser.add_argument("-I", "--LUFS", type=float, help="loudness target", default=-18.0)
    parser.add_argument("-l", "--LRA", type=float, help="loudness range", default=7.0)
    parser.add_argument("-t", "--TP", type=float, help="true peak loudness", default=-1.0)
    parser.add_argument("--video", action="store_true", help="encode video as well")
    parser.add_argument("--lossless", action="store_true", help="encode audio as flac")
    parser.add_argument("--loudnorm", action="store_true", help="use loudnorm filter instead of simple volume gain")
    parser.add_argument("--linear", action="store_true", help="use linear loudnorm")
    parser.add_argument("--acopy", action="store_true", help="only copy audio stream without re-encoding")

    args = parser.parse_args()
    args.output = args.output if args.output else args.output_fn

    encode_clip(args)
