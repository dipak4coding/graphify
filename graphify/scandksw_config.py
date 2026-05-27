"""
Auto-detection of ScanDKSW project configuration.

Replaces the manual Config.xml by discovering all paths from the
folder structure conventions used in VW/AUTOSAR embedded projects.

Convention (all paths relative to the project root):
    01_Buildprocess/make.bat           ← compiler + include paths (fixed folder name)
    {source_folder}/                   ← CWD where graphify is run (e.g. 03_SwFunktion)
    {source_folder}/components.{variant}.makelist  ← variant name auto-extracted
    99_Output/Target/dksw_{variant}.a2l            ← A2L file (fixed path)
    {source_folder}/graphify-out/scandksw/         ← output
    {source_folder}/graphify-out/Kommentare/       ← comments output

Usage:
    from graphify.scandksw_config import auto_detect, ScanDKSWConfig
    cfg = auto_detect(Path("."))
    print(cfg)          # shows what was found
    cfg.validate()      # raises if anything critical is missing
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ScanDKSWConfig:
    """
    All paths needed to run the signal flow extractor.
    All Path fields may be None if not found (check .validate() before use).
    """
    # Detected from folder structure
    project_root: Path          # parent of the source folder
    source_root: Path           # CWD — e.g. 03_SwFunktion/
    variant: str                # e.g. "dq381g4"  (from components.{variant}.makelist)

    # Input files
    components_makelist: Path | None   # components.dq381g4.makelist
    make_bat: Path | None              # 01_Buildprocess/make.bat
    a2l: Path | None                   # 99_Output/Target/dksw_dq381g4.a2l

    # Output folders (inside graphify-out/)
    scandksw_out: Path     # graphify-out/scandksw/
    kommentare_out: Path   # graphify-out/Kommentare/

    # Predefined compiler switches (loaded from make.bat PreDefines section)
    predefined: dict[str, str] = field(default_factory=dict)

    def validate(self, raise_on_missing: bool = True) -> list[str]:
        """
        Check that all critical paths exist.
        Returns list of warning strings. Raises ValueError if raise_on_missing=True.
        """
        warnings: list[str] = []

        def _check(p: Path | None, label: str) -> None:
            if p is None:
                warnings.append(f"NOT FOUND: {label}")
            elif not p.exists():
                warnings.append(f"PATH MISSING: {label} → {p}")

        _check(self.components_makelist, "components makelist")
        _check(self.make_bat, "make.bat (01_Buildprocess)")
        _check(self.a2l, f"A2L file (dksw_{self.variant}.a2l)")

        if warnings and raise_on_missing:
            raise ValueError("ScanDKSW auto-detection incomplete:\n" + "\n".join(f"  {w}" for w in warnings))
        return warnings

    def __str__(self) -> str:
        lines = [
            f"ScanDKSW config (variant: {self.variant})",
            f"  project root :  {self.project_root}",
            f"  source root  :  {self.source_root}",
            f"  makelist     :  {self.components_makelist}",
            f"  make.bat     :  {self.make_bat}",
            f"  A2L          :  {self.a2l}",
            f"  output       :  {self.scandksw_out}",
            f"  Kommentare   :  {self.kommentare_out}",
        ]
        if self.predefined:
            lines.append(f"  predefined   :  {len(self.predefined)} switches")
            for k, v in self.predefined.items():
                lines.append(f"    {k} = {v}")
        return "\n".join(lines)


def auto_detect(cwd: Path | None = None) -> ScanDKSWConfig:
    """
    Auto-detect all ScanDKSW paths from the project folder structure.

    Call from the source 'Quelle' folder (e.g. 03_SwFunktion/).
    All conventions are fixed by the project structure — no Config.xml needed.

    Parameters
    ----------
    cwd : Root directory to inspect. Defaults to Path(".") (current directory).

    Returns
    -------
    ScanDKSWConfig with all detected paths populated.
    Call .validate() to check for missing files before running.
    """
    source_root = Path(cwd or ".").resolve()
    project_root = source_root.parent   # one level up

    # ── Variant name ──────────────────────────────────────────────────────────
    # File: components.{variant}.makelist  (e.g. components.dq381g4.makelist)
    makelists = sorted(source_root.glob("components.*.makelist"))
    if makelists:
        components_makelist = makelists[0]
        # "components.dq381g4" → split on first dot after "components."
        stem = components_makelist.stem   # e.g. "components.dq381g4"
        variant = stem[len("components."):]  # e.g. "dq381g4"
    else:
        # Fallback: look for any *.makelist and use its stem
        fallback = sorted(source_root.glob("*.makelist"))
        if fallback:
            components_makelist = fallback[0]
            variant = components_makelist.stem
        else:
            components_makelist = None
            variant = "unknown"

    # ── 01_Buildprocess/make.bat ──────────────────────────────────────────────
    buildprocess_dir = project_root / "01_Buildprocess"
    make_bat = buildprocess_dir / "make.bat" if buildprocess_dir.exists() else None

    # ── A2L: 99_Output/Target/dksw_{variant}.a2l ─────────────────────────────
    a2l_path = project_root / "99_Output" / "Target" / f"dksw_{variant}.a2l"
    a2l = a2l_path if a2l_path.exists() else None

    # ── Output directories (inside graphify-out/) ─────────────────────────────
    graphify_out = source_root / "graphify-out"
    scandksw_out = graphify_out / "scandksw"
    kommentare_out = graphify_out / "Kommentare"

    # ── Extract predefined compiler switches from make.bat ────────────────────
    predefined: dict[str, str] = {}
    if make_bat and make_bat.exists():
        predefined = _parse_predefined_from_make_bat(make_bat)

    return ScanDKSWConfig(
        project_root=project_root,
        source_root=source_root,
        variant=variant,
        components_makelist=components_makelist,
        make_bat=make_bat,
        a2l=a2l,
        scandksw_out=scandksw_out,
        kommentare_out=kommentare_out,
        predefined=predefined,
    )


# ── make.bat parsing ──────────────────────────────────────────────────────────

# Pattern: -D NAME=VALUE  or  -D NAME  (compiler define flags in make.bat)
_DEFINE_FLAG_RE = re.compile(r"-D\s*([A-Z_][A-Z0-9_]*)(?:=([^\s\"\\]+))?")

# Pattern: lines that look like CS_MODUS_* assignments in project-specific bat files
# e.g.  SET CS_MODUS_DERIVAT=EN_MODUS_DERIVAT_DQ381G4
_SET_RE = re.compile(r"^\s*SET\s+([A-Z_][A-Z0-9_]*)=(.+)", re.IGNORECASE)


def _parse_predefined_from_make_bat(make_bat: Path) -> dict[str, str]:
    """
    Extract compiler switch definitions from make.bat.

    Looks for two patterns:
      1. -D NAME=VALUE  or  -D NAME    (GCC/CTC style compiler flags)
      2. SET NAME=VALUE                (batch variable assignments)

    Returns a dict of {name: value} where value may be empty string
    for boolean flags (defined but no value).
    """
    predefined: dict[str, str] = {}
    try:
        text = make_bat.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return predefined

    for line in text.splitlines():
        # Pattern 1: -D flags
        for m in _DEFINE_FLAG_RE.finditer(line):
            name = m.group(1)
            value = m.group(2) or ""
            if name not in predefined:
                predefined[name] = value

        # Pattern 2: SET NAME=VALUE (batch script assignments)
        m = _SET_RE.match(line)
        if m:
            name = m.group(1).strip()
            value = m.group(2).strip().strip('"')
            if name not in predefined:
                predefined[name] = value

    return predefined


# ── Group/makelist parsing ────────────────────────────────────────────────────

def read_group_list(components_makelist: Path) -> list[str]:
    """
    Parse the top-level components.{variant}.makelist.

    Returns a list of group paths, e.g.:
        ["krs\\krs", "svc\\svc", "gsi\\gsi", ...]

    The file format uses continuation lines ending in '\' and may have
    a 'COMPONENTS = ' header (same format as in ScanDKSW's Program.cs).
    """
    groups: list[str] = []
    try:
        text = components_makelist.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return groups

    for raw in text.splitlines():
        line = raw.strip()
        if line.endswith("\\"):
            line = line[:-1].strip()
        if line.upper().startswith("COMPONENTS"):
            line = line[10:].strip()
        if line.startswith("="):
            line = line[1:].strip()
        if line:
            groups.append(line)
    return groups


def read_group_c_files(source_root: Path, group_path: str) -> list[Path]:
    """
    For a group like "krs\\krs", find its per-group makelist and return
    all C files it declares.

    Looks for:  {source_root}/{group}/{group_name}.makelist
    where group_name = last segment of group_path.

    Returns a list of resolved Path objects for each C file.
    """
    parts = group_path.replace("\\", "/").split("/")
    group_name = parts[-1]

    group_dir = source_root
    for p in parts:
        group_dir = group_dir / p

    per_group_makelist = group_dir / f"{group_name}.makelist"
    if not per_group_makelist.exists():
        return []

    c_files: list[Path] = []
    try:
        text = per_group_makelist.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return c_files

    for raw in text.splitlines():
        line = raw.strip()
        if line.endswith("\\"):
            line = line[:-1].strip()
        if line.upper().startswith("SRC_FILES"):
            line = line[9:].strip()
        if line.startswith("="):
            line = line[1:].strip()

        for token in line.split():
            if token.endswith(".c") or token.endswith(".h"):
                candidate = group_dir / "src" / token
                if candidate.exists():
                    c_files.append(candidate)
    return c_files


def discover_all_c_files(cfg: ScanDKSWConfig) -> dict[str, list[Path]]:
    """
    Walk the components makelist → per-group makelists → C/H files.

    Returns a dict:
        {group_path: [Path, Path, ...]}
    e.g. {"krs\\krs": [Path(".../krs/src/krs_app.c"), ...]}
    """
    if not cfg.components_makelist or not cfg.components_makelist.exists():
        return {}

    result: dict[str, list[Path]] = {}
    groups = read_group_list(cfg.components_makelist)
    for group in groups:
        files = read_group_c_files(cfg.source_root, group)
        if files:
            result[group] = files
    return result
