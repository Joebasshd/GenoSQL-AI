"""
Deterministic answerability checks.

Small models can't be trusted to judge what a database can answer: offered a
refusal they over-refuse, and otherwise they answer with something adjacent
("What disease does this sample have?" -> ClinVar conditions of carried
variants, which reads like a diagnosis). So the decision is made here:

- concept gate (before the LLM): the question needs a kind of data this
  dataset doesn't hold (keyword rules, aware of what each dataset contains).
- scope classifier (before the LLM): a question is out of scope for genomic
  variant databases in general. Keyword lists can't enumerate this, so it's a
  small logistic regression on the question embedding (eval/train_scope.py).
- entity check (after SQL generation): the question names a specific
  identifier that the SQL ignores.
"""
import json
import re
from collections.abc import Callable
from pathlib import Path

import numpy as np

from app.nl_query.schema import SchemaView
from app.rag.embeddings import embed_text

MODEL_PATH = Path(__file__).resolve().parent / "scope_model.json"
_model = None


def _has_column(*names: str) -> Callable[[SchemaView], bool]:
    return lambda view: bool(set(names) & view.allowed_columns)


# (what is missing, pattern, does this dataset provide it?)
CONCEPTS: list[tuple[str, str, Callable[[SchemaView], bool]]] = [
    ("external frequency or disease databases such as gnomAD, TOPMed, UK Biobank, GTEx, COSMIC or OMIM",
     r"\b(gnomad|exac|topmed|uk ?biobank|gtex|cosmic|omim|hgmd|dbgap|all of us)\b", lambda v: False),
    ("gene expression data",
     r"\b(expression|expressed|rna-?seq|tpm|fpkm|eqtls?|transcript abundance)\b", lambda v: False),
    ("protein structure data",
     r"\b(alphafold|protein structure|crystal structure|protein folding|pdb)\b", lambda v: False),
    ("pathway or gene-function annotation",
     r"\b(pathways?|gene ontology|kegg|reactome|go terms?)\b", lambda v: False),
    ("drug, dosage or treatment information",
     r"\b(dosage|dosing|prescri\w+|medications?|treatments?|therap(y|ies|eutic))\b", lambda v: False),
    ("personal attributes such as age, height, weight or smoking status",
     r"\b(age|ages|how old|date of birth|bmi|body mass|height|weight|blood pressure|smok\w+|alcohol)\b", lambda v: False),
    ("clinical records, diagnoses or phenotypes of individuals",
     r"\b(diagnos\w+|symptoms?|prognosis|survival|mortality|life expectancy|phenotypes?)\b"
     r"|\bwhat (diseases?|conditions?|illness\w*|disorders?)\b[^?]*\b(sample|patient|individual|person|does|did|do)\b[^?]*\b(have|has|had|suffer\w*)\b"
     r"|\b(is|are) [^?]*\b(sick|healthy|ill|affected)\b", lambda v: False),
    ("who submitted ClinVar records",
     r"(?<!multiple )\b(submitters?|submitted|submitting lab\w*)\b", lambda v: False),
    ("ancestry or population labels",
     r"\b(ancestr\w+|ethnic\w*|races?|nationalit\w+|populations?|super-?populations?|continental"
     r"|africans?|europeans?|asians?|hispanics?|latin[oa]s?|admixed|yoruba|han chinese|finnish|british|japanese)\b",
     _has_column("population", "super_population", "afr_af")),
    ("the sex of individuals", r"\b(sex|gender|males?|females?|men|women|boys?|girls?)\b", _has_column("sex")),
]

ENTITY_PATTERNS = (
    r"\brs\d+\b",                 # dbSNP rsID
    r"\b[A-Z]{2}\d{5}\b",         # sample ids such as NA18609, HG00259
    r"\bENS[GTP]\d{6,}\b",        # Ensembl ids
)


def missing_concept(question: str, view: SchemaView) -> str | None:
    """What the question needs that this dataset lacks, or None if it looks answerable."""
    # ClinVar's own disease and condition fields are real data, so don't treat them as patient records.
    text = question.lower()
    available = view.categorical.get("chrom")
    if available:
        asked = {re.sub(r"^m$", "mt", c).upper() for c in re.findall(r"\bchr(?:omosome)?[ -]?(\d{1,2}|x|y|mt?)\b", text)}
        outside = sorted(asked - set(available))
        if outside and not asked & set(available):
            return (f"chromosome {', '.join(outside)}: it covers only chromosome "
                    f"{', '.join(sorted(available)[:10])}")
    for concept, pattern, provided in CONCEPTS:
        if re.search(pattern, text) and not provided(view):
            if "clinvar" in text and "clinical records" in concept and "clinvar" in view.allowed_tables:
                continue
            return concept
    return None


def out_of_scope(question: str) -> bool:
    """True if the classifier places the question outside what these databases can answer."""
    global _model
    if _model is None:
        if not MODEL_PATH.exists():
            return False
        _model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
        _model["weights"] = np.array(_model["weights"])
    score = float(_model["weights"] @ np.array(embed_text(question))) + _model["intercept"]
    return 1 / (1 + np.exp(-score)) > _model["threshold"]


def repair_hint(problem: str, sql: str) -> str:
    """Extra direction for failures that small models repeat when only shown the raw error."""
    if "variant_id" in problem and "clinvar" in sql.lower():
        return " clinvar has no variant_id: join variants ON chrom, pos, ref AND alt."
    return ""


def ignored_entity(question: str, sql: str) -> str | None:
    """An identifier named in the question that the SQL never uses."""
    for pattern in ENTITY_PATTERNS:
        for entity in re.findall(pattern, question):
            if entity.lower() not in sql.lower():
                return entity
    return None
