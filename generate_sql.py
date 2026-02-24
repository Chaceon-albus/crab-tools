import argparse

from pathlib import Path
from datetime import datetime, timezone, timedelta


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--music", "-i", type=str, required=True, help="music dir")
    parser.add_argument("--cover", "-o", type=str, required=True, help="cover dir")

    args = parser.parse_args()

    music_dir = args.music
    cover_dir = args.cover

    try:
        from mutagen import File
    except ImportError:
        print("mutagen not installed. Please run 'pip install mutagen'")
        return

    music_files = [f for f in music_dir.iterdir() if f.is_file()]
    cover_files = [f for f in cover_dir.iterdir() if f.is_file()]

    # Create a mapping of stem to cover file
    cover_map = {f.stem: f.name for f in cover_files}

    sql_statements = []

    sql_statements.append("-- Generated SQL for importing music into D1")

    dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    create_date_ts = int(dt.timestamp())
    release_date_ts = create_date_ts

    missing_covers = []

    for m_file in music_files:
        stem = m_file.stem
        title = stem

        location = f"/music/{m_file.name}"

        cover_name = cover_map.get(stem, "")
        if not cover_name:
            missing_covers.append(stem)

        cover_location = f"/cover/{cover_name}" if cover_name else ""

        # get duration
        duration = 0
        try:
            audio = File(m_file)
            if audio is not None and audio.info is not None:
                duration = int(round(audio.info.length))
        except Exception as e:
            print(f"Could not read duration for {m_file.name}: {e}")

        artists_json = "[]"
        original_artists_json = "[]"

        # SQL injection prevention: escape single quotes in title etc
        title_esc = title.replace("'", "''")
        location_esc = location.replace("'", "''")
        cover_location_esc = cover_location.replace("'", "''")

        sql = f"INSERT INTO music (title, album, artists, original_artists, source, caption, location, cover_location, is_hidden, duration, create_date, release_date) VALUES ('{title_esc}', '', '{artists_json}', '{original_artists_json}', 'lossless', '', '{location_esc}', '{cover_location_esc}', 0, {duration}, {create_date_ts}, {release_date_ts});"
        sql_statements.append(sql)

    output_file = Path("music_import.sql")
    output_file.write_text("\n".join(sql_statements), encoding="utf-8")
    print(f"Successfully generated {output_file.absolute()} with {len(music_files)} records.")

    if missing_covers:
        print(f"Warning: Missing covers for {len(missing_covers)} files:")
        for mc in missing_covers[:5]:
            print(f"  - {mc}")
        if len(missing_covers) > 5:
            print("  ...")


if __name__ == "__main__":
    main()
