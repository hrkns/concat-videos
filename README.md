# Concatenate MP4 files from a folder with FFmpeg

This set of scripts automates the classic FFmpeg concat workflow:

```bash
ffmpeg -f concat -safe 0 -i files.txt -c copy output.mp4
```

Instead of manually creating `files.txt`, the scripts:

- receive a **source folder path**
- collect all `.mp4` files in that folder
- sort them in **alphabetical order**
- generate the temporary concat file automatically
- call FFmpeg with `-c copy`

## Included scripts

- `concat.py`
- `concat.sh`
- `concat.ps1`

## Important note

These scripts use:

```bash
-c copy
```

That means the input files should be stream-compatible for concat copy, usually meaning they should have matching codec/container characteristics such as:

- video codec
- audio codec
- resolution
- frame rate
- channel layout
- time base / stream compatibility

If FFmpeg fails, you may need a re-encoding version instead of stream copy.

---

## Default output behavior

All three scripts now behave the same way:

- if you provide **only the source folder**, the output will be created **inside that same source folder** as `output.mp4`
- if you provide **only a filename** like `merged.mp4`, it will also be created **inside the source folder**
- if you provide a **full path** or a **relative path containing directories**, that exact location will be used
- the scripts also **exclude the output file from the input list** if it is inside the source folder and has `.mp4`

---

## Python version

### File

`concat.py`

### Requirements

- Python 3
- FFmpeg available in `PATH`

### Usage

Default output in the same source folder:

```bash
python3 concat.py /path/to/folder
```

Output:

```text
/path/to/folder/output.mp4
```

Custom filename in the same source folder:

```bash
python3 concat.py /path/to/folder -o merged.mp4
```

Output:

```text
/path/to/folder/merged.mp4
```

Explicit path elsewhere:

```bash
python3 concat.py /path/to/folder -o /another/path/merged.mp4
```

### Notes

- files are sorted alphabetically by filename
- matching is case-insensitive for `.mp4`
- the script prints the files it will concatenate before running FFmpeg

---

## Bash version

### File

`concat.sh`

### Requirements

- Bash
- FFmpeg available in `PATH`
- `python3` available in `PATH` for robust path resolution

### Make it executable

```bash
chmod +x concat.sh
```

### Usage

Default output in the same source folder:

```bash
./concat.sh /path/to/folder
```

Output:

```text
/path/to/folder/output.mp4
```

Custom filename in the same source folder:

```bash
./concat.sh /path/to/folder merged.mp4
```

Output:

```text
/path/to/folder/merged.mp4
```

Explicit path elsewhere:

```bash
./concat.sh /path/to/folder /another/path/merged.mp4
```

### Notes

- files are sorted alphabetically by filename
- matching is case-insensitive for `.mp4`
- excludes the output file if it is found in the same folder

---

## PowerShell version

### File

`concat.ps1`

### Requirements

- PowerShell
- FFmpeg available in `PATH`

### Usage

Default output in the same source folder:

```powershell
.\concat.ps1 -FolderPath "C:\path\to\folder"
```

Output:

```text
C:\path\to\folder\output.mp4
```

Custom filename in the same source folder:

```powershell
.\concat.ps1 -FolderPath "C:\path\to\folder" -OutputFile "merged.mp4"
```

Output:

```text
C:\path\to\folder\merged.mp4
```

Explicit path elsewhere:

```powershell
.\concat.ps1 -FolderPath "C:\path\to\folder" -OutputFile "D:\videos\merged.mp4"
```

### Notes

- files are sorted alphabetically by filename
- matching is case-insensitive for `.mp4`
- excludes the output file if it is found in the same folder

---

## Example workflow

If your folder contains:

```text
clip-a.mp4
clip-b.mp4
clip-c.mp4
```

Running the script with only the folder argument will produce:

```text
output.mp4
```

inside that same folder, using alphabetical order:

1. `clip-a.mp4`
2. `clip-b.mp4`
3. `clip-c.mp4`

---

## Troubleshooting

### FFmpeg not found

Make sure `ffmpeg` is installed and available in your shell `PATH`.

### Output file accidentally included as input

This is already handled by the scripts, as long as the output is an `.mp4` inside the source folder and matches the computed output path.

### FFmpeg concat fails

If FFmpeg errors out while using `-c copy`, the inputs may not be compatible for stream-copy concatenation. In that case, use a re-encoding workflow instead.

## Additional notes

This project is a more updated and specialized approach, focusing only on MP4 files, than the one used in the project [playing-with-video](https://github.com/hrkns/playing-with-video). It's planned to join both projects at some point.
