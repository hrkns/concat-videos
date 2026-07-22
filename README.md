# Concatenate MP4 videos with FFmpeg

This project provides a desktop GUI and command-line scripts for the classic
FFmpeg concat workflow:

```bash
ffmpeg -f concat -safe 0 -i files.txt -c copy output.mp4
```

Instead of manually creating `files.txt`, it can:

- add every `.mp4` in a selected folder, sorted alphabetically
- accept repeated drag-and-drops of videos from different folders
- reorder and remove queued videos before concatenating
- generate the temporary concat file automatically
- call FFmpeg with `-c copy`

## Included scripts

- `concat_gui.py` — desktop interface
- `concat_core.py` — shared ordered-list and cancellation logic
- `concat.py`
- `concat.sh`
- `concat.ps1`

## Desktop GUI

### Requirements

- Python 3.10 or newer
- FFmpeg available in `PATH`
- FFprobe available in `PATH` for percentage and time-remaining estimates
  (it is normally installed together with FFmpeg)
- the Python GUI dependency from `requirements.txt`

Install the GUI dependency:

```bash
python -m pip install -r requirements.txt
```

Launch the application:

```bash
python concat_gui.py
```

In the application you can:

1. Select **Add Folder** to append that folder's immediate `.mp4` files in
   alphabetical order.
2. Select **Add Videos**, or drop MP4 files and folders onto the list. Every
   later drop is appended, so inputs can come from several locations.
3. Drag rows into a new order, or use **Move Up** and **Move Down**. Multiple
   selected rows can also be removed together. Duplicate entries are allowed
   intentionally when a clip should appear more than once.
4. Choose the output MP4 and select **Start Concatenation**.

While FFmpeg is running, the input and output controls are locked and **Cancel**
is enabled. The status bar displays elapsed wall-clock time, the completed
percentage, and an estimated time remaining based on the videos' total duration
and FFmpeg's current speed. If a duration cannot be read, concatenation still
works with an indeterminate progress indicator while elapsed time continues to
update.

When concatenation finishes successfully, the application plays the system
alert sound and shows a completion dialog with **Close**, **Open Folder**, and
**Play Video** actions. The last two use the operating system's default folder
viewer and video player.

Cancel first stops FFmpeg and then removes the per-job manifest and partial
output. Source videos are never cleanup targets. FFmpeg writes to a unique
staging MP4 beside the destination and promotes it only after success, so
cancellation or failure also preserves any pre-existing destination file.

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

- Python 3.10 or newer
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

If the destination already exists, the CLI asks before replacing it. For an
explicitly non-interactive overwrite, add `--overwrite` (or `-y`).

### Notes

- files are sorted alphabetically by filename
- matching is case-insensitive for `.mp4`
- the script prints the files it will concatenate before running FFmpeg
- a failed or cancelled run preserves any existing destination, just like the GUI

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

### The GUI reports that PySide6 is missing

Install the declared dependency and launch the GUI again:

```bash
python -m pip install -r requirements.txt
python concat_gui.py
```

## Tests

With `pytest` installed, run:

```bash
python -m pytest -q
```

## Additional notes

This project is a more updated and specialized approach, focusing only on MP4 files, than the one used in the project [playing-with-video](https://github.com/hrkns/playing-with-video). It's planned to join both projects at some point.
