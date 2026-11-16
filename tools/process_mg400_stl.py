#!/usr/bin/env python3
"""Convert DOBOT MG400 ASCII STL → decimated binary STL for the web sim.

Usage:
  .venv/bin/python tools/process_mg400_stl.py
  .venv/bin/python tools/process_mg400_stl.py --src "C:/Users/.../DOBOT MG400.stl"
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = Path(r"/mnt/c/Users/kuk_g/Downloads/DOBOT MG400.stl")
DEFAULT_OUT = ROOT / "external/Agent-Robotic-Cluster/web/public/models/mg400/mg400_arm.stl"


def parse_ascii_stl(path: Path) -> tuple[np.ndarray, np.ndarray]:
    normals: list[tuple[float, float, float]] = []
    verts: list[list[tuple[float, float, float]]] = []
    with path.open("r", errors="ignore") as f:
        tri: list[tuple[float, float, float]] = []
        nrm: tuple[float, float, float] | None = None
        for line in f:
            s = line.strip()
            if s.startswith("facet normal"):
                parts = s.split()
                nrm = (float(parts[2]), float(parts[3]), float(parts[4]))
            elif s.startswith("vertex"):
                parts = s.split()
                tri.append((float(parts[1]), float(parts[2]), float(parts[3])))
                if len(tri) == 3:
                    normals.append(nrm if nrm else (0.0, 0.0, 0.0))
                    verts.append(tri)
                    tri = []
                    nrm = None
    return np.asarray(verts, dtype=np.float64), np.asarray(normals, dtype=np.float64)


def write_binary_stl(path: Path, V: np.ndarray, N: np.ndarray) -> None:
    header = b"MG400 decimated binary STL for ARC web sim".ljust(80, b"\0")[:80]
    tcount = int(V.shape[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(header)
        f.write(struct.pack("<I", tcount))
        for i in range(tcount):
            n = N[i]
            if not np.any(n):
                e1 = V[i, 1] - V[i, 0]
                e2 = V[i, 2] - V[i, 0]
                nn = np.cross(e1, e2)
                ln = float(np.linalg.norm(nn))
                n = nn / ln if ln > 1e-12 else np.array([0.0, 0.0, 1.0])
            f.write(struct.pack("<3f", float(n[0]), float(n[1]), float(n[2])))
            for j in range(3):
                f.write(
                    struct.pack(
                        "<3f",
                        float(V[i, j, 0]),
                        float(V[i, j, 1]),
                        float(V[i, j, 2]),
                    )
                )
            f.write(struct.pack("<H", 0))


def decimate(V: np.ndarray, target_tris: int = 40000) -> tuple[np.ndarray, np.ndarray]:
    """Vertex-cluster decimation (no extra deps)."""
    flat = V.reshape(-1, 3)
    mins = flat.min(axis=0)
    maxs = flat.max(axis=0)
    extent = float(np.max(maxs - mins))
    best: tuple[int, np.ndarray, np.ndarray, float] | None = None

    for cells in [60, 80, 100, 120, 150, 180, 220, 280]:
        cell = extent / cells if cells else extent
        if cell < 1e-9:
            cell = 1e-9
        q = np.floor((flat - mins) / cell).astype(np.int64)
        keys = q[:, 0] * (10**12) + q[:, 1] * (10**6) + q[:, 2]
        tcount = V.shape[0]
        tri_keys = keys.reshape(tcount, 3)
        keep = (
            (tri_keys[:, 0] != tri_keys[:, 1])
            & (tri_keys[:, 1] != tri_keys[:, 2])
            & (tri_keys[:, 0] != tri_keys[:, 2])
        )
        unique_keys, inv = np.unique(keys, return_inverse=True)
        sums = np.zeros((unique_keys.shape[0], 3), dtype=np.float64)
        counts = np.zeros(unique_keys.shape[0], dtype=np.int64)
        np.add.at(sums, inv, flat)
        np.add.at(counts, inv, 1)
        reps = sums / counts[:, None]
        inv_tri = inv.reshape(tcount, 3)[keep]
        a = np.sort(inv_tri, axis=1)
        packed = (
            a[:, 0].astype(np.int64) * (10**12)
            + a[:, 1].astype(np.int64) * (10**6)
            + a[:, 2].astype(np.int64)
        )
        _, uniq_idx = np.unique(packed, return_index=True)
        a = a[np.sort(uniq_idx)]
        ntri = int(a.shape[0])
        print(f"  cells={cells} cell={cell:.6f} tris={ntri}")
        best = (ntri, a, reps, cell)
        if ntri <= target_tris:
            break

    assert best is not None
    ntri, a, reps, cell = best
    V_out = reps[a]
    e1 = V_out[:, 1] - V_out[:, 0]
    e2 = V_out[:, 2] - V_out[:, 0]
    N_out = np.cross(e1, e2)
    ln = np.linalg.norm(N_out, axis=1, keepdims=True)
    ln = np.maximum(ln, 1e-12)
    N_out = N_out / ln
    print(f"decimated to {ntri} tris, cell={cell}")
    return V_out.astype(np.float32), N_out.astype(np.float32)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--src", type=Path, default=DEFAULT_SRC)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--target-tris", type=int, default=40000)
    args = p.parse_args(argv)

    src: Path = args.src
    if not src.exists():
        # Windows path via WSL
        alt = Path("/mnt/c/Users/kuk_g/Downloads/DOBOT MG400.stl")
        if alt.exists():
            src = alt
        else:
            print(f"Source not found: {args.src}", file=sys.stderr)
            return 1

    print("Reading", src, "size", src.stat().st_size)
    V, _N = parse_ascii_stl(src)
    print(f"triangles: {V.shape[0]}")
    print("Decimating...")
    Vd, Nd = decimate(V, target_tris=args.target_tris)
    print("Writing", args.out)
    write_binary_stl(args.out, Vd, Nd)
    print("out size MB", args.out.stat().st_size / 1024 / 1024)

    flat = Vd.reshape(-1, 3)
    mins = flat.min(0)
    maxs = flat.max(0)
    center = (mins + maxs) / 2
    size = maxs - mins
    readme = args.out.parent / "README.md"
    readme.write_text(
        f"""# MG400 CAD mesh (web sim)

Source: `{src}`

Processed by `tools/process_mg400_stl.py`:
- ASCII → binary STL
- vertex-cluster decimation → ~{Vd.shape[0]} triangles
- units: **meters** (bbox max dim ≈ {float(size.max()):.3f} m)

## Files
- `{args.out.name}` — single rigid mesh (whole arm at one pose).

## Limits
- Mesh is **one solid** (not split by joints) → cannot articulate J2/J3 links.
- Web `RobotArm` keeps procedural IK tip + held objects; CAD mesh yaws with the base.

## BBox (meters, CAD frame)
- min: {mins.tolist()}
- max: {maxs.tolist()}
- center: {center.tolist()}
- size: {size.tolist()}
""",
        encoding="utf-8",
    )
    print("Wrote", readme)
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
