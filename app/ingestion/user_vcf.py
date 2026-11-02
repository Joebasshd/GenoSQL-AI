"""
Load an arbitrary user VCF into its own schema ds_<id>.

Unlike the reference loader this keeps per-sample genotypes (carriers only,
with GQ/DP when the file has them), accepts any chromosome and either naming
style, and computes AC/AN/AF from genotypes when INFO doesn't provide them.
"""
import csv
import io
import json
import re
from dataclasses import dataclass, field

from app import config
from app.database import engine
from app.ingestion.vcf_parser import _open, classify, normalize, parse_info

GRCH37_LENGTHS = {"1": 249250621, "22": 51304566, "X": 155270560}
GRCH38_LENGTHS = {"1": 248956422, "22": 50818468, "X": 156040895}
PROMOTED_INFO = {"AC", "AN", "AF", "DP", "END", "SVTYPE"}
INFO_LINE = re.compile(r'##INFO=<ID=([^,]+),Number=([^,]+),Type=([^,]+),Description="([^"]*)"')
CONTIG_LINE = re.compile(r"##contig=<ID=([^,>]+)(?:,length=(\d+))?")
COPY_EVERY = 20_000


class VcfError(ValueError):
    """The upload is not a VCF we can load; the message is shown to the user."""


@dataclass
class VcfHeader:
    samples: list[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)         # id -> (number, description, type)
    contigs: dict = field(default_factory=dict)      # normalized name -> length
    reference: str = ""


@dataclass
class LoadResult:
    records: int = 0
    variants: int = 0
    genotypes: int = 0
    skipped: int = 0
    info_keys: set = field(default_factory=set)


def normalize_chrom(chrom: str) -> str:
    name = re.sub(r"^chr", "", chrom, flags=re.IGNORECASE)
    return "MT" if name.upper() in ("M", "MT") else name


def read_header(path: str) -> VcfHeader:
    header = VcfHeader()
    seen_fileformat = False
    with _open(path) as fh:
        for line in fh:
            if line.startswith("##fileformat=VCF"):
                seen_fileformat = True
            elif line.startswith("##reference="):
                header.reference = line.split("=", 1)[1].strip()
            elif line.startswith("##INFO="):
                m = INFO_LINE.match(line)
                if m:
                    header.info[m.group(1)] = (m.group(2), m.group(4), m.group(3))
            elif line.startswith("##contig="):
                m = CONTIG_LINE.match(line)
                if m:
                    header.contigs[normalize_chrom(m.group(1))] = int(m.group(2) or 0)
            elif line.startswith("#CHROM"):
                if not seen_fileformat:
                    break
                columns = line.rstrip("\n").split("\t")
                header.samples = columns[9:]
                return header
            elif not line.startswith("#"):
                break
    raise VcfError("This does not look like a VCF file (missing ##fileformat or #CHROM header).")


def detect_build(header: VcfHeader) -> str:
    for known, build in ((GRCH37_LENGTHS, "GRCh37"), (GRCH38_LENGTHS, "GRCh38")):
        if any(header.contigs.get(c) == n for c, n in known.items()):
            return build
    ref = header.reference.lower()
    if re.search(r"grch38|hg38|hs38", ref):
        return "GRCh38"
    if re.search(r"grch37|hg19|hs37|b37", ref):
        return "GRCh37"
    return "unknown"


def _number(value, kind):
    if isinstance(value, bool) or value is None:
        return None
    try:
        return kind(value)
    except (TypeError, ValueError):
        return None


def _pick(values, i):
    if not isinstance(values, list) or not values:
        return None
    return values[i] if i < len(values) else None


def parse_genotypes(fmt: str, columns: list[str], samples: list[str], n_alts: int):
    """Return (called_alleles, {alt_index: [(sample, copies, gt, gq, dp)]}) for one record."""
    keys = fmt.split(":")
    if "GT" not in keys:
        return 0, {}
    gt_i = keys.index("GT")
    gq_i = keys.index("GQ") if "GQ" in keys else None
    dp_i = keys.index("DP") if "DP" in keys else None
    called = 0
    carriers: dict[int, list] = {}
    for sample, column in zip(samples, columns):
        parts = column.split(":")
        if gt_i >= len(parts):
            continue
        gt = parts[gt_i]
        alleles = [a for a in gt.replace("|", "/").split("/") if a != "."]
        called += len(alleles)
        gq = parts[gq_i] if gq_i is not None and gq_i < len(parts) else None
        dp = parts[dp_i] if dp_i is not None and dp_i < len(parts) else None
        for k in {a for a in alleles if a != "0"}:
            if k.isdigit() and 1 <= int(k) <= n_alts:
                carriers.setdefault(int(k), []).append(
                    (sample, alleles.count(k), gt, _number(gq, int), _number(dp, int)))
    return called, carriers


def parse_record(line: str, header: VcfHeader) -> list[dict]:
    """One dict per ALT allele: {'row': [...variants columns...], 'carriers': [...]}."""
    f = line.rstrip("\n").split("\t")
    if len(f) < 8:
        raise VcfError(f"Malformed VCF line with {len(f)} columns: {line[:60]!r}")
    chrom, pos, vid, ref, alts, qual, filt, info_field = f[:8]
    if alts in (".", ""):
        return []
    alt_list = alts.split(",")
    info = parse_info(info_field)
    called, carriers = (parse_genotypes(f[8], f[9:], header.samples, len(alt_list))
                        if len(f) > 9 else (0, {}))
    sv_type = _pick(info.get("SVTYPE"), 0)
    end = _number(_pick(info.get("END"), 0), int)
    extra = {}
    for key, value in info.items():
        if key not in PROMOTED_INFO:
            extra[key] = value[0] if isinstance(value, list) and len(value) == 1 else value

    out = []
    for i, raw_alt in enumerate(alt_list):
        a_pos, a_ref, alt = normalize(int(pos), ref, raw_alt)
        allele_carriers = carriers.get(i + 1, [])
        ac = _number(_pick(info.get("AC"), i), int)
        an = _number(_pick(info.get("AN"), 0), int)
        af = _number(_pick(info.get("AF"), i), float)
        if header.samples:
            ac = sum(c[1] for c in allele_carriers) if ac is None else ac
            an = called if an is None else an
            af = round(ac / an, 6) if af is None and an else af
        row = [normalize_chrom(chrom), a_pos, end if end else a_pos + len(a_ref) - 1,
               None if vid == "." else vid, a_ref, alt, classify(a_ref, alt, sv_type), sv_type,
               len(alt_list) > 1, _number(qual, float), None if filt == "." else filt,
               ac, an, af, _number(_pick(info.get("DP"), 0), int),
               json.dumps(extra) if extra else None]
        out.append({"row": row, "carriers": allele_carriers})
    return out


VARIANT_COLUMNS = ("id, chrom, pos, end_pos, rsid, ref, alt, variant_type, sv_type, is_multiallelic, "
                   "quality, filter, ac, an, af, dp, info")
GENOTYPE_COLUMNS = "variant_id, sample_id, allele_count, gt, gq, dp"


def create_tables(cur, schema: str):
    cur.execute(f"""
        CREATE SCHEMA {schema};
        CREATE TABLE {schema}.variants (
            id BIGINT PRIMARY KEY, chrom TEXT NOT NULL, pos INTEGER NOT NULL, end_pos INTEGER, rsid TEXT,
            ref TEXT NOT NULL, alt TEXT NOT NULL, variant_type TEXT, sv_type TEXT,
            is_multiallelic BOOLEAN NOT NULL, quality REAL, filter TEXT,
            ac INTEGER, an INTEGER, af REAL, dp INTEGER, info JSONB);
        CREATE TABLE {schema}.samples (sample_id TEXT PRIMARY KEY);
        CREATE TABLE {schema}.genotypes (
            variant_id BIGINT NOT NULL, sample_id TEXT NOT NULL, allele_count SMALLINT NOT NULL,
            gt TEXT, gq INTEGER, dp INTEGER);
    """)


def _copy(cur, table: str, columns: str, buffer: io.StringIO):
    buffer.seek(0)
    cur.copy_expert(f"COPY {table} ({columns}) FROM STDIN WITH (FORMAT csv, NULL '')", buffer)
    buffer.seek(0)
    buffer.truncate()


def load_vcf(path: str, schema: str, header: VcfHeader) -> LoadResult:
    """Stream the file into schema (tables must exist), then index and analyze."""
    result = LoadResult()
    raw = engine.raw_connection()
    cur = raw.cursor()
    variants_buf, genotypes_buf = io.StringIO(), io.StringIO()
    variants_csv, genotypes_csv = csv.writer(variants_buf), csv.writer(genotypes_buf)
    next_id = 1
    try:
        for sample in header.samples:
            cur.execute(f"INSERT INTO {schema}.samples VALUES (%s) ON CONFLICT DO NOTHING", (sample,))
        with _open(path) as fh:
            for line in fh:
                if line.startswith("#"):
                    continue
                alleles = parse_record(line, header)
                result.records += 1
                if not alleles:
                    result.skipped += 1
                for allele in alleles:
                    variants_csv.writerow([next_id] + allele["row"])
                    for sample, copies, gt, gq, dp in allele["carriers"]:
                        genotypes_csv.writerow([next_id, sample, copies, gt, gq, dp])
                        result.genotypes += 1
                    if allele["row"][-1]:
                        result.info_keys.update(json.loads(allele["row"][-1]))
                    next_id += 1
                if result.genotypes > config.UPLOAD_MAX_GENOTYPES:
                    raise VcfError(f"Too many genotypes for this machine (limit "
                                   f"{config.UPLOAD_MAX_GENOTYPES:,}). Upload a smaller region or cohort.")
                if result.records % COPY_EVERY == 0:
                    _copy(cur, f"{schema}.variants", VARIANT_COLUMNS, variants_buf)
                    _copy(cur, f"{schema}.genotypes", GENOTYPE_COLUMNS, genotypes_buf)
                    raw.commit()
        _copy(cur, f"{schema}.variants", VARIANT_COLUMNS, variants_buf)
        _copy(cur, f"{schema}.genotypes", GENOTYPE_COLUMNS, genotypes_buf)
        cur.execute(f"""
            CREATE INDEX ON {schema}.variants (chrom, pos);
            CREATE INDEX ON {schema}.variants (rsid) WHERE rsid IS NOT NULL;
            CREATE INDEX ON {schema}.variants (af);
            CREATE INDEX ON {schema}.genotypes (variant_id);
            CREATE INDEX ON {schema}.genotypes (sample_id);
            ANALYZE {schema}.variants;
            ANALYZE {schema}.genotypes;
        """)
        raw.commit()
    finally:
        cur.close()
        raw.close()
    result.variants = next_id - 1
    return result
