"""Make SHOBS-SCHED's release zip from the committed files: python tools/make_release_zip.py <out.zip> [ref]

Everything in the git tree at <ref> (default HEAD) goes in under a top folder "shobs_sched/", except what a user does
not need (the manual's sources, the build spec, these tools, git's own files). Unix permissions are kept, so the Mac
and Linux launchers stay executable after unzipping.
"""
from __future__ import annotations

import io
import subprocess
import sys
import tarfile
import time
import zipfile

LEAVE_OUT = ("docs/", "tools/", "SPEC.md", ".gitignore", ".gitattributes")


def main(out: str, ref: str = "HEAD") -> int:
    tar = subprocess.run(["git", "archive", "--format=tar", ref], check=True, capture_output=True).stdout
    n = 0
    with tarfile.open(fileobj=io.BytesIO(tar)) as t, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for m in t.getmembers():
            if not m.isfile() or m.name.startswith(LEAVE_OUT):
                continue
            info = zipfile.ZipInfo("shobs_sched/" + m.name, date_time=time.localtime(m.mtime)[:6])
            info.external_attr = (m.mode & 0o777 | 0o100000) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, t.extractfile(m).read())
            n += 1
    print(f"{out}: {n} files")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(*sys.argv[1:3]))
