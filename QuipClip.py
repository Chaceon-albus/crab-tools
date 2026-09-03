import argparse
import json
import re
import shlex
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


def parse_segments(starts: list[str] | None, ends: list[str] | None) -> list[tuple[float, float]]:
    starts = list(starts) if starts else ["0"]
    ends = list(ends) if ends is not None else []

    if len(starts) < len(ends):
        raise ValueError(f"Too many --end/-to arguments ({len(ends)}) for --start/-ss arguments ({len(starts)}).")
    if len(starts) > len(ends) + 1:
        raise ValueError(f"Too many --start/-ss arguments ({len(starts)}) for --end/-to arguments ({len(ends)}).")

    if len(starts) == len(ends) + 1:
        ends.append("")

    segments: list[tuple[float, float]] = []
    for s_str, e_str in zip(starts, ends):
        s = parse_time(s_str)
        e = parse_time(e_str)
        if e > 0 and s >= e:
            raise ValueError(f"Start time {s} s ({s_str}) must be earlier than end time {e} s ({e_str}).")
        segments.append((s, e))

    return segments


def resolve_output_path(input: Path, output_arg: str | None, video: bool, lossless: bool, acopy: bool) -> Path:
    output = Path(output_arg) if output_arg else input.parent.joinpath(input.stem)
    if acopy:
        if not output.suffix:
            if video:
                ext = ".mkv" if lossless else ".mp4"
                output = output.parent.joinpath(f"{output.stem}{ext}")
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
                    if input.suffix.lower() not in [".mp4", ".mkv", ".avi", ".mov", ".flv", ".m2ts", ".ts", ".webm"]:
                        ext = input.suffix.lower()
                    else:
                        ext = ".m4a"
                output = output.parent.joinpath(f"{output.stem}{ext}")
        else:
            if video and output.suffix.lower() not in [".mp4", ".mkv"]:
                ext = ".mkv" if lossless else ".mp4"
                output = output.parent.joinpath(f"{output.stem}{ext}")
    elif video:
        if output.suffix.lower() not in [".mp4", ".mkv"]:
            ext = ".mkv" if lossless else ".mp4"
            output = output.parent.joinpath(f"{output.stem}{ext}")
    else:
        if lossless:
            if output.suffix.lower() != ".flac":
                output = output.parent.joinpath(f"{output.stem}.flac")
        else:
            if output.suffix.lower() != ".m4a":
                output = output.parent.joinpath(f"{output.stem}.m4a")

    if output.resolve() == input.resolve():
        output = output.parent.joinpath(f"{output.stem}_clip{output.suffix}")

    return output


def run_ffmpeg(cmd: list[str]) -> bool:
    try:
        subprocess.run(
            cmd, check=True,
            capture_output=True, text=True,
            encoding="utf-8", errors="ignore"
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error: FFmpeg exited with code {e.returncode}")
        if e.stderr:
            for line in e.stderr.strip().splitlines():
                print(f" > {line}")
        return False


def build_clip_cmd(
    input: Path,
    output: Path,
    segments: list[tuple[float, float]],
    video: bool = False,
    video_color: VideoColor | None = None,
    lossless: bool = False,
    acopy: bool = False,
    audio_codec: str | None = None,
    audio_bitrate: str = "",
) -> list[str]:
    cmd = ["ffmpeg", "-hide_banner"]

    for s, e in segments:
        cmd.extend(["-ss", f"{s:.3f}"])
        if e > 0:
            cmd.extend(["-to", f"{e:.3f}"])
        cmd.extend(["-i", str(input.resolve()), "-ss", "0"])

    if len(segments) > 1:
        if acopy:
            raise ValueError(
                "Cannot use --acopy when concatenating multiple segments because FFmpeg concat filter requires decoding and re-encoding."
            )
        n = len(segments)
        if video:
            filter_inputs = "".join(f"[{i}:v][{i}:a]" for i in range(n))
            filter_complex = f"{filter_inputs}concat=n={n}:v=1:a=1[outv][outa]"
            cmd.extend(["-filter_complex", filter_complex, "-map", "[outv]", "-map", "[outa]"])
        else:
            filter_inputs = "".join(f"[{i}:a]" for i in range(n))
            filter_complex = f"{filter_inputs}concat=n={n}:v=0:a=1[outa]"
            cmd.extend(["-filter_complex", filter_complex, "-map", "[outa]"])

    cmd.extend(["-map_metadata", "-1"])

    if video:
        cmd.extend(["-c:v", "libx264", "-preset", "veryslow", "-crf", "23"])
        if video_color:
            cmd.extend(video_color_args(video_color))
            cmd.extend(x264_color_args(video_color))
    else:
        cmd.extend(["-vn"])

    if acopy:
        cmd.extend(["-c:a", "copy"])
    elif audio_codec:
        cmd.extend(["-c:a", audio_codec])
        if audio_bitrate:
            cmd.extend(["-ab", audio_bitrate])
    elif lossless:
        cmd.extend(["-c:a", "flac"])
    else:
        cmd.extend(["-c:a", "aac"])
        if audio_bitrate:
            cmd.extend(["-ab", audio_bitrate])

    cmd.extend(["-y", str(output.resolve())])
    return cmd


def encode_clip(args: argparse.Namespace):

    # 0. sanitize & parse
    input = Path(args.fn)
    if not input.exists():
        print(f"Error: input file {input} does not exist.")
        return

    try:
        segments = parse_segments(args.start, args.end)
    except ValueError as e:
        print(f"Error: {e}")
        return

    if args.acopy and len(segments) > 1:
        print("Error: --acopy is not supported when concatenating multiple segments because FFmpeg concat filter requires decoding and re-encoding. Please omit --acopy (use --lossless if you want lossless audio).")
        return

    output = resolve_output_path(
        input=input,
        output_arg=args.output,
        video=args.video,
        lossless=args.lossless,
        acopy=args.acopy,
    )

    video_color = get_video_color(input) if args.video else VideoColor(None, None, None, None)

    # 1. acopy mode (only single segment)
    if args.acopy:
        cmd = build_clip_cmd(
            input=input,
            output=output,
            segments=segments,
            video=args.video,
            video_color=video_color,
            lossless=args.lossless,
            acopy=True,
        )
        desc = "encode video, copy audio" if args.video else "copy audio"
        print(f"{str(input)} -> {str(output)} ({desc})")
        if args.print_cmd or args.dry_run:
            print("[CMD]", shlex.join(cmd))
        if args.dry_run:
            print("FINISHED (dry run)")
            return

        if not run_ffmpeg(cmd):
            return

        final_loudness = get_loudness(output)
        print("measure final output:", final_loudness)
        print("FINISHED")
        return

    # 2. bypass gain adjust mode (single pass direct output)
    if args.bypass_gain_adjust:
        audio_codec = "flac" if args.lossless else "aac"
        audio_bitrate = "" if args.lossless else (args.bitrate or ("320k" if args.video else "256k"))
        cmd = build_clip_cmd(
            input=input,
            output=output,
            segments=segments,
            video=args.video,
            video_color=video_color,
            lossless=args.lossless,
            acopy=False,
            audio_codec=audio_codec,
            audio_bitrate=audio_bitrate,
        )
        desc = f"bypass gain adjust, {'video + ' if args.video else ''}{audio_codec}"
        print(f"{str(input)} -> {str(output)} ({desc})")
        if args.print_cmd or args.dry_run:
            print("[CMD]", shlex.join(cmd))
        if args.dry_run:
            print("FINISHED (dry run)")
            return

        if not run_ffmpeg(cmd):
            return

        final_loudness = get_loudness(output)
        print("measure final output:", final_loudness)
        print("FINISHED")
        return

    # 3. Two-pass loudness normalization mode
    with TemporaryDirectory(prefix="QuipClip_") as temp_dir:
        temp_path = Path(temp_dir)
        temp_ext = ".mkv" if args.video else ".flac"
        temp_output = temp_path.joinpath(f"{output.stem}{temp_ext}")

        print(f"{str(input)} -> {str(temp_output)} (temporary)")

        pass1_cmd = build_clip_cmd(
            input=input,
            output=temp_output,
            segments=segments,
            video=args.video,
            video_color=video_color,
            lossless=True,
            acopy=False,
            audio_codec="flac",
        )

        if args.print_cmd or args.dry_run:
            print("[CMD Pass 1 (Cut & Concat)]", shlex.join(pass1_cmd))

        if args.dry_run:
            audio_bitrate = args.bitrate or ("320k" if args.video else "256k")
            pass2_preview = [
                "ffmpeg", "-hide_banner",
                "-i", str(temp_output.resolve()),
                "-map_metadata", "-1",
                *(["-c:v", "copy"] if args.video else ["-vn"]),
                *(["-c:a", "flac"] if args.lossless else ["-c:a", "aac", "-ab", audio_bitrate]),
                "-af", "<loudnorm/volume filter evaluated from Pass 1 output>,aresample=resampler=soxr:osr=48000:precision=33:dither_method=triangular",
                "-y", str(output.resolve()),
            ]
            print("[CMD Pass 2 (Loudness Adjustment Preview)]", shlex.join(pass2_preview))
            print("FINISHED (dry run)")
            return

        if not run_ffmpeg(pass1_cmd):
            return

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
            audio_bitrate = args.bitrate or ("320k" if args.video else "256k")
            cmd.extend([
                "-c:a", "aac", "-ab", audio_bitrate,
                "-af", f"{audio_filter},aresample=resampler=soxr:osr=48000:precision=33:dither_method=triangular",
                "-y", str(output.resolve()),
            ])

        print(f"{str(temp_output)} -> {str(output)} ({'loudnorm' if args.loudnorm else 'volume'})")
        if args.print_cmd:
            print("[CMD Pass 2 (Loudness Adjustment)]", shlex.join(cmd))

        if not run_ffmpeg(cmd):
            return

        final_loudness = get_loudness(output)
        print("measure final output:", final_loudness)

    print("FINISHED")


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Clip audio from input video and loudnorm it!")

    parser.add_argument("fn", type=str, help="input video or audio file")
    parser.add_argument("output_fn", type=str, nargs="?", help="output file path (optional)")
    parser.add_argument("--start", "-ss", action="extend", nargs="+", help="start time(s), e.g. 00:01:00 or 10.5 (can be repeated)")
    parser.add_argument("--end", "-to", action="extend", nargs="+", help="end time(s), e.g. 00:02:00 or 25.0 (can be repeated)")
    parser.add_argument("--output", "-o", type=str, required=False, help="output file path")
    parser.add_argument("-I", "--LUFS", type=float, help="loudness target", default=-18.0)
    parser.add_argument("-l", "--LRA", type=float, help="loudness range", default=7.0)
    parser.add_argument("-t", "--TP", type=float, help="true peak loudness", default=-1.0)
    parser.add_argument("--video", action="store_true", help="encode video as well")
    parser.add_argument("--lossless", action="store_true", help="encode audio as flac")
    parser.add_argument("--loudnorm", action="store_true", help="use loudnorm filter instead of simple volume gain")
    parser.add_argument("--linear", action="store_true", help="use linear loudnorm")
    parser.add_argument("--acopy", action="store_true", help="only copy audio stream without re-encoding (single segment only)")
    parser.add_argument("--bitrate", type=str, default="", help="use audio bitrate if specified")
    parser.add_argument("--bypass-gain-adjust", action="store_true", help="bypass audio loudness/gain adjustment")
    parser.add_argument("--print-cmd", action="store_true", help="print generated ffmpeg command before execution")
    parser.add_argument("--dry-run", action="store_true", help="print generated ffmpeg command and exit without running")

    args = parser.parse_args()
    args.output = args.output if args.output else args.output_fn

    encode_clip(args)
