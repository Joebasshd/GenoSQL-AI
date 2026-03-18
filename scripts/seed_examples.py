from app.rag.example_store import store_example

def seed():

    examples = [

        # Basic filtering
        (
            "show all variants on chromosome 22",
            "SELECT chrom, pos, ref, alt, qual FROM variants WHERE chrom = '22';"
        ),

        # Range queries
        (
            "variants between positions 16000000 and 16100000",
            "SELECT chrom, pos, ref, alt, qual FROM variants WHERE pos BETWEEN 16000000 AND 16100000;"
        ),

        # Combined filters
        (
            "variants on chromosome 22 between 16000000 and 16100000",
            "SELECT chrom, pos, ref, alt, qual FROM variants WHERE chrom = '22' AND pos BETWEEN 16000000 AND 16100000;"
        ),

        # Single position
        (
            "variants at position 16050075",
            "SELECT chrom, pos, ref, alt, qual FROM variants WHERE pos = 16050075;"
        ),

        # Quality filtering
        (
            "high quality variants",
            "SELECT chrom, pos, ref, alt, qual FROM variants WHERE qual > 30;"
        ),

        # Combined quality + chromosome
        (
            "high quality variants on chromosome 22",
            "SELECT chrom, pos, ref, alt, qual FROM variants WHERE chrom = '22' AND qual > 30;"
        ),

        # Limit query
        (
            "show a few variants on chromosome 22",
            "SELECT chrom, pos, ref, alt, qual FROM variants WHERE chrom = '22' LIMIT 10;"
        ),

    ]

    for q, sql in examples:
        store_example(q, sql)

    print("Seeded sql_examples table.")


if __name__ == "__main__":
    seed()