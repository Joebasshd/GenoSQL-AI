import vcfpy
import gzip
import psycopg2
import os
import json
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(override=True)

BATCH_SIZE = 5000

def ingest_vcf(file_path):

    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASS"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT")
    )

    conn.autocommit = False
    cursor = conn.cursor()

    reader = vcfpy.Reader.from_stream(
        gzip.open(file_path, "rt")
    )

    total_seen = 0
    total_inserted = 0
    batch = []

    for record in reader:

        chrom = record.CHROM
        pos = record.POS
        variant_id = record.ID
        ref = record.REF
        alt = ",".join([str(a.value) for a in record.ALT])
        qual = record.QUAL
        filt = ",".join(record.FILTER) if record.FILTER else None
        info = record.INFO

        batch.append((
            chrom,
            pos,
            variant_id,
            ref,
            alt,
            qual,
            filt,
            json.dumps(info)
        ))

        total_seen += 1

        if len(batch) >= BATCH_SIZE:
            psycopg2.extras.execute_values(
                cursor,
                """
                INSERT INTO variants
                    (chrom, pos, variant_id, ref, alt, quality, filter, info)
                VALUES %s
                ON CONFLICT (chrom, pos, ref, alt) DO NOTHING
                """,
                batch,
                page_size=BATCH_SIZE
            )
            conn.commit()
            # rowcount is reliable here: execute_values with a single page
            # (page_size >= batch size) sets it correctly after INSERT
            inserted = cursor.rowcount
            total_inserted += inserted
            skipped = len(batch) - inserted
            print(f"Seen: {total_seen:,} | Inserted: {total_inserted:,} | Skipped (duplicates): {skipped:,}")
            batch.clear()

    # Flush remaining records
    if batch:
        psycopg2.extras.execute_values(
            cursor,
            """
            INSERT INTO variants
                (chrom, pos, variant_id, ref, alt, quality, filter, info)
            VALUES %s
            ON CONFLICT (chrom, pos, ref, alt) DO NOTHING
            """,
            batch,
            page_size=BATCH_SIZE
        )
        conn.commit()
        inserted = cursor.rowcount
        total_inserted += inserted
        skipped = len(batch) - inserted
        print(f"Seen: {total_seen:,} | Inserted: {total_inserted:,} | Skipped (duplicates): {skipped:,}")

    cursor.close()
    conn.close()

    print(f"\nVCF ingestion completed. Total seen: {total_seen:,} | Total inserted: {total_inserted:,}")
