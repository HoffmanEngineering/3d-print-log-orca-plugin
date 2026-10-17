"""Checks a built wheel against what OrcaSlicer 2.5 requires to install and load it.

Mirrors read_wheel_plugin_metadata (src/slic3r/plugin/PluginFsUtils.cpp): one .dist-info with
METADATA (Name, Version), WHEEL (Wheel-Version, a *-none-any tag) and RECORD; the entry package
comes from Import-Name. Also checks the package carries its modules. Usage: check_wheel.py <whl>
"""

import sys
import zipfile

PACKAGE = "printlog_orca"
MODULES = [
    "__init__.py",
    "plugin.py",
    "page.py",
    "page_html.py",
    "claim_window.py",
    "plate.py",
    "script.py",
]


def check(path):
    problems = []
    with zipfile.ZipFile(path) as wheel:
        names = wheel.namelist()
        dist_infos = {n.split("/")[0] for n in names if n.split("/")[0].endswith(".dist-info")}
        if len(dist_infos) != 1:
            return [f"expected one .dist-info, found {sorted(dist_infos)}"]
        dist_info = dist_infos.pop()

        def text(name):
            try:
                return wheel.read(f"{dist_info}/{name}").decode("utf-8")
            except KeyError:
                problems.append(f"{dist_info}/{name} is missing")
                return ""

        headers = {}
        for line in text("METADATA").splitlines():
            if not line:
                break
            key, _, value = line.partition(":")
            headers.setdefault(key.strip().lower(), value.strip())
        for field in ("name", "version"):
            if not headers.get(field):
                problems.append(f"METADATA has no {field.title()}")
        import_name = headers.get("import-name")
        if import_name != PACKAGE:
            problems.append(f"METADATA Import-Name is {import_name!r}, not {PACKAGE}")
        if "requires-dist" in headers:
            problems.append("METADATA has Requires-Dist; the plugin must have no dependencies")

        wheel_file = text("WHEEL")
        if "Wheel-Version:" not in wheel_file:
            problems.append("WHEEL has no Wheel-Version")
        tags = [line[4:].strip() for line in wheel_file.splitlines() if line.startswith("Tag:")]
        if not tags or not all(tag.endswith("-none-any") for tag in tags):
            problems.append(f"WHEEL tags {tags} are not pure Python (*-none-any)")
        if not text("RECORD").strip():
            problems.append("RECORD is empty")

        for module in MODULES:
            if f"{PACKAGE}/{module}" not in names:
                problems.append(f"{PACKAGE}/{module} is missing")
    return problems


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    problems = check(argv[1])
    for problem in problems:
        print(f"FAIL  {problem}")
    if not problems:
        print(f"OK    {argv[1]}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
