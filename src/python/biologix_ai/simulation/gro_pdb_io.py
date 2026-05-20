#!/usr/bin/env python3
"""Minimal GRO <-> PDB for Packmol (atom order preserved)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple


def read_gro(path: str) -> Tuple[str, List[Tuple[int, str, str, float, float, float]]]:
    """Return title, list of (resnum, resname, atomname, x, y, z) in nm."""
    with open(path) as f:
        lines = f.readlines()
    title = lines[0].strip()
    n = int(lines[1].strip())
    atoms: List[Tuple[int, str, str, float, float, float]] = []
    for i in range(2, 2 + n):
        line = lines[i]
        if len(line) < 44:
            continue
        resnum = int(line[0:5])
        resname = line[5:10].strip()
        atomname = line[10:15].strip()
        x = float(line[20:28])
        y = float(line[28:36])
        z = float(line[36:44])
        atoms.append((resnum, resname, atomname, x, y, z))
    return title, atoms


def write_gro(path: str, title: str, atoms: List[Tuple], box: Tuple[float, float, float]) -> None:
    """Write GRO coordinates in nm."""
    n = len(atoms)
    lines = [title + "\n", str(n) + "\n"]
    for i, a in enumerate(atoms, start=1):
        resnum, resname, aname, x, y, z = a
        lines.append(
            f"{resnum % 100000:5d}{resname[:5]:>5}{aname[:5]:>5}{i % 100000:5d}"
            f"{x:8.3f}{y:8.3f}{z:8.3f}\n"
        )
    lines.append(f"{box[0]:10.5f}{box[1]:10.5f}{box[2]:10.5f}\n")
    with open(path, "w") as f:
        f.writelines(lines)


def gro_to_pdb(gro_path: str, pdb_path: str) -> None:
    """
    Write PDB (Angstrom) from GRO (nm). One ATOM per line; element from atom name.
    """
    lines = Path(gro_path).read_text().splitlines()
    n = int(lines[1].strip())
    atoms: List[Tuple[str, str, float, float, float]] = []
    for i in range(2, 2 + n):
        line = lines[i]
        if len(line) < 44:
            continue
        resname = line[5:10].strip()
        aname = line[10:15].strip()
        x = float(line[20:28]) * 10.0
        y = float(line[28:36]) * 10.0
        z = float(line[36:44]) * 10.0
        el = re.sub(r"[0-9]", "", aname)[:2]
        if len(el) == 2 and el[1].islower():
            pass
        else:
            el = el[0] if el else "C"
        atoms.append((resname, aname, x, y, z))

    out = [f"REMARK from {gro_path}\n"]
    for i, (res, aname, x, y, z) in enumerate(atoms, start=1):
        el = re.sub(r"[0-9]", "", aname)[0]
        out.append(
            f"ATOM  {i % 100000:5d} {aname[:4]:>4} {res[:3]:>3} A{i % 10000:4d}    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00          {el:>2}\n"
        )
    out.append("END\n")
    Path(pdb_path).parent.mkdir(parents=True, exist_ok=True)
    Path(pdb_path).write_text("".join(out))


def count_pdb_atoms(pdb_path: str) -> int:
    n = 0
    with open(pdb_path) as f:
        for line in f:
            if line.startswith(("ATOM  ", "HETATM")):
                n += 1
    return n


def read_pdb_coords_nm(pdb_path: str) -> List[Tuple[str, str, float, float, float]]:
    """Sequential ATOM/HETATM: (resname, atomname, x_nm, y_nm, z_nm)."""
    out: List[Tuple[str, str, float, float, float]] = []
    with open(pdb_path) as f:
        for line in f:
            if not line.startswith(("ATOM  ", "HETATM")):
                continue
            res = line[17:20].strip() or "UNK"
            aname = line[12:16].strip()
            x = float(line[30:38]) / 10.0
            y = float(line[38:46]) / 10.0
            z = float(line[46:54]) / 10.0
            out.append((res, aname, x, y, z))
    return out
