"""
clear_progress.py — Archive the current implementation run and reset the workspace.

Usage:
    python clear_progress.py

Creates .implementations/implementation_N.zip from the contents of:
    plans/  .artifacts/  .archived_memory/  .archived_artifacts/

Then clears those directories so the next run starts fresh.
"""

import os
import glob
import shutil
import zipfile

DIRS_TO_ARCHIVE = [
    "./plans",
    "./.artifacts",
    "./.archived_memory",
    "./.archived_artifacts",
]
IMPLEMENTATIONS_DIR = "./.implementations"


def next_implementation_number(impl_dir: str) -> int:
    os.makedirs(impl_dir, exist_ok=True)
    existing = glob.glob(f"{impl_dir}/implementation_*.zip")
    if not existing:
        return 1
    numbers = []
    for path in existing:
        name = os.path.splitext(os.path.basename(path))[0]  # "implementation_3"
        try:
            numbers.append(int(name.split("_")[-1]))
        except ValueError:
            pass
    return max(numbers) + 1 if numbers else 1


def archive_and_clear():
    n = next_implementation_number(IMPLEMENTATIONS_DIR)
    zip_path = f"{IMPLEMENTATIONS_DIR}/implementation_{n}.zip"

    print(f"Archiving to {zip_path} ...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dir_path in DIRS_TO_ARCHIVE:
            if not os.path.exists(dir_path):
                continue
            for root, _, files in os.walk(dir_path):
                for file in files:
                    full_path = os.path.join(root, file)
                    arcname = os.path.relpath(full_path, start=".")
                    zf.write(full_path, arcname)

    print(f"Clearing source directories ...")
    for dir_path in DIRS_TO_ARCHIVE:
        if not os.path.exists(dir_path):
            continue
        for entry in os.listdir(dir_path):
            entry_path = os.path.join(dir_path, entry)
            if os.path.isfile(entry_path) or os.path.islink(entry_path):
                os.remove(entry_path)
            elif os.path.isdir(entry_path):
                shutil.rmtree(entry_path)

    print(f"Done. Implementation {n} archived to {zip_path}.")


if __name__ == "__main__":
    archive_and_clear()
