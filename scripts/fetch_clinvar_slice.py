"""
Download one chromosome of the ClinVar GRCh37 VCF without fetching the whole
file: read the tabix index (.tbi), then HTTP-range-request only the BGZF
blocks that hold that chromosome. (~2-5 MB for chr22 instead of ~200 MB.)

    python -m scripts.fetch_clinvar_slice            # chr22 → data/clinvar/clinvar_GRCh37_chr22.vcf.gz
    python -m scripts.fetch_clinvar_slice --chrom 17
"""
import argparse
import gzip
import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path

import httpx

URL = "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh37/clinvar.vcf.gz"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "clinvar"
PSEUDO_BIN = 37450
MAX_BLOCK = 65536


def fetch(client: httpx.Client, url: str, start: int | None = None, end: int | None = None) -> bytes:
    headers = {"Range": f"bytes={start}-{end}"} if start is not None else {}
    r = client.get(url, headers=headers, timeout=600)
    r.raise_for_status()
    if start is not None and r.status_code != 206:
        raise RuntimeError("Server ignored the Range header")
    return r.content


def chrom_span(tbi: bytes, chrom: str) -> tuple[int, int]:
    """Smallest/largest virtual offset of `chrom` in a tabix index."""
    data = gzip.decompress(tbi)
    if data[:4] != b"TBI\x01":
        raise ValueError("Not a tabix index")
    n_ref, _fmt, _cs, _cb, _ce, _meta, _skip, l_nm = struct.unpack_from("<8i", data, 4)
    names = data[36:36 + l_nm].split(b"\x00")[:n_ref]
    off = 36 + l_nm
    for name in names:
        (n_bin,) = struct.unpack_from("<i", data, off); off += 4
        lo, hi = None, None
        for _ in range(n_bin):
            bin_id, n_chunk = struct.unpack_from("<Ii", data, off); off += 8
            chunks = struct.unpack_from(f"<{2 * n_chunk}Q", data, off); off += 16 * n_chunk
            if bin_id == PSEUDO_BIN:
                continue  # holds counts, not real chunks
            for beg, end in zip(chunks[::2], chunks[1::2]):
                lo = beg if lo is None else min(lo, beg)
                hi = end if hi is None else max(hi, end)
        (n_intv,) = struct.unpack_from("<i", data, off); off += 4 + 8 * n_intv
        if name.decode() == chrom:
            return lo, hi
    raise KeyError(f"Chromosome {chrom} not in index ({[n.decode() for n in names]})")


def inflate_blocks(raw: bytes) -> list[tuple[int, bytes]]:
    """Decompress consecutive complete BGZF blocks → [(compressed_offset, data)]."""
    blocks, pos = [], 0
    while pos + 18 <= len(raw):
        if raw[pos:pos + 4] != b"\x1f\x8b\x08\x04":
            raise ValueError(f"Not a BGZF block at {pos}")
        bsize = struct.unpack_from("<H", raw, pos + 16)[0] + 1
        if pos + bsize > len(raw):
            break  # incomplete trailing block
        payload = raw[pos + 18: pos + bsize - 8]
        blocks.append((pos, zlib.decompress(payload, -15)))
        pos += bsize
    return blocks


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--chrom", default="22")
    args = p.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"clinvar_GRCh37_chr{args.chrom}.vcf.gz"

    with httpx.Client(follow_redirects=True) as client:
        head = client.head(URL, timeout=60)
        last_modified = head.headers.get("last-modified", "unknown")
        print(f"Source: {URL} (Last-Modified: {last_modified}, {int(head.headers['content-length']):,} bytes)")

        tbi = fetch(client, URL + ".tbi")
        lo, hi = chrom_span(tbi, args.chrom)
        c_lo, u_lo, c_hi, u_hi = lo >> 16, lo & 0xFFFF, hi >> 16, hi & 0xFFFF
        print(f"chr{args.chrom}: compressed bytes {c_lo:,}-{c_hi:,} (~{(c_hi - c_lo) / 1e6:.1f} MB)")

        header_blocks = inflate_blocks(fetch(client, URL, 0, 4 * MAX_BLOCK - 1))
        header_text = b"".join(d for _, d in header_blocks).decode()
        header = [l for l in header_text.split("\n") if l.startswith("#")]

        body = inflate_blocks(fetch(client, URL, c_lo, c_hi + MAX_BLOCK))

    # Stitch the uncompressed stream from voffset lo to voffset hi.
    parts = []
    for coff, data in body:
        abs_off = c_lo + coff
        start = u_lo if abs_off == c_lo else 0
        stop = u_hi if abs_off == c_hi else len(data)
        parts.append(data[start:stop])
        if abs_off == c_hi:
            break
    else:
        raise RuntimeError("Did not reach the end block — range too short")

    lines = [l for l in b"".join(parts).decode().split("\n") if l]
    bad = [l for l in lines if l.split("\t", 1)[0] != args.chrom]
    if bad:
        raise RuntimeError(f"{len(bad)} lines from other chromosomes, e.g. {bad[0][:60]}")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    header.insert(-1, f"##genosql_slice=chr{args.chrom} of {URL} (Last-Modified {last_modified}), fetched {stamp}")
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        fh.write("\n".join(header + lines) + "\n")
    print(f"Wrote {len(lines):,} records -> {out}")


if __name__ == "__main__":
    main()
