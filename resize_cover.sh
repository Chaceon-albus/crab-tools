#!/bin/bash

# 检查输入参数
if [ "$#" -ne 2 ]; then
    echo "用法: $0 <源目录> <目标目录>"
    exit 1
fi

SOURCE_DIR="$1"
TARGET_DIR="$2"

if [ ! -d "$SOURCE_DIR" ]; then
    echo "错误: 源目录不存在。"
    exit 1
fi

mkdir -p "$TARGET_DIR"

echo "开始处理..."

find "$SOURCE_DIR" -maxdepth 1 -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" -o -iname "*.webp" -o -iname "*.bmp" \) -print0 | while read -d $'\0' file; do

    full_filename=$(basename "$file")
    filename_no_ext="${full_filename%.*}"
    output_file="$TARGET_DIR/$filename_no_ext.webp"

    echo "正在处理: $full_filename"

    ffmpeg -hide_banner -loglevel error -nostdin -i "$file" \
        -vf "scale=256:-2:flags=lanczos" \
        -c:v libwebp -quality 100 \
        -y "$output_file"

done

echo "---"
echo "所有任务已完成！"